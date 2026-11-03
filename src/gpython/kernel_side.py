"""The half of the variables panel and the debugger that runs inside the kernel process.

gpython reads this file and executes it in the kernel as the module `_gpython_debug` (see
`kernel._helpers`), so it uses the standard library only and never imports gpython. The tests
import it directly and drive the debugger in process. The record format and the caps are
described in docs/plans/10-variables-debugger.md.
"""

from __future__ import annotations

import ast
import itertools
import json
import linecache
import reprlib
import sys
import types
from collections import deque

MARKER = "\x1egpython-debug\x1e"
HISTORY_CAP = 5000
BATCH = 300
SEQ_WINDOW = 40
GRID_WINDOW = 10
MAP_ITEMS = 30
LOCALS_CAP = 60
TABLE_ROWS = 1000
TABLE_COLS = 50
TEXT_CAP = 200_000
OUTPUT_CAP = 4000
SKIP = [
    "IPython", "IPython.*", "ipykernel", "ipykernel.*", "asyncio", "asyncio.*", "tornado.*", "zmq.*",
    "traitlets.*", "jupyter_client.*", "concurrent.*", "contextlib", "threading", "selectors", "runpy",
    "codeop", "bdb", "pdb", "cmd", "_gpython_debug", "gpython.kernel_side",
]
HIDDEN = {"In", "Out", "get_ipython", "exit", "quit", "open"}
HEAVY = {"numpy", "pandas", "polars", "torch", "jax", "tensorflow", "xarray", "scipy", "pyarrow"}
ROUTINES = (types.FunctionType, types.BuiltinFunctionType, types.MethodType, types.BuiltinMethodType)

cell_files: dict[str, str] = {}
_infos: dict[str, CellInfo | None] = {}
_state = {"current": None, "last": None, "session": 0}


