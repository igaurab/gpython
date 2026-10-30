"""Notebook files: plain Python (with or without `# %%` markers) and `.ipynb`.

Percent format (VS Code, Spyder, Jupytext):
    # %%                 code cell
    # %% [markdown]      markdown cell, each line prefixed with "# "

Plain scripts are split automatically at top-level statements; module-level
string statements (docstrings) become markdown cells. They are saved back
without markers so the file stays an ordinary script.

`.ipynb` files load code and markdown cells (outputs are dropped) and save as
nbformat 4 with empty outputs, keeping the notebook metadata that was loaded.

marimo files (`import marimo` + `app = marimo.App(...)`) load one cell per
`@app.cell` function (body without the trailing `return`), `@app.class_definition`
or `@app.function`, and save back in marimo's own layout, byte for byte when
nothing was edited.
"""

from __future__ import annotations

import ast
import io
import json
import textwrap
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from gpython import graph

MARKER = "# %%"
Kind = Literal["code", "markdown"]


Decorator = Literal["cell", "class_definition", "function"]


@dataclass(frozen=True)
class CellSpec:
    source: str
    kind: Kind = "code"
    # marimo only; ignored by the other formats.
    name: str | None = None  # function name when it is not `_`
    hide_code: bool = False
    decorator: Decorator = "cell"


def _spec(cell: CellSpec | str) -> CellSpec:
    return cell if isinstance(cell, CellSpec) else CellSpec(cell)


# ── percent format ───────────────────────────────────────────────────────


def has_markers(text: str) -> bool:
    return any(line.startswith(MARKER) for line in text.splitlines())


def _markdown_from_comments(lines: list[str]) -> str:
    out = []
    for line in lines:
        if line.startswith("# "):
            out.append(line[2:])
        elif line.rstrip() == "#":
            out.append("")
        else:
            out.append(line)
    return "\n".join(out).strip("\n")


def parse(text: str) -> list[CellSpec]:
    """Split percent-format text into cells."""
    chunks: list[tuple[Kind, list[str]]] = [("code", [])]
    for line in text.splitlines():
        if line.startswith(MARKER):
            tag = line[len(MARKER) :].strip().lower()
            kind: Kind = "markdown" if tag.startswith(("[markdown]", "[md]")) else "code"
            chunks.append((kind, []))
        else:
            chunks[-1][1].append(line)
    cells = []
    for i, (kind, lines) in enumerate(chunks):
        if i == 0 and not "".join(lines).strip():
            continue  # nothing before the first marker
        if kind == "markdown":
            cells.append(CellSpec(_markdown_from_comments(lines), "markdown"))
        else:
            cells.append(CellSpec("\n".join(lines).strip("\n"), "code"))
    return cells or [CellSpec("")]


def _dump_percent(cells: list[CellSpec]) -> str:
    parts = []
    for cell in cells:
        body = cell.source.strip("\n")
        if cell.kind == "markdown":
            lines = [f"# {line}" if line.strip() else "#" for line in body.splitlines()]
            parts.append(f"{MARKER} [markdown]\n" + "\n".join(lines) + "\n")
        else:
            parts.append(f"{MARKER}\n{body}\n")
    return "\n".join(parts)


# ── plain scripts ────────────────────────────────────────────────────────


def _kind(node: ast.stmt) -> str:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return "import"
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.If, ast.With, ast.For, ast.While, ast.Try)):
        return "block"
    if _is_docstring(node):
        return "doc"
    return "simple"


