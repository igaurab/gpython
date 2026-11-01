"""Cell layout of plain .py notebooks, kept outside the script so the script stays ordinary code.

gpython writes a plain .py notebook without `# %%` lines. Where each cell starts and ends, and
whether it is markdown or has its code hidden, goes to ~/.gpython/cells/<id>.json instead
(GPYTHON_HOME moves ~/.gpython). On open the layout is used only when the file is byte for byte
what gpython last wrote; a file changed by another editor is split afresh.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("GPYTHON_HOME") or Path.home() / ".gpython")


def _digest(text: str) -> str:
    from hashlib import blake2b

    return blake2b(text.encode("utf-8", "surrogatepass"), digest_size=16).hexdigest()


def layout_file(path: Path) -> Path:
    from hashlib import blake2b

    key = blake2b(str(path.resolve()).encode("utf-8", "surrogatepass"), digest_size=10).hexdigest()
    return home() / "cells" / f"{key}.json"


def write(path: Path, text: str, cells: list[dict]) -> None:
    """Remember `cells` ({start, end, kind, hide_code}, line numbers of `text`) for `path`. Never raises."""
    target = layout_file(path)
    data = {"path": str(path.resolve()), "digest": _digest(text), "cells": cells}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        pass


def read(path: Path, text: str) -> list[tuple[str, str, bool]] | None:
    """(source, kind, hide_code) per cell when `text` is what gpython last wrote to `path`, else None."""
    try:
        data = json.loads(layout_file(path).read_text(encoding="utf-8"))
        if data.get("digest") != _digest(text):
            return None
        lines = text.splitlines()
        cells = []
        for entry in data["cells"]:
            start, end, kind = int(entry["start"]), int(entry["end"]), entry["kind"]
            if kind not in ("code", "markdown") or not (start == end or 0 <= start < end <= len(lines)):
                return None
            chunk = "\n".join(lines[start:end])
            if kind == "markdown" and chunk:
                chunk = _docstring(chunk)
                if chunk is None:
                    return None
            cells.append((chunk, kind, bool(entry.get("hide_code"))))
        return cells or None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _docstring(chunk: str) -> str | None:
    try:
        body = ast.parse(chunk).body
    except SyntaxError:
        return None
    if len(body) == 1 and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        return body[0].value.value.strip("\n")
    return None
