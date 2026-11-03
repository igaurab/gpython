"""The half of the variables panel and the debugger that runs inside the kernel process.

gpython reads this file and executes it in the kernel as the module `_gpython_debug` (see
`kernel._helpers`), so it uses the standard library only and never imports gpython (gpython
imports it for PRESETS and `resolve`). The tests import it directly and drive the debugger in
process. The record format, the presets and the caps are described in
docs/plans/10-variables-debugger.md.

Rules that hold in every preset: no hook is installed while nothing is being stepped, stopped
for or recorded; on Python 3.12+ the run-until modes use sys.monitoring LINE events for the
cells' own code only; history goes to gpython in batches at stops; and a tensor or array is
summarised by type, shape, dtype and device, never by repr(), .item(), .mean() or .tolist(),
unless it is watched (or `debug_stats=cpu` asks for stats of small CPU data).
"""

from __future__ import annotations

import ast
import itertools
import json
import linecache
import reprlib
import sys
import time
import types
from collections import deque

MARKER = "\x1egpython-debug\x1e"
BATCH = 300
KEYFRAME_EVERY = 100
MAP_ITEMS = 30
LOCALS_CAP = 60
TABLE_ROWS = 1000
TABLE_COLS = 50
TEXT_CAP = 200_000
OUTPUT_CAP = 4000
HUGE_LEN = 100_000
SLOW_REPR = 0.01
STATS_SIZE = 100_000
SKIP = [
    "IPython", "IPython.*", "ipykernel", "ipykernel.*", "asyncio", "asyncio.*", "tornado.*", "zmq.*",
    "traitlets.*", "jupyter_client.*", "concurrent.*", "contextlib", "threading", "selectors", "runpy",
    "codeop", "bdb", "pdb", "cmd", "_gpython_debug", "gpython.kernel_side",
]
HIDDEN = {"In", "Out", "get_ipython", "exit", "quit", "open"}
HEAVY = {"numpy", "pandas", "polars", "torch", "jax", "jaxlib", "tensorflow", "xarray", "scipy", "pyarrow", "cupy"}
ROUTINES = (types.FunctionType, types.BuiltinFunctionType, types.MethodType, types.BuiltinMethodType)
SCALARS = (int, float, complex, str, bytes, bool, type(None))

MB = 1024 * 1024
PRESETS = {
    "full": {
        "history": 200 * MB, "events": 2_000_000, "thinning": "off", "deltas": "off",
        "repr_chars": 2000, "strip_items": 500, "grid_size": 50, "stats": "watched",
    },
    "balanced": {
        "history": 20 * MB, "events": 200_000, "thinning": "on", "deltas": "on",
        "repr_chars": 80, "strip_items": 40, "grid_size": 20, "stats": "watched",
    },
    "lean": {
        "history": 5 * MB, "events": 50_000, "thinning": "aggressive", "deltas": "watched",
        "repr_chars": 40, "strip_items": 20, "grid_size": 10, "stats": "watched",
    },
}
DEFAULT_PRESET = "full"
DETAILS = (*PRESETS, "auto")
AUTO_STEPS = 10_000
AUTO_SWITCH = ("history", "events", "thinning", "deltas")
SETTINGS = ("history", "events", "thinning", "repr_chars", "strip_items", "grid_size", "stats")
THINNING = {"on": (3, 100, 1000), "aggressive": (1, 20, 200)}

cell_files: dict[str, str] = {}
_infos: dict[str, CellInfo | None] = {}
_state: dict = {"current": None, "last": None, "session": 0, "settings": {}}
_slow_types: set[type] = set()


def parse_bytes(text) -> int:
    """`20MB`, `1GB`, `512kb` or a plain number of bytes."""
    if isinstance(text, int):
        return text
    cleaned = str(text).strip().lower().replace(" ", "").rstrip("b")
    scale = {"k": 1024, "m": MB, "g": 1024 * MB}.get(cleaned[-1:], 1)
    number = cleaned[:-1] if scale != 1 else cleaned
    return int(float(number) * scale)


def _parse(key: str, value):
    if key == "history":
        return parse_bytes(value)
    if key in ("events", "repr_chars", "strip_items", "grid_size"):
        return max(1, int(value))
    if key == "thinning":
        value = {True: "on", False: "off"}.get(value, str(value).lower())
        if value not in ("on", "off", "aggressive"):
            raise ValueError("thinning is on, off or aggressive")
        return value
    if key == "stats":
        if value not in ("watched", "cpu", "off"):
            raise ValueError("stats is watched, cpu or off")
        return value
    raise KeyError(key)


def resolve(settings: dict) -> tuple[dict, dict]:
    """The limits in effect for config keys `debug_detail` and `debug_<limit>`, and where each came
    from: the preset's name, or "override". An unknown preset or a bad value falls back to the preset.

    `auto` starts as full; its limits carry `then`, the balanced storage limits (those not
    overridden) that the Recorder switches to once a recording passes AUTO_STEPS steps."""
    detail = str(settings.get("debug_detail", DEFAULT_PRESET)).lower()
    if detail not in DETAILS:
        detail = DEFAULT_PRESET
    limits = dict(PRESETS["full" if detail == "auto" else detail], detail=detail)
    sources = {key: detail for key in SETTINGS}
    for key in SETTINGS:
        value = settings.get(f"debug_{key}")
        if value is None or value == "":
            continue
        try:
            limits[key] = _parse(key, value)
            sources[key] = "override"
        except (ValueError, KeyError):
            continue
    if detail == "auto":
        limits["then"] = {key: PRESETS["balanced"][key] for key in AUTO_SWITCH if sources.get(key) != "override"}
    return limits, sources


def configure(settings: dict) -> None:
    """gpython sends the `debug_*` config keys at start and on :set; the next recording uses them."""
    _state["settings"] = dict(settings)


def limits() -> dict:
    return resolve(_state["settings"])[0]


class _Shaper(reprlib.Repr):
    """reprlib that never calls repr() on an array or tensor inside a container (a list of CUDA
    losses would sync the GPU once per element): those are shown by their metadata."""

    def repr1(self, x, level):
        if _heavy(x):
            return heavy_summary(x)
        return super().repr1(x, level)


