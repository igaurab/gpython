"""gpython: a reactive Python notebook in the terminal."""

import inspect
import os
import sys
from pathlib import Path

USAGE = """usage: gpython [--version] [--no-vim] [--theme NAME] [--groups] [FILE | DIR ...]

With no file, opens an empty untitled notebook (ctrl+s or :w asks for a name).
Several files open in tabs (gt / gT switch); a directory opens the file
explorer there (ctrl+b toggles it). Plain scripts are split into cells
automatically; `# %%` markers and .ipynb files are honoured.

  -v, --version  print the version and exit
  --no-vim       plain editing, no modal keys
  --theme NAME   colour theme (e.g. monokai, catppuccin, gruvbox)
  --groups       print which cells of each FILE may run at the same time (:set concurrent) and exit"""


def parse_args(argv: list[str]) -> dict:
    opts = {"vim": True, "theme": None, "path": None, "paths": [], "help": False, "version": False, "groups": False}
    it = iter(argv)
    for arg in it:
        if arg in ("-h", "--help"):
            opts["help"] = True
        elif arg in ("-v", "-V", "--version"):
            opts["version"] = True
        elif arg == "--groups":
            opts["groups"] = True
        elif arg == "--no-vim":
            opts["vim"] = False
        elif arg == "--theme":
            opts["theme"] = next(it, None)
        elif arg.startswith("--theme="):
            opts["theme"] = arg.split("=", 1)[1]
        elif arg.startswith("-"):
            sys.exit(f"gpython: unknown option {arg}\n{USAGE}")
        else:
            opts["paths"].append(Path(arg))
    opts["path"] = opts["paths"][0] if opts["paths"] else None
    return opts


def print_groups(paths: list[Path]) -> int:
    """`gpython --groups FILE ...`: the groups concurrent mode would run, without starting a kernel."""
    from . import config
    from .concurrency import default_cap, report
    from .notebook import load_cells

    if not paths:
        print("gpython --groups needs a notebook file", file=sys.stderr)
        return 2
    cap = config.load().get("concurrency") or default_cap()
    for n, path in enumerate(paths):
        if n:
            print()
        if not path.is_file():
            print(f"{path}: not a file", file=sys.stderr)
            return 1
        specs, _ = load_cells(path)
        cells = {str(i): s.source for i, s in enumerate(specs, 1) if s.kind == "code"}
        if len(paths) > 1:
            print(f"== {path}")
        print(report(cells, {cid: f"c{cid}" for cid in cells}, int(cap)))
    return 0


def installed_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("gpynb")
    except PackageNotFoundError:
        return "unknown"


def main() -> None:
    opts = parse_args(sys.argv[1:])
    if opts["help"]:
        print(USAGE)
        return
    if opts["version"]:
        print(f"gpython {installed_version()}")
        return
    if opts["groups"]:
        sys.exit(print_groups(opts["paths"]))

    os.environ.setdefault("TEXTUAL_FPS", "120")
    from . import prespawn

    prespawn.start(opts["path"])
    from .app import GPython

    kwargs = {} if opts["vim"] else {"vim": False}
    if opts["theme"] is not None:
        if "theme" in inspect.signature(GPython).parameters:
            kwargs["theme"] = opts["theme"]
        else:
            print("gpython: --theme is not supported by this build yet", file=sys.stderr)
    if len(opts["paths"]) > 1:
        kwargs["paths"] = opts["paths"][1:]
    GPython(opts["path"], **kwargs).run()
