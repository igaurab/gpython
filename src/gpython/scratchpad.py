"""The scratchpad folder: where an untitled notebook is saved when its new name has no folder.

`gpython --scratchpad PATH` stores the folder in the config file as `scratchpad`. With no
folder stored, a name is read from the folder gpython was started in, as before.
"""

from __future__ import annotations

from pathlib import Path

from . import config

CONFIG_KEY = "scratchpad"
HERE_PREFIXES = ("./", "../")


def folder() -> Path | None:
    """The stored scratchpad folder, or None when none is set."""
    stored = config.load().get(CONFIG_KEY)
    return Path(stored) if isinstance(stored, str) and stored else None


def set_folder(path: str) -> Path | None:
    """Store `path` (made absolute, created when missing) as the scratchpad folder.
    An empty `path` clears the setting. Returns the stored folder."""
    if not path:
        config.save({CONFIG_KEY: ""})
        return None
    target = Path(path).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    config.save({CONFIG_KEY: str(target)})
    return target


def names_its_own_folder(name: str) -> bool:
    """True when `name` says where it goes: an absolute path, `~/...`, `./...` or `../...`."""
    return name.startswith(HERE_PREFIXES) or Path(name).expanduser().is_absolute()


def resolve(name: str) -> Path:
    """The file an untitled notebook is saved to under the typed `name`."""
    path = Path(name).expanduser()
    base = None if names_its_own_folder(name) else folder()
    return path if base is None else base / path


def shown(path: Path) -> str:
    """`path` as text, with the home folder written as ~."""
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)
