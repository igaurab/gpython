"""Notebook files are plain Python.

Files with `# %%` markers (VS Code, Spyder, jupytext) split at the markers.
Plain scripts are split automatically at top-level statements, and are saved
back without markers so the file keeps its original look.
"""

from __future__ import annotations

import ast
from pathlib import Path

MARKER = "# %%"


def parse(text: str) -> list[str]:
    cells: list[list[str]] = [[]]
    for line in text.splitlines():
        if line.startswith(MARKER):
            cells.append([])
        else:
            cells[-1].append(line)
    sources = ["\n".join(lines).strip("\n") for lines in cells]
    if not sources[0].strip():  # nothing before the first marker
        sources = sources[1:]
    return sources or [""]


def _kind(node: ast.stmt) -> str:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return "import"
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.If, ast.With, ast.For, ast.While, ast.Try)):
        return "block"
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
        return "doc"
    return "simple"


def split_script(text: str) -> list[str]:
    """Split a plain script into cells.

    Each def/class/compound statement and each docstring gets its own cell.
    Consecutive imports share a cell, and so do simple statements that are
    not separated by a blank line. Comments stay with the code below them.
    """
    try:
        body = ast.parse(text).body
    except SyntaxError:
        return [text.strip("\n")] or [""]
    if not body:
        return [text.strip("\n")] if text.strip() else [""]

    lines = text.splitlines()
    starts: list[int] = []  # 0-based line index where each cell begins
    prev: ast.stmt | None = None
    for node in body:
        first = min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", []))]) - 1
        if prev is None:
            starts.append(0)
        else:
            kind, prev_kind = _kind(node), _kind(prev)
            gap = lines[prev.end_lineno : first]
            new_cell = (
                kind in ("block", "doc")
                or prev_kind in ("block", "doc")
                or kind != prev_kind
                or any(not line.strip() for line in gap)
            )
            if new_cell:
                # Start at the first non-blank line after the previous statement,
                # so leading comments move with the statement they describe.
                begin = prev.end_lineno
                while begin < first and not lines[begin].strip():
                    begin += 1
                starts.append(begin)
        prev = node

    bounds = [*starts, len(lines)]
    cells = ["\n".join(lines[a:b]).strip("\n") for a, b in zip(bounds, bounds[1:])]
    return [c for c in cells if c.strip()] or [""]


def has_markers(text: str) -> bool:
    return any(line.startswith(MARKER) for line in text.splitlines())


def dump(sources: list[str], markers: bool = True) -> str:
    if markers:
        return "\n".join(f"{MARKER}\n{s.strip(chr(10))}\n" for s in sources)
    return "\n\n\n".join(s.strip("\n") for s in sources if s.strip()) + "\n"


def load(path: Path) -> tuple[list[str], bool]:
    """Return (cells, uses_markers). New files use markers."""
    if not path.exists():
        return [""], True
    text = path.read_text()
    if has_markers(text):
        return parse(text), True
    return split_script(text), False


def save(path: Path, sources: list[str], markers: bool = True) -> None:
    path.write_text(dump(sources, markers))
