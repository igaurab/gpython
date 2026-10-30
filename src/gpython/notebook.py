"""Notebook files: plain Python (with or without `# %%` markers) and `.ipynb`.

Percent format (VS Code, Spyder, Jupytext):
    # %%                 code cell
    # %% [markdown]      markdown cell, each line prefixed with "# "

Plain scripts are split automatically at top-level statements; module-level
string statements (docstrings) become markdown cells. They are saved back
without markers so the file stays an ordinary script.

`.ipynb` files load code and markdown cells (outputs are dropped) and save as
nbformat 4 with empty outputs, keeping the notebook metadata that was loaded.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

MARKER = "# %%"
Kind = Literal["code", "markdown"]


@dataclass(frozen=True)
class CellSpec:
    source: str
    kind: Kind = "code"


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
    else:
        path.write_text(dump(specs, markers))