def _is_docstring(node: ast.stmt) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def split_script(text: str) -> list[CellSpec]:
    """Split a plain script into cells.

    Each def/class/compound statement gets its own cell and each module-level
    string statement becomes a markdown cell. Consecutive imports share a
    cell, and so do simple statements not separated by a blank line.
    Comments stay with the code below them.
    """
    try:
        body = ast.parse(text).body
    except SyntaxError:
        return [CellSpec(text.strip("\n"))]
    if not body:
        return [CellSpec(text.strip("\n"))] if text.strip() else [CellSpec("")]

    lines = text.splitlines()
    starts: list[int] = []
    prev: ast.stmt | None = None
    for node in body:
        first = min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", []))]) - 1
        if prev is None:
            starts.append(0)
        else:
            kind, prev_kind = _kind(node), _kind(prev)
            gap = lines[prev.end_lineno : first]
            if (
                kind in ("block", "doc")
                or prev_kind in ("block", "doc")
                or kind != prev_kind
                or any(not line.strip() for line in gap)
            ):
                begin = prev.end_lineno
                while begin < first and not lines[begin].strip():
                    begin += 1
                starts.append(begin)
        prev = node

    bounds = [*starts, len(lines)]
    cells: list[CellSpec] = []
    for node_index, (a, b) in enumerate(zip(bounds, bounds[1:])):
        chunk = "\n".join(lines[a:b]).strip("\n")
        if not chunk.strip():
            continue
        node = _statement_at(body, a + 1)
        if node is not None and _is_docstring(node) and not lines[a].lstrip().startswith("#"):
            cells.append(CellSpec(node.value.value.strip("\n"), "markdown"))
        else:
            cells.append(CellSpec(chunk))
    return cells or [CellSpec("")]


def _statement_at(body: list[ast.stmt], lineno: int) -> ast.stmt | None:
    for node in body:
        first = min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", []))])
        if first <= lineno <= node.end_lineno:
            return node
    return None


def _dump_script(cells: list[CellSpec]) -> str:
    parts = []
    for cell in cells:
        body = cell.source.strip("\n")
        if not body.strip():
            continue
        if cell.kind == "markdown":
            escaped = body.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
            parts.append(f'"""\n{escaped}\n"""')
        else:
            parts.append(body)
    return "\n\n\n".join(parts) + "\n"


# ── ipynb ────────────────────────────────────────────────────────────────

_DEFAULT_IPYNB_META = {
    "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
    "language_info": {"name": "python"},
}
_ipynb_meta: dict[Path, dict] = {}  # metadata seen at load, reused on save


def _is_ipynb(path: Path) -> bool:
    return path.suffix.lower() == ".ipynb"


def parse_ipynb(text: str, path: Path | None = None) -> list[CellSpec]:
    nb = json.loads(text)
    if path is not None:
        _ipynb_meta[path.resolve()] = nb.get("metadata", {})
    cells = []
    for cell in nb.get("cells", []):
        source = cell.get("source", "")
        if isinstance(source, list):
            source = "".join(source)
        kind: Kind = "markdown" if cell.get("cell_type") in ("markdown", "raw") else "code"
        cells.append(CellSpec(source.strip("\n"), kind))
    return cells or [CellSpec("")]


def dump_ipynb(cells: list[CellSpec], path: Path | None = None) -> str:
    def lines(source: str) -> list[str]:
        return source.strip("\n").splitlines(keepends=True)

    out = []
    for cell in cells:
        if cell.kind == "markdown":
            out.append({"cell_type": "markdown", "metadata": {}, "source": lines(cell.source)})
        else:
            out.append(
                {
                    "cell_type": "code",
                    "execution_count": None,
                    "metadata": {},
                    "outputs": [],
                    "source": lines(cell.source),
                }
            )
    metadata = _ipynb_meta.get(path.resolve()) if path is not None else None
    nb = {"cells": out, "metadata": metadata or _DEFAULT_IPYNB_META, "nbformat": 4, "nbformat_minor": 5}
    return json.dumps(nb, indent=1, ensure_ascii=False) + "\n"


# ── marimo ───────────────────────────────────────────────────────────────

