"""Notebook files are plain Python with `# %%` cell markers.

The same format is used by VS Code, Spyder and jupytext, and the file still
runs as a normal script.
"""

from __future__ import annotations

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


def dump(sources: list[str]) -> str:
    return "\n\n".join(f"{MARKER}\n{s.strip(chr(10))}\n" for s in sources)


def load(path: Path) -> list[str]:
    return parse(path.read_text()) if path.exists() else [""]


def save(path: Path, sources: list[str]) -> None:
    path.write_text(dump(sources))
