"""`gpython --update` and `:update`: get a newer gpython with the tool that installed this one.

Two channels: "stable" is the newest release on PyPI (the default), "dev" is the newest code
on the main branch on GitHub. The install is one of four kinds, found from the environment
gpython runs in: a `uv tool` (it has a `uv-receipt.toml`), a pipx app (`pipx_metadata.json`),
an editable install of a checkout (updated with git, not from here), or a plain pip install.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, distribution
from importlib.util import find_spec
from pathlib import Path

PACKAGE = "gpynb"
REPOSITORY = "git+https://github.com/igaurab/gpython"
STABLE, DEV = "stable", "dev"
CHANNELS = {"": STABLE, "stable": STABLE, "release": STABLE, "pypi": STABLE, "dev": DEV, "main": DEV, "github": DEV}
VERSION_CODE = f"from importlib.metadata import version; print(version('{PACKAGE}'))"


@dataclass
class Outcome:
    ok: bool
    message: str


def channel_of(word: str) -> str | None:
    """"stable" or "dev" for the word after --update / :update; None for a word that is neither."""
    return CHANNELS.get(word.strip().lower())


def checkout() -> Path | None:
    """The folder of the source checkout when gpython is an editable install of one."""
    try:
        text = distribution(PACKAGE).read_text("direct_url.json")
    except PackageNotFoundError:
        return None
    if not text:
        return None
    origin = json.loads(text)
    if not origin.get("dir_info", {}).get("editable"):
        return None
    return Path(origin.get("url", "").removeprefix("file://"))


def install_kind(prefix: Path | None = None) -> str:
    """How this gpython was installed: "uv-tool", "pipx" or "pip"."""
    prefix = Path(sys.prefix) if prefix is None else prefix
    if (prefix / "uv-receipt.toml").exists():
        return "uv-tool"
    if (prefix / "pipx_metadata.json").exists():
        return "pipx"
    return "pip"


def command(kind: str, channel: str, has_pip: bool | None = None) -> list[str] | None:
    """The command that installs the newest gpython of `channel`; None when its tool is missing."""
    source = PACKAGE if channel == STABLE else REPOSITORY
    if kind == "uv-tool":
        return ["uv", "tool", "install", "--force", "--refresh", source] if shutil.which("uv") else None
    if kind == "pipx":
        return ["pipx", "install", "--force", source] if shutil.which("pipx") else None
    has_pip = find_spec("pip") is not None if has_pip is None else has_pip
    if has_pip:
        return [sys.executable, "-m", "pip", "install", "--upgrade", source]
    if shutil.which("uv"):
        return ["uv", "pip", "install", "--python", sys.executable, "--upgrade", "--refresh", source]
    return None


def version_now() -> str:
    """The installed version as a new Python process sees it (this one loaded the old files)."""
    done = subprocess.run([sys.executable, "-c", VERSION_CODE], capture_output=True, text=True)
    return done.stdout.strip() or "unknown"


def summary(before: str, after: str, channel: str) -> str:
    if channel == DEV:
        return f"gpython has the newest code from GitHub now (version {after}). Start gpython again to use it"
    if before == after:
        return f"gpython {after} is the newest release"
    return f"gpython {before} is now {after}. Start gpython again to use it"


def run(channel: str, before: str, quiet: bool = False) -> Outcome:
    """Update gpython. `quiet` keeps the installer's output off the terminal (the app is on it)."""
    folder = checkout()
    if folder is not None:
        return Outcome(False, f"This gpython runs from the source folder {folder}. Update it there with: git pull")
    kind = install_kind()
    argv = command(kind, channel)
    if argv is None:
        tool = {"uv-tool": "uv", "pipx": "pipx"}.get(kind, "pip or uv")
        return Outcome(False, f"gpython was installed with {tool}, which is not on PATH now")
    try:
        done = subprocess.run(argv, capture_output=quiet, text=True)
    except OSError as error:
        return Outcome(False, f"Could not run {argv[0]}: {error}")
    if done.returncode != 0:
        detail = (done.stderr or "").strip().splitlines()[-1:] if quiet else []
        return Outcome(False, " ".join([f"The update did not finish ({' '.join(argv[:3])} failed).", *detail]))
    return Outcome(True, summary(before, version_now(), channel))