MARIMO_PRELUDE = 'import marimo\n\n__generated_with = "0.25.2"\napp = marimo.App(width="medium")\n\n\n'
MARIMO_FOOTER = '\n\n\nif __name__ == "__main__":\n    app.run()\n'
_LINE_LIMIT = 88  # ruff/black line length marimo formats with
_marimo_frame: dict[Path, tuple[str, str]] = {}  # path -> (prelude, footer) seen at load
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def is_marimo(text: str) -> bool:
    """True when the module does `import marimo` and `app = marimo.App(...)` at top level."""
    try:
        body = ast.parse(text).body
    except SyntaxError:
        return False
    imports = any(
        isinstance(node, ast.Import) and any(a.name == "marimo" and a.asname is None for a in node.names)
        for node in body
    )
    return imports and any(
        isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "app" for t in node.targets)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "App"
        and isinstance(node.value.func.value, ast.Name)
        and node.value.func.value.id == "marimo"
        for node in body
    )


def _app_decorator(node: ast.stmt) -> tuple[ast.expr, str, bool] | None:
    """(decorator, "cell" | "class_definition" | "function", hide_code) for `@app.x` / `@app.x(...)`."""
    for dec in getattr(node, "decorator_list", []):
        call = dec if isinstance(dec, ast.Call) else None
        target = call.func if call else dec
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "app"
            and target.attr in ("cell", "class_definition", "function")
        ):
            hide = any(
                k.arg == "hide_code" and isinstance(k.value, ast.Constant) and k.value.value is True
                for k in (call.keywords if call else [])
            )
            return dec, target.attr, hide
    return None


def _header_end(lines: list[str], node: ast.stmt) -> int:
    """1-based line on which the `def ...(...):` header of node ends."""
    text = "\n".join(lines[node.lineno - 1 : node.end_lineno]) + "\n"
    depth = 0
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.OP:
                if tok.string in "([{":
                    depth += 1
                elif tok.string in ")]}":
                    depth -= 1
                elif tok.string == ":" and depth == 0:
                    return node.lineno + tok.start[0] - 1
    except (tokenize.TokenError, SyntaxError):
        pass
    return node.lineno


def _unindent(line: str) -> str:
    """Remove the 4-space function indent (marimo strips it from every line, strings included)."""
    if line.startswith("    "):
        return line[4:]
    return "" if not line.strip() else line


def _strip_blank(lines: list[str]) -> list[str]:
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def _md_code(text: str) -> str:
    """Cell code for a markdown cell, the way marimo writes it."""
    if '"""' in text or text.endswith("\\"):
        escaped = text.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
        return f'mo.md("""\n{escaped}\n""")'
    return f'mo.md(r"""\n{text}\n""")'


def _as_markdown(source: str) -> str | None:
    """The markdown text when the cell is exactly `mo.md(r\"\"\"...\"\"\")` in marimo's layout.

    Other spellings stay code cells so that saving never rewrites them.
    """
    try:
        body = ast.parse(source).body
    except SyntaxError:
        return None
    if len(body) != 1 or not isinstance(body[0], ast.Expr):
        return None
    call = body[0].value
    if not (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "md"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "mo"
        and len(call.args) == 1
        and not call.keywords
        and isinstance(call.args[0], ast.Constant)
        and isinstance(call.args[0].value, str)
    ):
        return None
    text = textwrap.dedent(call.args[0].value).strip("\n")
    return text if _md_code(text) == source else None


def _cell_from_function(lines: list[str], node: ast.FunctionDef | ast.AsyncFunctionDef, hide: bool) -> CellSpec:
    start = _header_end(lines, node)  # 0-based index of the first body line
    end = node.end_lineno
    if node.body and isinstance(node.body[-1], ast.Return):
        end = node.body[-1].lineno - 1
    source = "\n".join(_strip_blank([_unindent(line) for line in lines[start:end]]))
    if source.strip() == "pass":
        source = ""
    name = None if node.name == "_" else node.name
    text = _as_markdown(source) if hide else None
    if text is not None:
        return CellSpec(text, "markdown", name, True, "cell")
    return CellSpec(source, "code", name, hide, "cell")


def _first_line(node: ast.stmt) -> int:
    return min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", []))])


