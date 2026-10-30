"""Tiny persistent config: a flat TOML file of str/bool/int values.

A table is read as dotted keys: `[experimental]` + `true_markdown = true` is
`experimental.true_markdown`, the same key as `"experimental.true_markdown" = true`.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "gpython" / "config.toml"


def _path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    return Path(base) / "gpython" / "config.toml" if base else CONFIG_PATH


def load() -> dict:
    """The config as a dict; {} if the file is missing or invalid. Never raises."""
    try:
        with open(_path(), "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return {}
    flat = {f"{k}.{sub}": v for k, table in data.items() if isinstance(table, dict) for sub, v in table.items()}
    flat.update(data)
    return {k: v for k, v in flat.items() if isinstance(v, (str, bool, int))}


def _value(v: str | bool | int) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    out = v.replace("\\", "\\\\").replace('"', '\\"')
    for ch, esc in (("\n", "\\n"), ("\r", "\\r"), ("\t", "\\t")):
        out = out.replace(ch, esc)
    return f'"{out}"'


def _key(k: str) -> str:
    if k and all(ch.isascii() and (ch.isalnum() or ch in "-_") for ch in k):
        return k
    return _value(k)


def save(values: dict) -> None:
    """Merge `values` into the existing config and write it atomically.

    Only str/bool/int values are written; others are ignored.
    """
    merged = load()
    merged.update({str(k): v for k, v in values.items() if isinstance(v, (str, bool, int))})
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(f"{_key(k)} = {_value(v)}\n" for k, v in merged.items())
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
