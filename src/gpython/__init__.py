"""gpython: a reactive Python notebook in the terminal."""

import sys
from pathlib import Path


def main() -> None:
    from .app import GPython

    path = Path(sys.argv[1] if len(sys.argv) > 1 else "notebook.py")
    GPython(path).run()