def _split_marimo(text: str) -> tuple[str, list[CellSpec], str]:
    """(prelude, cells, footer) of a marimo file; prelude + cells joined + footer == text."""
    lines = text.split("\n")
    body = ast.parse(text).body
    first = next((i for i, n in enumerate(body) if _app_decorator(n)), len(body))
    footer_at = len(body)
    for i in range(len(body) - 1, first - 1, -1):
        if isinstance(body[i], ast.If) and "__name__" in ast.unparse(body[i].test):
            footer_at = i
            break

    nodes = body[first:footer_at]
    cells: list[CellSpec] = []
    for i, node in enumerate(nodes):
        found = _app_decorator(node)
        stop = _first_line(nodes[i + 1]) - 1 if i + 1 < len(nodes) else node.end_lineno
        if found and found[1] == "cell" and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            cells.append(_cell_from_function(lines, node, found[2]))
        elif found:
            dec, kind, hide = found
            # The whole class/def without its `@app...` line; comments up to the next cell stay.
            chunk = _strip_blank(lines[dec.end_lineno : stop])
            cells.append(CellSpec("\n".join(chunk), "code", None, hide, kind))
        else:
            cells.append(CellSpec("\n".join(_strip_blank(lines[_first_line(node) - 1 : stop]))))

    if nodes:
        prelude = "\n".join(lines[: _first_line(nodes[0]) - 1]) + "\n"
        footer = "\n" + "\n".join(lines[nodes[-1].end_lineno :])
    else:
        cut = _first_line(body[footer_at]) - 1 if footer_at < len(body) else len(lines)
        prelude = "\n".join(lines[:cut]).rstrip("\n") + "\n\n\n"
        footer = "\n" + "\n".join(lines[cut:]) if cut < len(lines) else MARIMO_FOOTER
    return prelude, cells, footer


def parse_marimo(text: str, path: Path | None = None) -> list[CellSpec]:
    """Cells of a marimo notebook. With a path, its prelude/footer are kept for save()."""
    prelude, cells, footer = _split_marimo(text)
    if path is not None:
        _marimo_frame[path.resolve()] = (prelude, footer)
    return cells or [CellSpec("")]


def _has_top_level_await(tree: ast.AST) -> bool:
    for child in ast.iter_child_nodes(tree):
        if isinstance(child, _SCOPES):
            continue
        if isinstance(child, (ast.Await, ast.AsyncFor, ast.AsyncWith)) or _has_top_level_await(child):
            return True
    return False


def _cell_code(cell: CellSpec) -> str:
    return _md_code(cell.source.strip("\n")) if cell.kind == "markdown" else cell.source


def _wrapped(names: list[str], prefix: str, close: str, indent: str) -> str:
    """`prefix a, b close`, or one name per line when too long (as ruff formats it)."""
    flat = prefix + ", ".join(names) + close
    if len(flat) <= _LINE_LIMIT:
        return flat
    inner = "".join(f"{indent}    {n},\n" for n in names)
    return f"{prefix}\n{inner}{indent}{close}"


