"""gpython: a reactive Python notebook in the terminal."""

import sys
from pathlib import Path

USAGE = """usage: gpython [--no-vim] [notebook.py]

With no file, opens an empty untitled notebook (ctrl+s or :w asks for a name).
Plain scripts are split into cells automatically; `# %%` markers are honoured."""


def main() -> None:
    args = sys.argv[1:]
    if "-h" in args or "--help" in args:
        print(USAGE)
        return
    vim = "--no-vim" not in args
    files = [a for a in args if not a.startswith("-")]
    path = Path(files[0]) if files else None

    from .app import GPython

    GPython(path, vim=vim).run()