def _repr(maxstring: int, maxother: int, items: int, level: int) -> reprlib.Repr:
    shaper = reprlib.Repr()
    shaper.maxstring, shaper.maxother, shaper.maxlevel = maxstring, maxother, level
    shaper.maxlist = shaper.maxtuple = shaper.maxset = shaper.maxfrozenset = shaper.maxdeque = items
    shaper.maxdict = max(2, items // 2)
    shaper.maxarray = items
    return shaper


_SHORT = _repr(60, 60, 10, 2)
_TINY = _repr(14, 14, 3, 1)


def _heavy(value) -> bool:
    return type(value).__module__.partition(".")[0] in HEAVY


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
    """`(3, 4) float64` for arrays and frames, `len 5` for sized containers and strings."""
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
            return f"{shape} {dtype}".strip() if dtype is not None else str(shape)
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


def short(value, width: int = 60) -> str:
    """A one-line repr that never formats a huge object in full."""
    try:
        if _heavy(value):
            text = _heavy_text(value)
        elif isinstance(value, types.ModuleType):
            text = ""
        else:
            text = _SHORT.repr(value)
    except Exception as error:
        text = f"<repr failed: {error!r}>"
    text = text.replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"


def _heavy_text(value) -> str:
    if hasattr(value, "columns") and hasattr(value, "iloc"):
        names = [str(c) for c in list(value.columns[:8])]
        return "columns: " + ", ".join(names) + (" …" if len(value.columns) > 8 else "")
    if hasattr(value, "iloc") and hasattr(value, "tolist"):
        return _SHORT.repr(value.iloc[:10].tolist())
    if getattr(value, "ndim", None) is not None and getattr(value, "size", 0) > 10_000:
        return f"{type(value).__name__} of {value.size} items"
    return " ".join(repr(value).split())


def cell_text(value, width: int = 6) -> str:
    """An element inside a strip or a grid box: numbers compact, strings bare."""
    if isinstance(value, bool) or value is None:
        text = str(value)
    elif isinstance(value, int):
        text = str(value)
    elif isinstance(value, float):
        text = format(value, ".4g")
    elif isinstance(value, str):
        text = value if len(value) <= 1 else repr(value)
    elif _heavy(value) and getattr(value, "ndim", 1) == 0:
        text = str(value.item()) if hasattr(value, "item") else str(value)
    else:
        text = _TINY.repr(value)
    text = text.replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"


def describe(name: str, value) -> list:
    return [name, type(value).__name__, short(value), shape_info(value), category(value)]


def variables(namespace: dict, names) -> list:
    """Rows [name, type, value, shape/len/dtype, category] for the names that exist."""
    return [describe(n, namespace[n]) for n in sorted(names) if n in namespace]


def full(value) -> dict:
    """The whole value for the viewer, capped: a table for frames and 2-D data, else text."""
    try:
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
        shown = [[cell_text(_at(value, r, c), 40) for c in range(min(cols, TABLE_COLS))] for r in range(min(rows, TABLE_ROWS))]
        return {
            "kind": "table", "columns": [str(c) for c in range(min(cols, TABLE_COLS))],
            "index": [str(r) for r in range(len(shown))], "rows": shown, "total": [rows, cols],
        }
    return None


def _full_text(value) -> str:
    import pprint

    if _heavy(value) and getattr(value, "ndim", None) is not None and type(value).__module__.startswith("numpy"):
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
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            return
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
    """seq, grid, map or '' for the views under the loop stack."""
    if isinstance(value, (dict, set, frozenset)):
        return "map"
    if _heavy(value):
        ndim = getattr(value, "ndim", None)
        if ndim == 1 and not hasattr(value, "iloc"):
            return "seq"
        if ndim == 2 and not hasattr(value, "iloc"):
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


def _seq_view(name: str, value, pointers: list[list]) -> dict:
    length = len(value)
    start = _window([p for _, p in pointers], length, SEQ_WINDOW)
    stop = min(length, start + SEQ_WINDOW)
    if isinstance(value, str):
        items = list(value[start:stop])
    else:
        items = [cell_text(value[i]) for i in range(start, stop)]
    return {"name": name, "type": type(value).__name__, "len": length, "start": start, "items": items, "ptrs": pointers}


def _grid_view(name: str, value, pointers: list[list]) -> dict:
    rows, cols = _grid_shape(value)
    row0 = _window([p[2] for p in pointers], rows, GRID_WINDOW)
    col0 = _window([p[3] for p in pointers], cols, GRID_WINDOW)
    cells = []
    for r in range(row0, min(rows, row0 + GRID_WINDOW)):
        line = []
        for c in range(col0, min(cols, col0 + GRID_WINDOW)):
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


def snapshot(frame, line: int, positions: dict | None = None) -> dict:
    """Everything the panel draws for one moment: loops, sequences, grids, maps and locals.

    On a loop's header line the next iteration has not started (its variable still holds the
    last value), so the header shows the iteration that just ended: 0 before the first."""
    filename = frame.f_code.co_filename
    info = cell_info(filename)
    space = frame.f_locals
    data = {}
    for name in frame_names(frame, info):
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
            if isinstance(node, ast.Name) and k is not None and loop.targets and loop.iter is node:
                seq_pointers.setdefault(node.id, []).append([loop.targets[0], k - 1])
    seqs, grids, maps = [], [], []
    for name, value in data.items():
        kind = _seq_kind(value)
        if not kind:
            continue
        try:
            if kind == "seq" and len(seqs) < 4:
                pointers = _pointers(name, value, info, frame, seq_pointers.get(name, []), data, loops)
                if pointers or isinstance(value, list) or _heavy(value):
                    seqs.append(_seq_view(name, value, pointers))
            elif kind == "grid" and len(grids) < 2:
                grids.append(_grid_view(name, value, _grid_pointers(name, info, frame)))
            elif kind == "map" and len(maps) < 3:
                maps.append(_map_view(name, value))
        except Exception:
            continue
    seqs.sort(key=lambda s: not s["ptrs"])
    return {
        "cell": cell_files.get(filename), "file": filename, "line": line, "func": frame.f_code.co_name,
        "loops": loops, "seqs": seqs, "grids": grids, "maps": maps,
        "vars": [[n, short(v, 40)] for n, v in data.items()],
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
                if isinstance(candidate, int) and not isinstance(candidate, bool) and 0 <= candidate <= length:
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


def _make_debugger_class():
    """The pdb.Pdb subclass, built on the first breakpoint so a kernel that never debugs pays nothing.

    ipykernel puts IPython's own debugger in `pdb.Pdb`; the standard library's class is the one
    in its MRO whose module is `pdb`."""
    import io
    import pdb

    standard = next(c for c in pdb.Pdb.__mro__ if c.__module__ == "pdb")

    class Debugger(standard):
        """pdb that answers in JSON: every prompt carries the stop's state after MARKER, and gpython
        draws it in the variables panel. Commands come back as pdb commands plus the `_gp_*` ones."""

        def __init__(self) -> None:
            self.buffer = io.StringIO()
            super().__init__(stdout=self.buffer, skip=SKIP, nosigint=True, readrc=False)
            self.use_rawinput = 1
            _state["session"] += 1
            self.session = _state["session"]
            self.history: deque = deque(maxlen=HISTORY_CAP)
            self.total = 0
            self.sent = 0
            self.batches: list[dict] = []
            self.counts: dict[int, dict[int, int | None]] = {}
            self.last_lines: dict[int, int] = {}
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

        def observe(self, frame) -> None:
            """A line event in a cell's frame: count loop iterations and record a snapshot."""
            info = cell_info(frame.f_code.co_filename)
            if info is None:
                return
            key, line = id(frame), frame.f_lineno
            previous = self.last_lines.get(key)
            self.last_lines[key] = line
            counts = self.counts.setdefault(key, {})
            for loop in info.active_loops(frame, line):
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
            self.pending = (frame, line, self.record(frame, line))

        def record(self, frame, line: int) -> dict:
            entry = snapshot(frame, line, self.counts.get(id(frame)))
            entry["i"] = self.total
            self.history.append(entry)
            self.total += 1
            return entry

        def goal_met(self, frame, previous) -> bool:
            goal, line = self.goal, frame.f_lineno
            kind = goal[0]
            if kind == "cond":
                try:
                    return bool(eval(goal[1], frame.f_globals, frame.f_locals))
                except Exception:
                    return False
            if frame is not self.goal_frame:
                return False
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

        def dispatch_line(self, frame):
            if self.is_cell(frame):
                previous = self.last_lines.get(id(frame))
                self.observe(frame)
                if self.goal is not None and self.goal_met(frame, previous):
                    self.goal = self.goal_frame = None
                    self.set_step()
                    self.user_line(frame)
                    if self.quitting:
                        raise __import__("bdb").BdbQuit
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
                self.set_step()
            try:
                return super().dispatch_return(frame, arg)
            finally:
                self.last_lines.pop(id(frame), None)
                self.counts.pop(id(frame), None)

        def dispatch_exception(self, frame, arg):
            if self.goal is not None and self.is_cell(frame) and not self.stop_here(frame):
                self.goal = self.goal_frame = None
                self.set_step()
                self.user_exception(frame, arg)
                if self.quitting:
                    raise __import__("bdb").BdbQuit
                return self.trace_dispatch
            return super().dispatch_exception(frame, arg)

        def user_line(self, frame):
            self.event = "line"
            super().user_line(frame)

        def stop_here(self, frame):
            if not self.is_cell(frame) and self.outermost(frame):
                return False
            return super().stop_here(frame)

        def user_call(self, frame, argument_list):
            return

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

        def user_frames(self) -> list[int]:
            """Indexes into self.stack of the frames worth showing: from the first cell frame on."""
            shown = [i for i, (f, _) in enumerate(self.stack) if not self.is_skipped_module(f.f_globals.get("__name__"))]
            cells = [i for i in shown if self.is_cell(self.stack[i][0])]
            return [i for i in shown if not cells or i >= cells[0]]

        def payload(self) -> dict:
            frame, line = self.stack[self.curindex]
            info = cell_info(frame.f_code.co_filename)
            frames = self.user_frames()
            if self.curindex == len(self.stack) - 1 and self.stop_index is not None and not self.postmortem:
                now = self.history[self.stop_index - (self.total - len(self.history))] if self.stop_index >= self.total - len(self.history) else snapshot(frame, line)
            else:
                now = snapshot(frame, line, self.counts.get(id(frame)))
                now["i"] = None
            space = frame.f_locals
            names = [n for n in frame_names(frame, info) if n in space]
            output = "\n".join(
                text for text in self.buffer.getvalue().splitlines()
                if not text.startswith(("> ", "-> ", "--Return--", "--Call--"))
            )
            self.buffer.seek(0)
            self.buffer.truncate()
            reply, self.reply = self.reply, None
            first = self.total - len(self.history)
            start = max(self.sent, self.total - BATCH, first)
            batches = self.batches + [{"start": start, "records": list(itertools.islice(self.history, start - first, None))}]
            self.batches, self.sent = [], self.total
            return {
                "session": self.session, "event": self.event, "postmortem": self.postmortem, "exception": self.exception,
                "now": now, "index": now.get("i"),
                "stack": [
                    {"file": f.f_code.co_filename, "line": n, "func": f.f_code.co_name, "cell": cell_files.get(f.f_code.co_filename)}
                    for f, n in (self.stack[i] for i in frames)
                ],
                "frame": frames.index(self.curindex) if self.curindex in frames else len(frames) - 1,
                "locals": [describe(n, space[n]) for n in names],
                "watches": [[expr, self._watch(expr, frame)] for expr in self.watches],
                "reply": reply, "output": output[-OUTPUT_CAP:],
                "history": {"total": self.total, "first": first, "batches": batches},
            }

        def _watch(self, expr: str, frame) -> str:
            try:
                return short(eval(expr, frame.f_globals, self.curframe_locals), 50)
            except Exception as error:
                return f"error: {type(error).__name__}"

        def _loops_here(self):
            frame, line = self.stack[self.curindex]
            info = cell_info(frame.f_code.co_filename)
            return frame, (info.active_loops(frame, line) if info is not None else [])

        def _run_to(self, goal, frame) -> int:
            self.goal, self.goal_frame = goal, frame
            self._set_stopinfo(self.botframe, None, -1)
            return 1

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

        def do__gp_until(self, arg):
            """_gp_until EXPR: run until EXPR is true on a line of a cell."""
            if self.postmortem:
                return self._refuse("the program has ended: this is a post-mortem")
            try:
                code = compile(arg, "<stop if>", "eval")
            except SyntaxError as error:
                return self._refuse(f"stop if: {error.msg}")
            return self._run_to(("cond", code, arg), None)

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
            """_gp_history START STOP: send recorded snapshots again (the panel asks for gaps)."""
            first = self.total - len(self.history)
            try:
                start, stop = (int(part) for part in arg.split())
            except ValueError:
                return 0
            start, stop = max(start, first), min(stop, self.total, max(start, first) + BATCH)
            if start < stop:
                self.batches.append({"start": start, "records": list(itertools.islice(self.history, start - first, stop - first))})
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


def debugger():
    """The debugger of the current run, made on first use and kept for the run's later breakpoints."""
    current = _state["current"]
    if current is None:
        cls = _state.get("class")
        if cls is None:
            cls = _state["class"] = _make_debugger_class()
        current = _state["current"] = cls()
    return current


def breakpointhook(*args, **kwargs):
    """sys.breakpointhook: pause in the caller and hand the panel the state."""
    debugger().set_trace(sys._getframe(1))


def new_run(cid: str | None) -> None:
    """A gpython cell starts: the next breakpoint opens a fresh session, the old one stays for :debug."""
    if cid and _state["current"] is not None:
        _state["last"], _state["current"] = _state["current"], None


def arm(cid: str) -> None:
    """Pause at the first line of the next run of cell `cid` (`r d`, `:debug` on a cell that did not fail)."""

    def tracer(frame, event, arg):
        if event == "call" and cell_files.get(frame.f_code.co_filename) == cid:
            sys.settrace(None)
            current = debugger()
            current.set_trace(frame)
            return current.trace_dispatch
        return None

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
        cls = _state.get("class") or _make_debugger_class()
        _state["class"] = cls
        current = cls()
        current.cell = failing
    current.postmortem, current.event = True, "postmortem"
    current.exception = f"{type(error).__name__}: {error}" if error is not None else ""
    current.goal = current.goal_frame = None
    current.reset()
    try:
        current.interaction(None, tb)
    finally:
        current.postmortem = False


def run_ended(result=None) -> None:
    """A run is over: stop tracing, so the next runs pay nothing for the debugger."""
    current = _state["current"]
    if current is not None and sys.gettrace() == current.trace_dispatch:
        current.goal = current.goal_frame = None
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
