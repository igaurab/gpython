"""Cell layout of plain .py notebooks, kept outside the script so the script stays ordinary code.

gpython writes a plain .py notebook without `# %%` lines. Where each cell starts and ends, whether
it is markdown or has its code hidden, goes to ~/.gpython/cells/<digest>.json, named after the
file's content (GPYTHON_HOME moves ~/.gpython). On open the layout is found by the content alone,
so a file moved, renamed or copied while unchanged keeps its cells; a file changed by another
editor has no layout and is split afresh. cells/paths/<id> notes which layout a path saved last,
so that layout is deleted when the file is saved again.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .notebook import CellSpec


def home() -> Path:
    return Path(os.environ.get("GPYTHON_HOME") or Path.home() / ".gpython")


def _hash(data: str, size: int) -> str:
    from hashlib import blake2b

    return blake2b(data.encode("utf-8", "surrogatepass"), digest_size=size).hexdigest()


def layout_file(text: str) -> Path:
    return home() / "cells" / f"{_hash(text, 16)}.json"


def _path_note(path: Path) -> Path:
    return home() / "cells" / "paths" / _hash(str(path.resolve()), 10)


def _write_atomically(target: Path, data: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, target)


def write(path: Path, text: str, cells: list[dict]) -> None:
    """Remember `cells` ({start, end, kind, hide_code}, line numbers of `text`) for this content,
    and forget the layout `path` had before. Never raises."""
    layout = layout_file(text)
    note = _path_note(path)
    try:
        previous = note.read_text(encoding="utf-8").strip() if note.exists() else ""
        _write_atomically(layout, json.dumps({"path": str(path.resolve()), "cells": cells}))
        _write_atomically(note, layout.name)
        if previous and previous != layout.name:
            (layout.parent / previous).unlink(missing_ok=True)
    except OSError:
        pass


def read(text: str) -> list[CellSpec] | None:
    """The cells gpython last wrote for exactly this content, wherever the file is now; else None.
    Each cell keeps the blank lines after it and, for a docstring cell, its own text."""
    try:
        data = json.loads(layout_file(text).read_text(encoding="utf-8"))
        return _cells(text.splitlines(), data["cells"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _cells(lines: list[str], entries: list[dict]) -> list[CellSpec] | None:
    from .notebook import CellSpec, docstring_value

    cells: list[CellSpec] = []
    filled = [entry for entry in entries if int(entry["start"]) != int(entry["end"])]
    next_start = {id(entry): int(after["start"]) for entry, after in zip(filled, filled[1:])}
    for entry in entries:
        start, end, kind = int(entry["start"]), int(entry["end"]), entry["kind"]
        if kind not in ("code", "markdown") or not (start == end or 0 <= start < end <= len(lines)):
            return None
        hidden = bool(entry.get("hide_code"))
        if start == end:
            cells.append(CellSpec("", kind, hide_code=hidden))
            continue
        chunk = "\n".join(lines[start:end])
        gap = next_start[id(entry)] - end if id(entry) in next_start else None
        if kind == "markdown":
            source = docstring_value(chunk)
            if source is None:
                return None
            cells.append(CellSpec(source, kind, hide_code=hidden, gap=gap, raw=chunk))
        else:
            cells.append(CellSpec(chunk, kind, hide_code=hidden, gap=gap))
    return cells or None
