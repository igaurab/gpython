"""gpython: a reactive Python notebook in the terminal."""

import inspect
import sys
from pathlib import Path

USAGE = """usage: gpython [--no-vim] [--theme NAME] [notebook.py | notebook.ipynb]

With no file, opens an empty untitled notebook (ctrl+s or :w asks for a name).
Plain scripts are split into cells automatically; `# %%` markers and .ipynb
files are honoured.

  --no-vim       plain editing, no modal keys
  --theme NAME   colour theme (e.g. monokai, catppuccin, gruvbox)"""


def parse_args(argv: list[str]) -> dict:
    opts = {"vim": True, "theme": None, "path": None, "help": False}
    it = iter(argv)
    for arg in it:
        if arg in ("-h", "--help"):
            opts["help"] = True
        elif arg == "--no-vim":
            opts["vim"] = False
        elif arg == "--theme":
            opts["theme"] = next(it, None)
        elif arg.startswith("--theme="):
            opts["theme"] = arg.split("=", 1)[1]
        elif arg.startswith("-"):
            sys.exit(f"gpython: unknown option {arg}\n{USAGE}")
        else:
            opts["path"] = Path(arg)
    return opts


def main() -> None:
    opts = parse_args(sys.argv[1:])
    if opts["help"]:
        print(USAGE)
        return

    from .app import GPython

    # Only pass what was given on the command line, so saved config applies otherwise.
    kwargs = {} if opts["vim"] else {"vim": False}
    if opts["theme"] is not None:
        if "theme" in inspect.signature(GPython).parameters:
            kwargs["theme"] = opts["theme"]
        else:
            print("gpython: --theme is not supported by this build yet", file=sys.stderr)
    GPython(opts["path"], **kwargs).run()
