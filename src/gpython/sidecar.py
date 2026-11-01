"""Cell layout of plain .py notebooks, kept outside the script so the script stays ordinary code.

gpython writes a plain .py notebook without `# %%` lines. Where each cell starts and ends and
whether it is markdown or has its code hidden go to ~/.gpython/cells/<content>/<path>.json, and
each code cell's last outputs (nbformat dicts, as in .ipynb), run number and run time to
<path>.out.json beside it. <content> is named after the file's text and <path> after where the
file is (GPYTHON_HOME moves ~/.gpython). On open the layout of this path is used, or, for a file
moved, renamed or copied while unchanged, the newest layout any path wrote for the same text. A
file changed by another editor has no layout and is split afresh. Nothing is deleted on save, so
copies never lose each other's layout; prune() removes what has not been read or written for a
month. The layout is small and read before the first frame; outputs are read after it.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .notebook import CellSpec

KEEP_DAYS = 30
PRUNE_EVERY_SECONDS = 24 * 3600
LAYOUT = ".json"
OUTPUTS = ".out.json"


def home() -> Path:
    return Path(os.environ.get("GPYTHON_HOME") or Path.home() / ".gpython")


def _hash(data: str, size: int) -> str:
    from hashlib import blake2b

    return blake2b(data.encode("utf-8", "surrogatepass"), digest_size=size).hexdigest()


def content_dir(text: str) -> Path:
    return home() / "cells" / _hash(text, 16)


def _path_name(path: Path) -> str:
    return _hash(str(path.resolve()), 10)


def layout_file(text: str, path: Path) -> Path:
    return content_dir(text) / f"{_path_name(path)}{LAYOUT}"


def outputs_file(text: str, path: Path) -> Path:
    return content_dir(text) / f"{_path_name(path)}{OUTPUTS}"


def _write(target: Path, data: str) -> None:
    from .config import write_atomically

    try:
        write_atomically(target, data)
    except OSError:
        pass


def write_layout(path: Path, text: str, cells: list[dict]) -> None:
    """Remember `cells` ({start, end, kind, hide_code}, line numbers of `text`) for this path and content. Never raises."""
    _write(layout_file(text, path), json.dumps({"path": str(path.resolve()), "cells": cells}))


def write_outputs(path: Path, text: str, states: str) -> None:
    """Remember `states` (a JSON list, one run state per cell) for this path and content. Never raises."""
    _write(outputs_file(text, path), states)


def _find(text: str, path: Path | None, suffix: str) -> Path | None:
    """This path's file for this content, else the newest one any path wrote for it."""
    folder = content_dir(text)
    if path is not None:
        own = folder / f"{_path_name(path)}{suffix}"
        if own.is_file():
            return own
    try:
        others = [entry for entry in folder.iterdir() if entry.name.endswith(suffix) and (suffix == OUTPUTS or not entry.name.endswith(OUTPUTS))]
    except OSError:
        return None
    return max(others, key=lambda entry: entry.stat().st_mtime, default=None)


def _read_json(found: Path):
    data = json.loads(found.read_text(encoding="utf-8"))
    try:
        os.utime(found)
    except OSError:
        pass
    return data


def read(text: str, path: Path | None = None) -> list[CellSpec] | None:
    """The cells gpython last wrote for exactly this content, wherever the file is now; else None.
    Each cell keeps the exact lines after it and, for a docstring cell, its own text."""
    try:
        found = _find(text, path, LAYOUT)
        if found is None:
            return None
        return _cells(text.splitlines(), _read_json(found)["cells"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def read_outputs(text: str, path: Path | None = None) -> list[dict] | None:
    """The run states stored for this content (this path's first), else None."""
    try:
        found = _find(text, path, OUTPUTS)
        data = _read_json(found) if found is not None else None
    except (OSError, ValueError):
        return None
    return data if isinstance(data, list) else None


def _cells(lines: list[str], entries: list[dict]) -> list[CellSpec] | None:
    """Cells from layout entries, or None unless the filled ranges are in order, do not overlap,
    lie inside the file and leave only blank lines between, before and after them."""
    from .notebook import CellSpec, docstring_value, link_gaps, separator

    cells: list[CellSpec] = []
    gaps: list[str | None] = []
    covered = 0
    last_filled: int | None = None
    for entry in entries:
        start, end, kind = int(entry["start"]), int(entry["end"]), entry["kind"]
        if kind not in ("code", "markdown") or not 0 <= start <= end <= len(lines):
            return None
        hidden = bool(entry.get("hide_code"))
        if start == end:
            cells.append(CellSpec("", kind, hide_code=hidden))
            gaps.append(None)
            continue
        if start < covered or any(line.strip() for line in lines[covered:start]):
            return None
        if last_filled is not None:
            gaps[last_filled] = separator(lines[covered:start])
        chunk = "\n".join(lines[start:end])
        if kind == "markdown":
            source = docstring_value(chunk)
            if source is None:
                return None
            cells.append(CellSpec(source, kind, hide_code=hidden, raw=chunk))
        else:
            cells.append(CellSpec(chunk, kind, hide_code=hidden))
        gaps.append(None)
        last_filled = len(cells) - 1
        covered = end
    if not cells or any(line.strip() for line in lines[covered:]):
        return None
    return link_gaps(cells, gaps)


def prune(now: float | None = None) -> int:
    """Delete layouts and outputs not read or written for KEEP_DAYS days; at most once a day.
    Returns how many files went. Never raises."""
    root = home() / "cells"
    stamp = root / ".pruned"
    now = time.time() if now is None else now
    try:
        if stamp.exists() and now - stamp.stat().st_mtime < PRUNE_EVERY_SECONDS:
            return 0
        root.mkdir(parents=True, exist_ok=True)
        stamp.touch()
        os.utime(stamp, (now, now))
    except OSError:
        return 0
    cutoff = now - KEEP_DAYS * 24 * 3600
    removed = 0
    for folder in [entry for entry in _entries(root) if entry.is_dir()]:
        for entry in _entries(folder):
            try:
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    entry.unlink()
                    removed += 1
            except OSError:
                pass
        try:
            folder.rmdir()
        except OSError:
            pass
    return removed


def _entries(folder: Path) -> list[Path]:
    try:
        return list(folder.iterdir())
    except OSError:
        return []