def _repr(maxstring: int, maxother: int, items: int, level: int) -> reprlib.Repr:
    shaper = _Shaper()
    shaper.maxstring, shaper.maxother, shaper.maxlevel = maxstring, maxother, level
    shaper.maxlist = shaper.maxtuple = shaper.maxset = shaper.maxfrozenset = shaper.maxdeque = items
    shaper.maxdict = max(2, items // 2)
    shaper.maxarray = items
    return shaper


_TINY = _repr(14, 14, 3, 1)
_shapers: dict[int, reprlib.Repr] = {}


def _shaper(width: int) -> reprlib.Repr:
    shaper = _shapers.get(width)
    if shaper is None:
        items = max(4, min(200, width // 4))
        shaper = _shapers[width] = _repr(width, width, items, 2 if width < 200 else 4)
    return shaper


def _root(value) -> str:
    return type(value).__module__.partition(".")[0]


def _heavy(value) -> bool:
    """numpy, pandas, torch, jax and friends, told apart by the type's module: nothing is imported."""
    return _root(value) in HEAVY


def device_of(value) -> str:
    """`cpu`, `cuda:0`, …; numpy and pandas are always on the CPU; '' when unknown."""
    if _root(value) in ("numpy", "pandas", "polars", "pyarrow", "scipy", "xarray"):
        return "cpu"
    device = getattr(value, "device", None)
    if device is None or callable(device):
        return ""
    return str(device)


def on_cpu(value) -> bool:
    return device_of(value).startswith(("cpu", "TFRT_CPU"))


def _numel(value):
    count = getattr(value, "numel", None)
    if callable(count):
        try:
            return int(count())
        except Exception:
            return None
    size = getattr(value, "size", None)
    return size if isinstance(size, int) else None


def category(value) -> str:
    """module, class, function or data: the panel hides the first three unless asked."""
    if isinstance(value, types.ModuleType):
        return "module"
    if isinstance(value, type):
        return "class"
    if isinstance(value, ROUTINES):
        return "function"
    return "data"


def shape_info(value) -> str:
    """`(3, 4) float64` for arrays and frames (plus the device when not the CPU), `len 5` for
    sized containers and strings. Reads metadata only."""
    if _heavy(value):
        shape = getattr(value, "shape", None)
        if isinstance(shape, tuple):
            dtype = getattr(value, "dtype", None)
            if dtype is None and hasattr(value, "dtypes"):
                try:
                    kinds = sorted({str(d) for d in value.dtypes})
                except Exception:
                    kinds = []
                dtype = ", ".join(kinds[:3]) + (" …" if len(kinds) > 3 else "")
            text = f"{tuple(shape)} {dtype}".strip() if dtype is not None else str(tuple(shape))
            device = device_of(value)
            return text + (f" {device}" if device and not on_cpu(value) else "")
        return ""
    if isinstance(value, (type, types.ModuleType)) or isinstance(value, ROUTINES):
        return ""
    if isinstance(value, (str, bytes, list, tuple, dict, set, frozenset, range, deque)):
        return f"len {len(value)}"
    if hasattr(type(value), "__len__"):
        try:
            return f"len {len(value)}"
        except Exception:
            return ""
    return ""


def stats(value) -> str:
    """mean, min, max and NaN count of an array or tensor; each one is skipped when it fails."""
    parts = []
    for label in ("mean", "min", "max"):
        try:
            parts.append(f"{label}={float(getattr(value, label)()):.4g}")
        except Exception:
            if label == "mean" and hasattr(value, "float"):
                try:
                    parts.append(f"mean={float(value.float().mean()):.4g}")
                except Exception:
                    pass
    try:
        nan = int((value != value).sum())
        if nan:
            parts.append(f"nan={nan}")
    except Exception:
        pass
    return " ".join(parts)


def heavy_summary(value, stats_mode: str = "off", watched: bool = False) -> str:
    """`Tensor(3, 4) float32 cuda:0`: type, shape, dtype and device, read from metadata only.
    Stats are added for a watched name, or with stats_mode "cpu" for small data on the CPU."""
    name = type(value).__name__
    if hasattr(value, "columns") and hasattr(value, "iloc"):
        try:
            names = [str(c) for c in list(value.columns[:8])]
            return f"{name}{tuple(value.shape)} columns: " + ", ".join(names) + (" …" if len(value.columns) > 8 else "")
        except Exception:
            return name
    shape = getattr(value, "shape", None)
    if not isinstance(shape, tuple):
        return name
    dtype = getattr(value, "dtype", None)
    text = f"{name}{tuple(shape)}" + (f" {dtype}" if dtype is not None else "")
    device = device_of(value)
    if device and not on_cpu(value):
        text += f" {device}"
    count = _numel(value)
    small = count is not None and count <= STATS_SIZE
    if stats_mode != "off" and (watched or (stats_mode == "cpu" and small and on_cpu(value))):
        extra = stats(value)
        if extra:
            text += " " + extra
    return text


def short(value, width: int = 60, stats_mode: str = "off", watched: bool = False) -> str:
    """A one-line repr capped in length and time: heavy data by its metadata, an object whose len()
    is huge by its type and length, and a type whose repr was once slow by its name from then on."""
    kind = type(value)
    try:
        if _heavy(value):
            text = heavy_summary(value, stats_mode, watched)
        elif isinstance(value, types.ModuleType):
            text = ""
        elif kind in _slow_types:
            text = f"<{kind.__name__}: slow repr skipped>"
        elif not isinstance(value, (str, bytes, list, tuple, dict, set, frozenset, range, deque)) and hasattr(kind, "__len__") and _length(value) > HUGE_LEN:
            text = f"<{kind.__name__} of {_length(value)} items>"
        else:
            start = time.perf_counter()
            text = _shaper(width).repr(value)
            if time.perf_counter() - start > SLOW_REPR and not isinstance(value, SCALARS):
                _slow_types.add(kind)
    except Exception as error:
        text = f"<repr failed: {type(error).__name__}>"
    text = text.replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"


def _length(value) -> int:
    try:
        return len(value)
    except Exception:
        return 0


def cell_text(value, width: int = 6) -> str:
    """An element inside a strip or a grid box: numbers compact, strings bare. A 0-d array
    element is read with .item() only on the CPU."""
    if isinstance(value, bool) or value is None:
        text = str(value)
    elif isinstance(value, int):
        text = str(value)
    elif isinstance(value, float):
        text = format(value, ".4g")
    elif isinstance(value, str):
        text = value if len(value) <= 1 else repr(value)
    elif _root(value) == "numpy" and getattr(value, "ndim", 1) == 0 and hasattr(value, "item"):
        item = value.item()
        text = format(item, ".4g") if isinstance(item, float) else str(item)
    elif _heavy(value):
        text = type(value).__name__
    else:
        text = _TINY.repr(value)
    text = text.replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"


def describe(name: str, value, width: int = 60, stats_mode: str = "off") -> list:
    return [name, type(value).__name__, short(value, width, stats_mode), shape_info(value), category(value)]


def variables(namespace: dict, names) -> list:
    """Rows [name, type, value, shape/len/dtype, category] for the names that exist."""
    return [describe(n, namespace[n]) for n in sorted(names) if n in namespace]


def full(value) -> dict:
    """The whole value for the viewer, capped: a table for frames and 2-D data, else text. Data
    on a GPU is described, not copied."""
    try:
        if _heavy(value) and not on_cpu(value) and _root(value) not in ("pandas",):
            return {"kind": "text", "text": heavy_summary(value) + "\n\n(on " + (device_of(value) or "a device") + ": not copied to the CPU; watch it with w for its stats)"}
        table = _table(value)
        if table is not None:
            return table
        return {"kind": "text", "text": _full_text(value)}
    except Exception as error:
        return {"kind": "text", "text": f"<could not show the value: {error!r}>"}


def _table(value):
    if hasattr(value, "columns") and hasattr(value, "iloc") and len(getattr(value, "shape", ())) == 2:
        part = value.iloc[:TABLE_ROWS, :TABLE_COLS]
        rows = [[cell_text(x, 40) for x in row] for row in part.itertuples(index=False)]
        return {
            "kind": "table", "columns": [str(c) for c in part.columns], "index": [str(i) for i in part.index],
            "rows": rows, "total": list(value.shape),
        }
    if hasattr(value, "iloc") and hasattr(value, "index") and len(getattr(value, "shape", ())) == 1:
        part = value.iloc[:TABLE_ROWS]
        return {
            "kind": "table", "columns": [str(getattr(value, "name", None) or "value")],
            "index": [str(i) for i in part.index], "rows": [[cell_text(x, 40)] for x in part.tolist()],
            "total": [len(value), 1],
        }
    grid = _grid_shape(value)
    if grid is not None:
        rows, cols = grid
        if _heavy(value):
            shown = [[cell_text(x, 40) for x in row] for row in value[:TABLE_ROWS, :TABLE_COLS].tolist()]
        else:
            shown = [[cell_text(_at(value, r, c), 40) for c in range(min(cols, TABLE_COLS))] for r in range(min(rows, TABLE_ROWS))]
        return {
            "kind": "table", "columns": [str(c) for c in range(min(cols, TABLE_COLS))],
            "index": [str(r) for r in range(len(shown))], "rows": shown, "total": [rows, cols],
        }
    return None


def _full_text(value) -> str:
    import pprint

    if _root(value) == "numpy" and getattr(value, "ndim", None) is not None:
        numpy = sys.modules["numpy"]
        text = numpy.array2string(value, threshold=5000, max_line_width=100)
    elif isinstance(value, (list, tuple)) and len(value) > 2000:
        text = pprint.pformat(list(value[:2000]), width=100) + f"\n… {len(value) - 2000} more"
    elif isinstance(value, dict) and len(value) > 2000:
        text = pprint.pformat(dict(itertools.islice(value.items(), 2000)), width=100) + f"\n… {len(value) - 2000} more"
    elif isinstance(value, (set, frozenset)) and len(value) > 2000:
        text = pprint.pformat(list(itertools.islice(value, 2000)), width=100) + f"\n… {len(value) - 2000} more"
    elif isinstance(value, str):
        text = value
    else:
        text = pprint.pformat(value, width=100)
    return text if len(text) <= TEXT_CAP else text[:TEXT_CAP] + "\n… (cut)"


def _grid_shape(value):
    if _heavy(value) and getattr(value, "ndim", None) == 2:
        return tuple(value.shape)
    if isinstance(value, (list, tuple)) and value and len(value) <= 100_000:
        first = value[0]
        if isinstance(first, (list, tuple)) and not isinstance(first, str):
            return len(value), max((len(row) for row in itertools.islice(value, 200) if isinstance(row, (list, tuple))), default=0)
    return None


def _at(value, r: int, c: int):
    row = value[r]
    try:
        return row[c] if c < len(row) else ""
    except TypeError:
        return value[r, c]


class Loop:
    """One for or while statement of a cell: its lines, header text and target names."""

    __slots__ = ("line", "end", "kind", "header", "targets", "target", "iter", "scope")

    def __init__(self, node, scope: int) -> None:
        self.line, self.end, self.scope = node.lineno, node.end_lineno or node.lineno, scope
        if isinstance(node, ast.While):
            self.kind, self.header, self.targets, self.target, self.iter = "while", f"while {ast.unparse(node.test)}", [], "", None
        else:
            self.kind, self.target, self.iter = "for", ast.unparse(node.target), node.iter
            self.header = f"for {self.target} in {ast.unparse(node.iter)}"
            self.targets = [n.id for n in ast.walk(node.target) if isinstance(n, ast.Name)]


class CellInfo:
    """What a cell's AST tells the debugger: its loops, the names it uses, and the expressions
    that index each sequence (`nums[j + 1]` gives nums the pointer `j + 1`, `dp[i][j]` gives dp
    the cell (i, j))."""

    def __init__(self, source: str) -> None:
        self.loops: list[Loop] = []
        self.names: dict[str, None] = {}
        self.pointers: dict[str, list[str]] = {}
        self.grids: dict[str, list[tuple[str, str]]] = {}
        self.scopes: set[int] = {0}
        self.last_start = 0
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            return
        if tree.body:
            last = tree.body[-1]
            self.last_start = min([d.lineno for d in getattr(last, "decorator_list", [])] + [last.lineno])
        self._visit(tree, 0)

    def _visit(self, node, scope: int) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            decorators = [d.lineno for d in getattr(node, "decorator_list", [])]
            scope = min(decorators + [node.lineno])
            self.scopes.add(scope)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            self.loops.append(Loop(node, scope))
        elif isinstance(node, ast.Name):
            self.names.setdefault(node.id)
        elif isinstance(node, ast.Subscript):
            self._subscript(node)
        for child in ast.iter_child_nodes(node):
            self._visit(child, scope)

    def _subscript(self, node: ast.Subscript) -> None:
        base, index = node.value, node.slice
        if isinstance(base, ast.Subscript) and isinstance(base.value, ast.Name):
            if _pointer_like(base.slice) and _pointer_like(index):
                self._add_grid(base.value.id, base.slice, index)
            return
        if not isinstance(base, ast.Name):
            return
        if isinstance(index, ast.Tuple) and len(index.elts) == 2 and all(_pointer_like(e) for e in index.elts):
            self._add_grid(base.id, *index.elts)
        elif isinstance(index, ast.Slice):
            for part in (index.lower, index.upper):
                if part is not None and _pointer_like(part):
                    self._add_pointer(base.id, part)
        elif _pointer_like(index):
            self._add_pointer(base.id, index)

    def _add_pointer(self, name: str, node) -> None:
        text = ast.unparse(node)
        found = self.pointers.setdefault(name, [])
        if text not in found:
            found.append(text)

    def _add_grid(self, name: str, row, col) -> None:
        pair = (ast.unparse(row), ast.unparse(col))
        found = self.grids.setdefault(name, [])
        if pair not in found:
            found.append(pair)

    def scope_of(self, frame) -> int:
        code = frame.f_code
        if code.co_name == "<module>":
            return 0
        return code.co_firstlineno if code.co_firstlineno in self.scopes else -1

    def active_loops(self, frame, line: int) -> list[Loop]:
        """The loops of the frame's own code around `line`, outermost first."""
        scope = self.scope_of(frame)
        found = [loop for loop in self.loops if loop.scope == scope and loop.line <= line <= loop.end]
        found.sort(key=lambda loop: (loop.line, -loop.end))
        return found


def _pointer_like(node) -> bool:
    """An index made of names, small ints and arithmetic: `i`, `j + 1`, `len(xs) - k`."""
    named = False
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            named = named or sub.id != "len"
        elif isinstance(sub, ast.Call):
            if not (isinstance(sub.func, ast.Name) and sub.func.id == "len" and len(sub.args) == 1 and not sub.keywords):
                return False
        elif isinstance(sub, ast.Constant):
            if not isinstance(sub.value, int):
                return False
        elif not isinstance(sub, (ast.BinOp, ast.UnaryOp, ast.operator, ast.unaryop, ast.Load)):
            return False
    return named


def cell_info(filename: str) -> CellInfo | None:
    """The parsed cell behind a code filename, or None when the file is not a gpython cell."""
    if filename not in cell_files:
        return None
    info = _infos.get(filename)
    if info is None:
        info = _infos[filename] = CellInfo("".join(linecache.getlines(filename)))
    return info


_BINARY = {
    ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b,
    ast.FloorDiv: lambda a, b: a // b, ast.Mod: lambda a, b: a % b,
}
_parsed: dict[str, object] = {}


def safe_value(node, frame):
    """Evaluate a small expression (names, attributes, ints, + - * // %, len, range, enumerate,
    zip, reversed, .items/.keys/.values) without running arbitrary code."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        for space in (frame.f_locals, frame.f_globals, frame.f_builtins):
            if node.id in space:
                return space[node.id]
        raise LookupError(node.id)
    if isinstance(node, ast.Attribute) and not node.attr.startswith("_"):
        return getattr(safe_value(node.value, frame), node.attr)
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        return _BINARY[type(node.op)](safe_value(node.left, frame), safe_value(node.right, frame))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = safe_value(node.operand, frame)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.Call) and not node.keywords and isinstance(node.func, ast.Name) and node.func.id == "len":
        return len(safe_value(node.args[0], frame))
    raise ValueError("not a simple expression")


def pointer_value(text: str, frame):
    node = _parsed.get(text)
    if node is None:
        node = _parsed[text] = ast.parse(text, mode="eval").body
    value = safe_value(node, frame)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(text)
    return value


def _call_name(node) -> str:
    return node.func.id if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) else ""


def loop_total(loop: Loop, frame):
    """How many times a for loop runs, when that is cheap to know; None otherwise."""
    node = loop.iter
    if node is None:
        return None
    try:
        name = _call_name(node)
        if name == "range" and not node.keywords:
            return len(range(*[safe_value(a, frame) for a in node.args]))
        if name in ("enumerate", "reversed") and node.args:
            return len(safe_value(node.args[0], frame))
        if name == "zip" and node.args:
            return min(len(safe_value(a, frame)) for a in node.args)
        if (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("items", "keys", "values") and not node.args
        ):
            return len(safe_value(node.func.value, frame))
        if isinstance(node, (ast.Name, ast.Attribute)):
            value = safe_value(node, frame)
            if isinstance(value, (list, tuple, str, dict, set, frozenset, range, bytes, deque)) or (_heavy(value) and hasattr(value, "__len__")):
                return len(value)
    except Exception:
        return None
    return None


def derived_position(loop: Loop, frame):
    """The 1-based iteration a for loop is on, worked out from its target: range and enumerate only."""
    node = loop.iter
    try:
        name = _call_name(node)
        if name == "range" and loop.targets and not node.keywords:
            args = [safe_value(a, frame) for a in node.args]
            start, step = (0, 1) if len(args) == 1 else (args[0], args[2] if len(args) > 2 else 1)
            value = frame.f_locals[loop.targets[0]]
            if isinstance(value, int) and step and (value - start) % step == 0 and (value - start) // step >= 0:
                return (value - start) // step + 1
        if name == "enumerate" and loop.targets:
            start = safe_value(node.args[1], frame) if len(node.args) > 1 else 0
            for keyword in node.keywords:
                if keyword.arg == "start":
                    start = safe_value(keyword.value, frame)
            value = frame.f_locals[loop.targets[0]]
            if isinstance(value, int):
                return value - start + 1
    except Exception:
        return None
    return None



def _seq_kind(value) -> str:
    """seq, grid, map or '' for the views under the loop stack. An array or tensor on any device
    qualifies: unless it is watched only its shape is used."""
    if isinstance(value, (dict, set, frozenset)):
        return "map"
    if _heavy(value):
        if hasattr(value, "iloc") or _root(value) not in ("numpy", "torch", "jax", "jaxlib", "cupy"):
            return ""
        ndim = getattr(value, "ndim", None)
        if ndim == 1:
            return "seq"
        if ndim == 2:
            return "grid"
        return ""
    if isinstance(value, (list, tuple)):
        return "grid" if _grid_shape(value) is not None else "seq"
    if isinstance(value, (str, range)):
        return "seq"
    return ""


def _window(points: list[int], length: int, size: int) -> int:
    if length <= size:
        return 0
    inside = [p for p in points if 0 <= p <= length]
    if not inside:
        return 0
    low, high = min(inside), max(inside)
    start = max(0, min(low - 2, length - size))
    if high >= start + size:
        start = max(0, min(high - size + 2, length - size))
    return start


def _seq_view(name: str, value, pointers: list[list], size: int, watched: bool = False) -> dict:
    """A window of a sequence around its pointers. An array or tensor that is not watched is drawn
    from its length alone (its elements are never read); a watched one is read with one .tolist()."""
    length = len(value)
    start = _window([p for _, p in pointers], length, size)
    stop = min(length, start + size)
    if isinstance(value, str):
        items = list(value[start:stop])
    elif _heavy(value):
        items = [cell_text(x) for x in value[start:stop].tolist()] if watched else ["·"] * (stop - start)
    else:
        items = [cell_text(value[i]) for i in range(start, stop)]
    return {"name": name, "type": type(value).__name__, "len": length, "start": start, "items": items, "ptrs": pointers}


def _grid_view(name: str, value, pointers: list[list], size: int, watched: bool = False) -> dict:
    rows, cols = _grid_shape(value)
    row0 = _window([p[2] for p in pointers], rows, size)
    col0 = _window([p[3] for p in pointers], cols, size)
    if _heavy(value):
        part = value[row0:row0 + size, col0:col0 + size].tolist() if watched else None
        cells = [
            [cell_text(x, 5) for x in line] for line in part
        ] if part is not None else [["·"] * (min(cols, col0 + size) - col0) for _ in range(row0, min(rows, row0 + size))]
        return {"name": name, "rows": rows, "cols": cols, "row0": row0, "col0": col0, "cells": cells, "ptrs": pointers}
    cells = []
    for r in range(row0, min(rows, row0 + size)):
        line = []
        for c in range(col0, min(cols, col0 + size)):
            try:
                line.append(cell_text(_at(value, r, c), 5))
            except Exception:
                line.append("")
        cells.append(line)
    return {"name": name, "rows": rows, "cols": cols, "row0": row0, "col0": col0, "cells": cells, "ptrs": pointers}


def _map_view(name: str, value) -> dict:
    if isinstance(value, dict):
        items = [[cell_text(k, 14), cell_text(v, 14)] for k, v in itertools.islice(value.items(), MAP_ITEMS)]
    else:
        items = [[text, None] for text in sorted(cell_text(k, 14) for k in itertools.islice(value, MAP_ITEMS * 4))[:MAP_ITEMS]]
    return {"name": name, "kind": "dict" if isinstance(value, dict) else "set", "len": len(value), "items": items}


def frame_names(frame, info: CellInfo | None) -> list[str]:
    """The names worth showing for a frame: a cell's own names at module level, every local in a function."""
    space = frame.f_locals
    if frame.f_code.co_name == "<module>" and info is not None:
        names = [n for n in info.names if n in space]
    else:
        names = list(space)
    return [n for n in names if not n.startswith("_") and n not in HIDDEN][:LOCALS_CAP]


def snapshot(
    frame, line: int, positions: dict | None = None, lim: dict | None = None, watched=(), cache: dict | None = None,
    views: bool = True,
) -> dict:
    """Everything the panel draws for one moment: loops, sequences, grids, maps and locals.

    On a loop's header line the next iteration has not started (its variable still holds the
    last value), so the header shows the iteration that just ended: 0 before the first.
    `views` False keeps only the loops and the watched names (the lean preset). `cache` maps a
    name to (id, text) so an unchanged int or string is not summarised again."""
    lim = lim or PRESETS[DEFAULT_PRESET]
    width, stats_mode = lim["repr_chars"], lim["stats"]
    filename = frame.f_code.co_filename
    info = cell_info(filename)
    space = frame.f_locals
    data = {}
    for name in frame_names(frame, info):
        if not views and name not in watched:
            continue
        try:
            value = space[name]
        except KeyError:
            continue
        if category(value) == "data":
            data[name] = value
    loops = []
    seq_pointers: dict[str, list[list]] = {}
    if info is not None:
        for loop in info.active_loops(frame, line):
            k = (positions or {}).get(loop.line)
            if k is not None and loop.line == line:
                k -= 1
            elif k is None and loop.kind == "for":
                k = derived_position(loop, frame)
            values = [[t, short(space[t], 20)] for t in loop.targets if t in space]
            loops.append({"line": loop.line, "header": loop.header, "kind": loop.kind, "vars": values, "k": k, "n": loop_total(loop, frame) if loop.kind == "for" else None})
            node = loop.iter
            if _call_name(node) == "enumerate" and node.args:
                node = node.args[0]
            if isinstance(node, ast.Name) and k is not None and loop.targets:
                seq_pointers.setdefault(node.id, []).append([loop.targets[0], k - 1])
    seqs, grids, maps = [], [], []
    if views:
        for name, value in data.items():
            kind = _seq_kind(value)
            if not kind:
                continue
            try:
                if kind == "seq" and len(seqs) < 4:
                    pointers = _pointers(name, value, info, frame, seq_pointers.get(name, []), data, loops)
                    if pointers or isinstance(value, list) or _heavy(value):
                        seqs.append(_seq_view(name, value, pointers, lim["strip_items"], name in watched))
                elif kind == "grid" and len(grids) < 2:
                    grids.append(_grid_view(name, value, _grid_pointers(name, info, frame), lim["grid_size"], name in watched))
                elif kind == "map" and len(maps) < 3:
                    maps.append(_map_view(name, value))
            except Exception:
                continue
        seqs.sort(key=lambda s: not s["ptrs"])
    shown = []
    for name, value in data.items():
        if cache is not None and isinstance(value, SCALARS):
            known = cache.get(name)
            if known is not None and known[0] == id(value):
                shown.append([name, known[1]])
                continue
            text = short(value, width)
            cache[name] = (id(value), text)
        else:
            text = short(value, width, stats_mode, name in watched)
        shown.append([name, text])
    return {
        "cell": cell_files.get(filename), "file": filename, "line": line, "func": frame.f_code.co_name,
        "loops": loops, "seqs": seqs, "grids": grids, "maps": maps, "vars": shown,
    }


def _pointers(name: str, value, info, frame, extra: list[list], data: dict, loops: list[dict]) -> list[list]:
    found: dict[str, int] = {}
    for text in (info.pointers.get(name, []) if info is not None else []):
        try:
            found[text] = pointer_value(text, frame)
        except Exception:
            continue
    for label, position in extra:
        if label not in found:
            found[label] = position
    if not found:
        length = len(value)
        for loop in loops:
            for target, _ in loop["vars"]:
                candidate = data.get(target)
                if isinstance(candidate, int) and not isinstance(candidate, bool) and 0 <= candidate < length:
                    found[target] = candidate
    return [[label, position] for label, position in list(found.items())[:5]]


def _grid_pointers(name: str, info, frame) -> list[list]:
    found = []
    for row, col in (info.grids.get(name, []) if info is not None else []):
        try:
            found.append([row, col, pointer_value(row, frame), pointer_value(col, frame)])
        except Exception:
            continue
    return found[:3]


FIELDS = ("vars", "seqs", "grids", "maps")


def _size(entry: dict) -> int:
    return len(json.dumps(entry, separators=(",", ":"), default=str))


def _path(entry: dict) -> tuple:
    return tuple((loop["line"], loop.get("k")) for loop in entry.get("loops", []) if loop.get("k"))


def merge(later: dict, earlier: dict) -> dict:
    """`later` with the changes of the dropped `earlier` folded in, so the chain from the last
    keyframe still rebuilds the same state. A keyframe folded in makes the result a keyframe."""
    merged = dict(later)
    gone = set(later.get("gone", ()))
    present = set()
    for field in FIELDS:
        values = {name: value for name, value in earlier.get(field, {}).items() if name not in gone}
        values.update(later.get(field, {}))
        merged[field] = values
        present |= set(values)
    merged["gone"] = sorted((set(earlier.get("gone", ())) - present) | gone) if not (earlier.get("key") or later.get("key")) else []
    merged["key"] = bool(earlier.get("key") or later.get("key"))
    return merged


class Recorder:
    """The moments of one debugging session, oldest first, capped by bytes and by count.

    An entry is a keyframe (every name) or a delta (the names whose summary changed, and the
    names that went away), plus the line and the loop positions. With deltas on, a keyframe comes
    every KEYFRAME_EVERY entries; the full preset makes every entry a keyframe. When a cap is hit
    the oldest entries go first and the next one absorbs them (`merge`), so the oldest entry is
    always a keyframe. Thinning keeps every moment of the newest iterations of the innermost loop,
    one end-of-iteration moment of older ones, then every 10th, then every 100th iteration; it
    runs when the entries have doubled since the last pass, so its cost stays linear."""

    def __init__(self, lim: dict) -> None:
        self.limits = lim
        self.entries: deque = deque()
        self.sizes: deque = deque()
        self.bytes = 0
        self.count = 0
        self.dropped = 0
        self.thinned = 0
        self.state = {field: {} for field in FIELDS}
        self.since_key = KEYFRAME_EVERY
        self.sent = -1
        self.next_thin = 512
        self.last_path: tuple = ()

    def add(self, record: dict) -> dict:
        fields = {
            "vars": dict(record.get("vars", [])),
            "seqs": {view["name"]: view for view in record.get("seqs", [])},
            "grids": {view["name"]: view for view in record.get("grids", [])},
            "maps": {view["name"]: view for view in record.get("maps", [])},
        }
        entry = {key: record.get(key) for key in ("cell", "file", "line", "func", "loops")}
        entry["i"] = self.count
        key = self.limits["deltas"] == "off" or self.since_key >= KEYFRAME_EVERY
        if key:
            entry.update(fields, key=True, gone=[])
            self.since_key = 0
        else:
            entry["key"] = False
            for field in FIELDS:
                old = self.state[field]
                entry[field] = {name: value for name, value in fields[field].items() if old.get(name) != value}
            entry["gone"] = sorted({name for field in FIELDS for name in self.state[field] if name not in fields[field]})
            self.since_key += 1
        self.state = fields
        size = _size(entry)
        self.entries.append(entry)
        self.sizes.append(size)
        self.bytes += size
        self.count += 1
        if self.count >= AUTO_STEPS and "then" in self.limits:
            self._go_balanced()
        path = _path(entry)
        if path and path != self.last_path and self.limits["thinning"] != "off" and len(self.entries) >= self.next_thin:
            self.thin()
            self.next_thin = 2 * len(self.entries) + 512
        self.last_path = path
        self._cap()
        return entry

    def _go_balanced(self) -> None:
        """debug_detail=auto: the run is long, so keep changes only, thin and cap at the balanced size."""
        self.limits.update(self.limits.pop("then"))
        self.limits["detail"] = "auto → balanced"
        self.next_thin = 0

    def _cap(self) -> None:
        cap, events = self.limits["history"], self.limits["events"]
        while len(self.entries) > 1 and (self.bytes > cap or len(self.entries) > events):
            oldest = self.entries.popleft()
            self.bytes -= self.sizes.popleft()
            self.dropped += 1
            following = self.entries[0]
            if not following.get("key"):
                merged = merge(following, oldest)
                self.entries[0] = merged
                self.bytes -= self.sizes[0]
                self.sizes[0] = _size(merged)
                self.bytes += self.sizes[0]

    def thin(self) -> None:
        """Logarithmic thinning of old iterations (see the class docstring)."""
        keep_full, tens, hundreds = THINNING.get(self.limits["thinning"], THINNING["on"])
        groups: list[tuple[tuple, list[dict]]] = []
        for entry in self.entries:
            path = _path(entry)
            if groups and groups[-1][0] == path:
                groups[-1][1].append(entry)
            else:
                groups.append((path, [entry]))
        looped = [index for index, (path, _) in enumerate(groups) if path]
        age = {index: len(looped) - 1 - rank for rank, index in enumerate(looped)}
        kept: deque = deque()
        pending = None
        for index, (path, items) in enumerate(groups):
            years = age.get(index, -1)
            k = path[-1][1] if path else 0
            if not path or years < keep_full:
                keep = items
            elif (years >= hundreds and k % 100) or (years >= tens and k % 10):
                keep = []
                self.thinned += 1
            else:
                keep = items[-1:]
            kept_ids = {id(entry) for entry in keep}
            for entry in items:
                if id(entry) in kept_ids:
                    if pending is not None:
                        entry = merge(entry, pending)
                        pending = None
                    if path and years >= keep_full:
                        entry["thinned"] = True
                    kept.append(entry)
                else:
                    pending = entry if pending is None else merge(entry, pending)
        if pending is not None:
            kept.append(pending)
        self.entries = kept
        self.sizes = deque(_size(entry) for entry in kept)
        self.bytes = sum(self.sizes)

    def _extend_to_key(self, items: list[dict], before: list[dict]) -> list[dict]:
        while items and not items[0].get("key") and before:
            items.insert(0, before.pop())
        return items

    def fresh(self) -> dict:
        """The entries made since the last batch (the newest BATCH of them, from a keyframe)."""
        entries = list(self.entries)
        new = [entry for entry in entries if entry["i"] > self.sent]
        older = [entry for entry in entries if entry["i"] <= self.sent]
        if len(new) > BATCH:
            older += new[:-BATCH]
            new = self._extend_to_key(new[-BATCH:], older)
        if self.entries:
            self.sent = self.entries[-1]["i"]
        return self._batch(new, entries)

    def before(self, index: int, count: int = BATCH) -> dict:
        """Up to `count` entries older than `index`, starting at a keyframe."""
        entries = list(self.entries)
        older = [entry for entry in entries if entry["i"] < index]
        items = self._extend_to_key(older[-count:], older[:-count] if len(older) > count else [])
        return self._batch(items, entries)

    @staticmethod
    def _batch(items: list[dict], entries: list[dict]) -> dict:
        if not items:
            return {"prev": None, "records": []}
        position = next(n for n, entry in enumerate(entries) if entry["i"] == items[0]["i"])
        return {"prev": entries[position - 1]["i"] if position else None, "records": items}

    def summary(self) -> dict:
        return {
            "detail": self.limits.get("detail", DEFAULT_PRESET), "steps": len(self.entries), "bytes": self.bytes,
            "cap": self.limits["history"], "dropped": self.dropped, "thinned": self.thinned,
            "first": self.entries[0]["i"] if self.entries else 0, "total": self.count,
        }


class LineHook:
    """sys.monitoring (Python 3.12+) LINE and RAISE events for the run-until modes: LINE reaches the
    debugger for the cells' own code only, every other line is disabled after its first event."""

    def __init__(self, debugger) -> None:
        self.debugger = debugger
        self.tool: int | None = None

    @staticmethod
    def available() -> bool:
        return hasattr(sys, "monitoring")

    def start(self) -> bool:
        monitoring = sys.monitoring
        tool = next((t for t in (4, 3, 5) if monitoring.get_tool(t) is None), None)
        if tool is None:
            return False
        monitoring.use_tool_id(tool, "gpython-debug")
        self.tool = tool
        events = monitoring.events
        monitoring.register_callback(tool, events.LINE, self._line)
        monitoring.register_callback(tool, events.RAISE, self._raise)
        monitoring.register_callback(tool, events.PY_RETURN, self._return)
        monitoring.set_events(tool, events.LINE | events.RAISE | events.PY_RETURN)
        return True

    def stop(self) -> None:
        if self.tool is None:
            return
        monitoring, tool = sys.monitoring, self.tool
        self.tool = None
        monitoring.set_events(tool, 0)
        monitoring.register_callback(tool, monitoring.events.LINE, None)
        monitoring.register_callback(tool, monitoring.events.RAISE, None)
        monitoring.register_callback(tool, monitoring.events.PY_RETURN, None)
        monitoring.free_tool_id(tool)
        monitoring.restart_events()

    def _line(self, code, line):
        if code.co_filename not in cell_files:
            return sys.monitoring.DISABLE
        self.debugger.hooked_line(sys._getframe(1), line)
        return None

    def _raise(self, code, offset, exception):
        if code.co_filename in cell_files:
            self.debugger.hooked_raise(sys._getframe(1), exception)

    def _return(self, code, offset, value):
        if code.co_filename not in cell_files:
            return sys.monitoring.DISABLE
        self.debugger.hooked_return(sys._getframe(1))
        return None


def _make_debugger_class():
    """The pdb.Pdb subclass, built on the first breakpoint so a kernel that never debugs pays nothing.

    ipykernel puts IPython's own debugger in `pdb.Pdb`; the standard library's class is the one
    in its MRO whose module is `pdb`."""
    import bdb
    import io
    import pdb

    standard = next(c for c in pdb.Pdb.__mro__ if c.__module__ == "pdb")

    class Debugger(standard):
        """pdb that answers in JSON: every prompt carries the stop's state after MARKER, and gpython
        draws it in the variables panel. Commands come back as pdb commands plus the `_gp_*` ones.

        n, s and r step with pdb's own sys.settrace machinery and record every line of the cells'
        frames. The run-until modes (`_gp_next`, `_gp_end`, `_gp_goto`, `_gp_until`) drop the trace
        and use LineHook on Python 3.12+, so only the cells' lines cost anything; older Pythons
        keep the trace and check the goal in dispatch_line."""

        def __init__(self) -> None:
            self.buffer = io.StringIO()
            super().__init__(stdout=self.buffer, skip=SKIP, nosigint=True, readrc=False)
            self.use_rawinput = 1
            _state["session"] += 1
            self.session = _state["session"]
            self.limits = limits()
            self.recorder = Recorder(self.limits)
            self.recording = True
            self.hook = LineHook(self) if LineHook.available() else None
            self.batches: list[dict] = []
            self.counts: dict[int, dict[int, int | None]] = {}
            self.last_lines: dict[int, int] = {}
            self.summaries: dict[str, tuple] = {}
            self.goal = None
            self.goal_frame = None
            self.watches: list[str] = []
            self.reply = None
            self.event = "line"
            self.postmortem = False
            self.exception = ""
            self.cell = None
            self.stop_index = None
            self.pending = None
            self._plain_prompt = "(Pdb) "

        def _get_prompt(self) -> str:
            if self.curframe is None:
                return self._plain_prompt
            try:
                return MARKER + json.dumps(self.payload(), default=str)
            except Exception as error:
                return MARKER + json.dumps({"session": self.session, "error": repr(error)})

        def _set_prompt(self, value: str) -> None:
            self._plain_prompt = value

        prompt = property(_get_prompt, _set_prompt)

        def is_cell(self, frame) -> bool:
            return frame is not None and frame.f_code.co_filename in cell_files

        def outermost(self, frame) -> bool:
            caller = frame.f_back
            while caller is not None:
                if self.is_cell(caller):
                    return False
                caller = caller.f_back
            return True

        def ends_run(self, frame) -> bool:
            """Nothing of the cells runs after this frame returns: it is the outermost cell frame and
            either a function or the cell's last top-level statement (IPython runs each statement as
            its own code object, so another statement's first line would come next)."""
            if not self.is_cell(frame) or not self.outermost(frame):
                return False
            if frame.f_code.co_name != "<module>":
                return True
            info = cell_info(frame.f_code.co_filename)
            return info is None or frame.f_lineno >= info.last_start

        @property
        def watched(self) -> set[str]:
            return {expr for expr in self.watches if expr.isidentifier()}

        def observe(self, frame, line: int | None = None) -> None:
            """A line event in a cell's frame: count loop iterations, and record the moment."""
            info = cell_info(frame.f_code.co_filename)
            if info is None:
                return
            key = id(frame)
            line = frame.f_lineno if line is None else line
            previous = self.last_lines.get(key)
            self.last_lines[key] = line
            counts = self.counts.setdefault(key, {})
            for loop in info.loops:
                if loop.line != line:
                    continue
                if previous is None or not loop.line <= previous <= loop.end:
                    counts[loop.line] = 1
                elif counts.get(loop.line) is not None:
                    counts[loop.line] += 1
                else:
                    known = derived_position(loop, frame) if loop.kind == "for" else None
                    counts[loop.line] = known + 1 if known else None
            if self.cell is None:
                self.cell = cell_files.get(frame.f_code.co_filename)
            if self.recording:
                self.pending = (frame, line, self.record(frame, line))

        def record(self, frame, line: int) -> dict:
            views = self.limits["deltas"] != "watched"
            moment = snapshot(frame, line, self.counts.get(id(frame)), self.limits, self.watched, self.summaries, views)
            return self.recorder.add(moment)

        def goal_met(self, frame, previous, line: int | None = None) -> bool:
            goal = self.goal
            line = frame.f_lineno if line is None else line
            kind = goal[0]
            if kind == "cond":
                try:
                    return bool(eval(goal[1], frame.f_globals, frame.f_locals))
                except Exception:
                    return False
            if frame is not self.goal_frame:
                caller = frame.f_back
                while caller is not None and caller is not self.goal_frame:
                    caller = caller.f_back
                return caller is None
            loop_line, loop_end = goal[1], goal[2]
            if kind == "end":
                return not loop_line <= line <= loop_end
            if not loop_line <= line <= loop_end:
                return True
            if previous != loop_line or line == loop_line:
                return False
            if kind == "iter":
                return True
            reached = self.counts.get(id(frame), {}).get(loop_line)
            return reached is not None and reached >= goal[3]

        def hooked_line(self, frame, line: int) -> None:
            """LineHook: a line of a cell while running to a goal."""
            if self.goal is None:
                return
            previous = self.last_lines.get(id(frame))
            self.observe(frame, line)
            if self.goal_met(frame, previous, line) or (self.breaks and self.break_here(frame)):
                self._arrive(frame, None)

        def hooked_return(self, frame) -> None:
            """LineHook: the frame a loop goal was set in returns before the goal was met (the loop
            was the cell's last statement, say): pause on its way out, as pdb's r does."""
            if self.goal is None or frame is not self.goal_frame or not self.ends_run(frame):
                return
            self.event = "return"
            self._arrive(frame, None, "return")

        def hooked_raise(self, frame, exception) -> None:
            if self.goal is None:
                return
            self.exception = f"{type(exception).__name__}: {exception}"
            self._arrive(frame, exception)

        def _arrive(self, frame, exception, event: str = "line") -> None:
            """Leave the run-until mode: stop the hook, put pdb's trace back and pause here."""
            self.goal = self.goal_frame = None
            self.recording = True
            if self.hook is not None:
                self.hook.stop()
            walk = frame
            while walk is not None:
                walk.f_trace = self.trace_dispatch
                walk.f_trace_lines = True
                if walk is self.botframe:
                    break
                walk = walk.f_back
            self.set_step()
            sys.settrace(self.trace_dispatch)
            if exception is None:
                self.event = event
                self.interaction(frame, None)
            else:
                self.event = "exception"
                frame.f_locals["__exception__"] = type(exception), exception
                self.interaction(frame, exception.__traceback__)
            if self.quitting:
                raise bdb.BdbQuit

        def dispatch_line(self, frame):
            if self.is_cell(frame):
                previous = self.last_lines.get(id(frame))
                self.observe(frame)
                if self.goal is not None and (self.goal_met(frame, previous) or (self.breaks and self.break_here(frame))):
                    self.goal = self.goal_frame = None
                    self.recording = True
                    self.set_step()
                    self.user_line(frame)
                    if self.quitting:
                        raise bdb.BdbQuit
                    return self.trace_dispatch
                if self.goal is not None:
                    return self.trace_dispatch
            return super().dispatch_line(frame)

        def dispatch_call(self, frame, arg):
            if self.goal is not None and self.is_cell(frame):
                return self.trace_dispatch
            return super().dispatch_call(frame, arg)

        def dispatch_return(self, frame, arg):
            if self.goal is not None and frame is self.goal_frame:
                self.goal = self.goal_frame = None
                self.recording = True
                self.set_step()
                if self.ends_run(frame):
                    self.event = "return"
                    self.interaction(frame, None)
                    if self.quitting:
                        raise bdb.BdbQuit
            try:
                return super().dispatch_return(frame, arg)
            finally:
                self.last_lines.pop(id(frame), None)
                self.counts.pop(id(frame), None)

        def dispatch_exception(self, frame, arg):
            if self.goal is not None and self.is_cell(frame) and not self.stop_here(frame):
                self.goal = self.goal_frame = None
                self.recording = True
                self.set_step()
                self.user_exception(frame, arg)
                if self.quitting:
                    raise bdb.BdbQuit
                return self.trace_dispatch
            return super().dispatch_exception(frame, arg)

        def stop_here(self, frame):
            if not self.is_cell(frame) and self.outermost(frame):
                return False
            return super().stop_here(frame)

        def user_call(self, frame, argument_list):
            return

        def user_line(self, frame):
            self.event = "line"
            super().user_line(frame)

        def user_return(self, frame, return_value):
            if self.is_cell(frame) and self.outermost(frame):
                if self.goal is None:
                    self.set_step()
                return
            self.event = "return"
            super().user_return(frame, return_value)

        def user_exception(self, frame, exc_info):
            self.event = "exception"
            self.exception = f"{exc_info[0].__name__}: {exc_info[1]}"
            super().user_exception(frame, exc_info)

        def interaction(self, frame, tb):
            if frame is not None and self.is_cell(frame):
                line = frame.f_lineno
                self.last_lines.setdefault(id(frame), line)
                pending = self.pending
                if pending is not None and pending[0] is frame and pending[1] == line:
                    self.stop_index = pending[2]["i"]
                else:
                    self.stop_index = self.record(frame, line)["i"]
            elif frame is not None and not self.postmortem and self.recording:
                self.stop_index = self.record(frame, frame.f_lineno)["i"]
            else:
                self.stop_index = None
            self.pending = None
            super().interaction(frame, tb)

        def _cmdloop(self):
            try:
                self.cmdloop()
            except KeyboardInterrupt:
                self.set_quit()
                raise

        def set_quit(self):
            if self.hook is not None:
                self.hook.stop()
            super().set_quit()

        def user_frames(self) -> list[int]:
            """Indexes into self.stack of the frames worth showing: from the first cell frame on."""
            shown = [i for i, (f, _) in enumerate(self.stack) if not self.is_skipped_module(f.f_globals.get("__name__"))]
            cells = [i for i in shown if self.is_cell(self.stack[i][0])]
            return [i for i in shown if not cells or i >= cells[0]]

        def payload(self) -> dict:
            frame, line = self.stack[self.curindex]
            info = cell_info(frame.f_code.co_filename)
            frames = self.user_frames()
            lim = self.limits
            now = snapshot(frame, line, self.counts.get(id(frame)), lim, self.watched)
            live = self.curindex == len(self.stack) - 1 and not self.postmortem
            now["i"] = self.stop_index if live else None
            space = frame.f_locals
            names = [n for n in frame_names(frame, info) if n in space]
            output = "\n".join(
                text for text in self.buffer.getvalue().splitlines()
                if not text.startswith(("> ", "-> ", "--Return--", "--Call--"))
            )
            self.buffer.seek(0)
            self.buffer.truncate()
            reply, self.reply = self.reply, None
            batches = self.batches + [self.recorder.fresh()]
            self.batches = []
            history = self.recorder.summary()
            history["batches"] = batches
            return {
                "session": self.session, "event": self.event, "postmortem": self.postmortem, "exception": self.exception,
                "now": now, "index": now.get("i"),
                "stack": [
                    {"file": f.f_code.co_filename, "line": n, "func": f.f_code.co_name, "cell": cell_files.get(f.f_code.co_filename)}
                    for f, n in (self.stack[i] for i in frames)
                ],
                "frame": frames.index(self.curindex) if self.curindex in frames else len(frames) - 1,
                "locals": [describe(n, space[n], min(lim["repr_chars"], 300), lim["stats"]) for n in names],
                "watches": [[expr, self._watch(expr, frame)] for expr in self.watches],
                "reply": reply, "output": output[-OUTPUT_CAP:], "history": history,
                "breaks": {
                    cell_files[name]: sorted(set(lines)) for name, lines in self.breaks.items() if name in cell_files and lines
                },
            }

        def _watch(self, expr: str, frame) -> str:
            try:
                value = eval(expr, frame.f_globals, self.curframe_locals)
            except Exception as error:
                return f"error: {type(error).__name__}"
            return short(value, 120, self.limits["stats"], watched=True)

        def _loops_here(self):
            frame, line = self.stack[self.curindex]
            info = cell_info(frame.f_code.co_filename)
            return frame, (info.active_loops(frame, line) if info is not None else [])

        def _run_to(self, goal, frame, record: bool = True) -> int:
            """Run until `goal`; with the hook (3.12+) pdb's trace is removed until then."""
            self.goal, self.goal_frame = goal, frame
            self.recording = record
            if self.hook is not None and self.hook.start():
                self.set_continue()
                self._untrace()
                self.goal, self.goal_frame = goal, frame
            else:
                self._set_stopinfo(self.botframe, None, -1)
            return 1

        def _untrace(self) -> None:
            """Take pdb's trace off every frame, as set_continue does when there are no breakpoints:
            with breakpoints set it keeps the trace, and the hook would see each line twice. The
            hook checks the breakpoints itself."""
            sys.settrace(None)
            frame = sys._getframe().f_back
            while frame is not None and frame is not self.botframe:
                frame.f_trace = None
                frame = frame.f_back
            saved = getattr(self, "frame_trace_lines_opcodes", None)
            if saved:
                for frame, (trace_lines, trace_opcodes) in saved.items():
                    frame.f_trace_lines, frame.f_trace_opcodes = trace_lines, trace_opcodes
                self.frame_trace_lines_opcodes = {}

        def _refuse(self, text: str) -> int:
            self.reply = {"kind": "error", "text": text}
            return 0

        def do__gp_next(self, arg):
            """_gp_next N: run until the loop N levels out from the innermost starts its next iteration."""
            if self.postmortem:
                return self._refuse("the program has ended: this is a post-mortem")
            frame, loops = self._loops_here()
            depth = int(arg or 0)
            if not loops:
                return self._refuse("not inside a loop")
            loop = loops[max(0, len(loops) - 1 - depth)]
            return self._run_to(("iter", loop.line, loop.end), frame)

        def do__gp_end(self, arg):
            """_gp_end: run until the innermost loop ends."""
            if self.postmortem:
                return self._refuse("the program has ended: this is a post-mortem")
            frame, loops = self._loops_here()
            if not loops:
                return self._refuse("not inside a loop")
            loop = loops[-1]
            return self._run_to(("end", loop.line, loop.end), frame)

        def do__gp_goto(self, arg):
            """_gp_goto DEPTH N: run until iteration N of the loop DEPTH levels out from the innermost."""
            if self.postmortem:
                return self._refuse("the program has ended: this is a post-mortem")
            frame, loops = self._loops_here()
            try:
                depth, target = (int(part) for part in arg.split())
            except ValueError:
                return self._refuse("usage: _gp_goto DEPTH N")
            if not loops:
                return self._refuse("not inside a loop")
            loop = loops[max(0, len(loops) - 1 - depth)]
            current = self.counts.get(id(frame), {}).get(loop.line) or derived_position(loop, frame)
            if current is not None and target <= current:
                return self._refuse(f"iteration {target} already ran (now {current})")
            return self._run_to(("goto", loop.line, loop.end, target), frame)

        def _until(self, arg: str, record: bool) -> int:
            if self.postmortem:
                return self._refuse("the program has ended: this is a post-mortem")
            try:
                code = compile(arg, "<stop if>", "eval")
            except SyntaxError as error:
                return self._refuse(f"stop if: {error.msg}")
            return self._run_to(("cond", code, arg), None, record)

        def do__gp_until(self, arg):
            """_gp_until EXPR: run until EXPR is true on a line of a cell; nothing is recorded on the way."""
            return self._until(arg, False)

        def do__gp_until_rec(self, arg):
            """_gp_until_rec EXPR: the same, recording every line on the way."""
            return self._until(arg, True)

        def do__gp_full(self, arg):
            """_gp_full EXPR: the whole value of EXPR, for the value viewer."""
            frame = self.curframe
            try:
                value = eval(arg, frame.f_globals, self.curframe_locals)
            except Exception as error:
                return self._refuse(f"{arg}: {type(error).__name__}: {error}")
            self.reply = {"kind": "full", "name": arg, "value": full(value)}
            return 0

        def do__gp_watch(self, arg):
            if arg and arg not in self.watches:
                self.watches.append(arg)
            return 0

        def do__gp_unwatch(self, arg):
            if arg in self.watches:
                self.watches.remove(arg)
            return 0

        def do__gp_history(self, arg):
            """_gp_history BEFORE: send up to BATCH recorded moments older than BEFORE (the panel
            asks for what it lacks), starting at a keyframe."""
            try:
                before = int(arg.split()[0])
            except (ValueError, IndexError):
                return 0
            self.batches.append(self.recorder.before(before))
            return 0

        def do__gp_frame(self, arg):
            """_gp_frame N: select the N-th shown frame of the stack (u and d without the kernel's frames)."""
            frames = self.user_frames()
            try:
                index = frames[max(0, min(len(frames) - 1, int(arg)))]
            except (ValueError, IndexError):
                return 0
            self.curindex = index
            self.curframe = self.stack[index][0]
            self.curframe_locals = self.curframe.f_locals
            self.lineno = None
            return 0

    return Debugger


def _debugger_class():
    cls = _state.get("class")
    if cls is None:
        cls = _state["class"] = _make_debugger_class()
    return cls


def debugger():
    """The debugger of the current run, made on first use and kept for the run's later breakpoints."""
    current = _state["current"]
    if current is None:
        current = _state["current"] = _debugger_class()()
    return current


def breakpointhook(*args, **kwargs):
    """sys.breakpointhook: pause in the caller and hand the panel the state."""
    debugger().set_trace(sys._getframe(1))


def new_run(cid: str | None) -> None:
    """A gpython cell starts: the next breakpoint opens a fresh session, the old one stays for :debug."""
    if cid and _state.get("armed") is not None:
        _state["armed_ran"] = True
    if cid and _state["current"] is not None:
        _state["last"], _state["current"] = _state["current"], None


def arm(cid: str) -> None:
    """Pause at the first line of the next run of cell `cid` (`r d`, `:debug` on a cell that did not fail)."""

    def tracer(frame, event, arg):
        if event == "call" and cell_files.get(frame.f_code.co_filename) == cid:
            sys.settrace(None)
            _state["armed"] = None
            current = debugger()
            current.set_trace(frame)
            return current.trace_dispatch
        return None

    _state["armed"], _state["armed_ran"] = tracer, False
    sys.settrace(tracer)


def postmortem() -> None:
    """Open the debugger at the frame where the last error was raised; the run's history is kept
    when that run was recorded."""
    error = getattr(sys, "last_exc", None) or getattr(sys, "last_value", None)
    tb = getattr(error, "__traceback__", None) or getattr(sys, "last_traceback", None)
    if tb is None:
        print("Nothing to debug: no cell has failed in this kernel yet.")
        return
    failing = None
    walk = tb
    while walk is not None:
        failing = cell_files.get(walk.tb_frame.f_code.co_filename, failing)
        walk = walk.tb_next
    last = _state["current"] or _state["last"]
    current = last if last is not None and last.cell == failing else None
    if current is None:
        current = _debugger_class()()
        current.cell = failing
    current.postmortem, current.event = True, "postmortem"
    current.exception = f"{type(error).__name__}: {error}" if error is not None else ""
    current.goal = current.goal_frame = None
    current.reset()
    try:
        current.interaction(None, tb)
    finally:
        current.postmortem = False
        if current.hook is not None:
            current.hook.stop()
        sys.settrace(None)


def run_ended(result=None) -> None:
    """A run is over: stop the hook and the trace, so the next runs pay nothing for the debugger.
    An `arm` whose cell run is over without reaching the cell's code (a syntax error, say) is
    dropped too; the run of `_gp_arm` itself is not a cell run and keeps it."""
    armed = _state.get("armed")
    if armed is not None and _state.get("armed_ran"):
        _state["armed"] = None
        if sys.gettrace() is armed:
            sys.settrace(None)
    for current in (_state["current"], _state["last"]):
        if current is None:
            continue
        current.goal = current.goal_frame = None
        if current.hook is not None:
            current.hook.stop()
        if sys.gettrace() == current.trace_dispatch:
            sys.settrace(None)


def install(shell) -> None:
    """Map each gpython cell's code filename to its cell id and make breakpoint() use the panel.

    IPython runs each top-level statement of a cell as its own code object, so the debugger
    steps from one statement's frame into the next and stops tracing when the run ends."""
    sys.breakpointhook = breakpointhook
    if shell is None:
        return

    def pre_run(info) -> None:
        cid = getattr(info, "cell_id", None)
        if not cid:
            return
        try:
            name = shell.compile.get_code_name(info.raw_cell, info.raw_cell, shell.execution_count)
        except Exception:
            return
        cell_files[name] = cid
        new_run(cid)

    shell.events.register("pre_run_cell", pre_run)
    shell.events.register("post_run_cell", run_ended)