def _dump_marimo_cell(cell: CellSpec, code: str, params: list[str], returns: list[str]) -> str:
    hide = cell.hide_code or cell.kind == "markdown"
    if cell.decorator in ("class_definition", "function"):
        deco = f"@app.{cell.decorator}" + ("(hide_code=True)" if hide else "")
        return f"{deco}\n" + "\n".join(_strip_blank(code.split("\n")))

    deco = "@app.cell(hide_code=True)" if hide else "@app.cell"
    body = _strip_blank(code.split("\n"))
    try:
        tree = ast.parse(graph.strip_magics("\n".join(body)))
    except SyntaxError:
        tree = None
    is_async = tree is not None and _has_top_level_await(tree)
    head = ("async def " if is_async else "def ") + (cell.name or "_") + "("
    signature = _wrapped(params, head, "):", "")

    if len(returns) == 1:
        ret = f"    return ({returns[0]},)"
    elif returns:
        ret = "    return " + ", ".join(returns)
        if len(ret) > _LINE_LIMIT:
            ret = _wrapped(returns, "    return (", ")", "    ")
    else:
        ret = "    return"

    parts = [deco, signature]
    parts.extend(f"    {line}" if line else "" for line in body)
    if body:
        # Formatter rule: one blank line after a trailing import or nested def/class,
        # unless comments follow it.
        last = tree.body[-1] if tree is not None and tree.body else None
        if (
            isinstance(last, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and last.end_lineno == len(body)
        ):
            parts.append("")
    parts.append(ret)
    return "\n".join(parts)


def dump_marimo(cells: list[CellSpec | str], path: Path | None = None) -> str:
    """marimo file text.

    Like marimo: a cell's parameters are the names it uses that another `@app.cell`
    defines, and it returns the names it defines that another cell uses, both sorted.
    Names from `@app.class_definition`/`@app.function` cells are module-level, never parameters.
    """
    specs = [_spec(c) for c in cells]
    codes = [_cell_code(c) for c in specs]
    analyses: list[tuple[set[str], set[str]]] = []
    for spec, code in zip(specs, codes):
        try:
            defs, refs = graph.analyze(code)
        except SyntaxError:
            defs, refs = set(), set()
        analyses.append((defs if spec.decorator == "cell" else set(), refs))

    parts = []
    for i, (spec, code) in enumerate(zip(specs, codes)):
        defs, refs = analyses[i]
        others = [a for j, a in enumerate(analyses) if j != i]
        defined = set().union(*(d for d, _ in others))
        used = set().union(*(r for _, r in others))
        parts.append(_dump_marimo_cell(spec, code, sorted(refs & defined), sorted(defs & used)))

    prelude, footer = MARIMO_PRELUDE, MARIMO_FOOTER
    if path is not None:
        prelude, footer = _marimo_frame.get(path.resolve(), (prelude, footer))
    return prelude + "\n\n\n".join(parts) + footer


def _starts_with_marimo_import(cells: list[CellSpec]) -> bool:
    if not cells or cells[0].kind != "code":
        return False
    try:
        body = ast.parse(cells[0].source).body
    except SyntaxError:
        return False
    return bool(body) and isinstance(body[0], ast.Import) and any(a.name == "marimo" for a in body[0].names)


# ── public API ───────────────────────────────────────────────────────────


def dump(cells: list[CellSpec | str], markers: bool = True) -> str:
    specs = [_spec(c) for c in cells]
    return _dump_percent(specs) if markers else _dump_script(specs)


def load_cells(path: Path) -> tuple[list[CellSpec], bool]:
    """Return (cells, uses_markers). New files use markers."""
    if not path.exists():
        return [CellSpec("")], True
    text = path.read_text()
    if _is_ipynb(path):
        return parse_ipynb(text, path), True
    _marimo_frame.pop(path.resolve(), None)
    if is_marimo(text):
        return parse_marimo(text, path), True
    if has_markers(text):
        return parse(text), True
    return split_script(text), False


def load(path: Path) -> tuple[list[str], bool]:
    """Transitional: like load_cells() but markdown cells come back as comment blocks.

    The UI will switch to load_cells(); until then nothing is lost on save.
    """
    cells, markers = load_cells(path)
    sources = []
    for cell in cells:
        if cell.kind == "markdown":
            sources.append("\n".join(f"# {line}" if line.strip() else "#" for line in cell.source.splitlines()))
        else:
            sources.append(cell.source)
    return sources, markers


def save(path: Path, cells: list[CellSpec | str], markers: bool = True) -> None:
    specs = [_spec(c) for c in cells]
    if _is_ipynb(path):
        path.write_text(dump_ipynb(specs, path))
    elif path.resolve() in _marimo_frame or (not path.exists() and _starts_with_marimo_import(specs)):
        path.write_text(dump_marimo(specs, path))
    else:
        path.write_text(dump(specs, markers))
