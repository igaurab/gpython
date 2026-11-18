"""gpython: a reactive Python notebook in the terminal."""

import inspect
import os
import sys
from pathlib import Path

USAGE = """usage: gpython [--version] [--no-vim] [--theme NAME] [--groups] [uv flags] [FILE | DIR ...]
       gpython --update [stable|dev]
       gpython --scratchpad DIR
       gpython --install-completions
       gpython --completions bash|zsh|fish|powershell

With no file, opens an empty untitled notebook (ctrl+s or :w asks for a name).
A name with no folder, such as `:w notes.py`, is saved in the scratchpad folder when
one is set, and in the folder gpython was started in when none is set.
Several files open in tabs (gt / gT switch); a directory opens the file
explorer there (ctrl+b toggles it). Plain scripts are split into cells
automatically; `# %%` markers and .ipynb files are honoured.

  -v, --version  print the version and exit
  --no-vim       plain editing, no modal keys
  --theme NAME   colour theme (e.g. monokai, catppuccin, gruvbox)
  --groups       print which cells of each FILE may run at the same time (:set concurrent) and exit
  --update [stable|dev]   get the newest gpython and exit, with the tool that installed it (uv,
                     pipx or pip). stable, the default, is the newest release on PyPI; dev is
                     the newest code on GitHub. In gpython: :update
  --scratchpad DIR   remember DIR as the scratchpad folder for every later launch and exit
                     (`--scratchpad .` is the current folder, `--scratchpad ""` forgets it)
  --install-completions   set up tab completion (gpy --th + tab) for your shell and exit. The shell
                     comes from $SHELL; name another with --install-completions=zsh. Run it again
                     after an upgrade to pick up new options
  --completions SHELL   print the tab completion script for bash, zsh, fish or powershell and exit,
                     to set it up by hand:
                     zsh:   gpython --completions zsh > ~/.zfunc/_gpython   (a folder in $fpath)
                     bash:  gpython --completions bash > ~/.gpython-completion.bash, then source it in ~/.bashrc
                     fish:  gpython --completions fish > ~/.config/fish/conf.d/gpython.fish
                     powershell:  gpython --completions powershell >> $PROFILE

In a uv project (pyproject.toml) the kernel runs in the project's environment through
`uv run --with ipykernel`; ipykernel comes from uv's cache, never into the project.
These flags mean what they mean to `uv run` and apply to this launch:

  --extra NAME   install the project's optional dependencies NAME (repeatable)
  --all-extras   all of them
  --group NAME   a dependency group (repeatable); --all-groups for all
  --with PKG     a package for this session only, outside the project (repeatable)
  --no-sync      use the environment as it is, install nothing"""

UV_VALUE_FLAGS = ("--extra", "--group", "--with")
UV_FLAGS = ("--all-extras", "--all-groups", "--no-sync")
SETTING_FLAGS = ("--scratchpad", "--completions")


SWITCHES = {
    "-h": ("help", True), "--help": ("help", True),
    "-v": ("version", True), "-V": ("version", True), "--version": ("version", True),
    "--groups": ("groups", True), "--no-vim": ("vim", False),
}


def _value_after(name: str, eq: str, value: str, rest) -> str:
    """The value of the option `name`: after its `=`, or the next argument. Exits when there is none."""
    found = value if eq else next(rest, None)
    if found is None:
        sys.exit(f"gpython: {name} needs a value\n{USAGE}")
    return found


def _with_update_channel(argv: list[str]) -> list[str]:
    """`--update dev` as `--update=dev`: the word after --update is its channel, never a file."""
    argv = list(argv)
    if "--update" in argv:
        at = argv.index("--update")
        if at + 1 < len(argv) and not argv[at + 1].startswith("-"):
            argv[at : at + 2] = [f"--update={argv[at + 1]}"]
    return argv


def parse_args(argv: list[str]) -> dict:
    opts = {"vim": True, "theme": None, "path": None, "paths": [], "help": False, "version": False, "groups": False, "uv": [], "scratchpad": None, "completions": None, "install_completions": None, "update": None}
    it = iter(_with_update_channel(argv))
    for arg in it:
        name, eq, value = arg.partition("=")
        if name in UV_VALUE_FLAGS:
            value = _value_after(name, eq, value, it)
            if not value:
                sys.exit(f"gpython: {name} needs a value\n{USAGE}")
            opts["uv"].append(f"{name}={value}")
        elif name in SETTING_FLAGS:
            opts[name[2:]] = _value_after(name, eq, value, it)
        elif name == "--install-completions":
            opts["install_completions"] = value
        elif name == "--update":
            opts["update"] = value
        elif arg in UV_FLAGS:
            opts["uv"].append(arg)
        elif arg in SWITCHES:
            key, state = SWITCHES[arg]
            opts[key] = state
        elif name == "--theme":
            opts["theme"] = value if eq else next(it, None)
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


def set_scratchpad(path: str) -> int:
    """`gpython --scratchpad DIR`: remember DIR as the folder for new notebooks."""
    from . import scratchpad

    try:
        folder = scratchpad.set_folder(path)
    except OSError as error:
        print(f"gpython: cannot use {path} as the scratchpad folder: {error}", file=sys.stderr)
        return 1
    if folder is None:
        print("gpython: no scratchpad folder. A new notebook is saved in the folder gpython is started in")
    else:
        print(f"gpython: a new notebook is now saved in {folder}")
    return 0


def print_completions(shell: str) -> int:
    """`gpython --completions SHELL`: the tab completion script for that shell."""
    from . import completions

    name = completions.shell_name(shell)
    if name is None:
        print(f"gpython: --completions takes one of: {', '.join(completions.SHELLS)}", file=sys.stderr)
        return 2
    sys.stdout.write(completions.script(name))
    return 0


def install_completions(shell: str) -> int:
    """`gpython --install-completions`: write the script for the shell and make the shell load it."""
    from . import completions

    name = completions.shell_name(shell) if shell else completions.detected_shell()
    if name is None:
        asked = shell or os.environ.get("SHELL") or "this shell"
        choices = ", ".join(completions.SHELLS)
        print(f"gpython: no completions for {asked}. Name one of {choices}: gpython --install-completions=zsh", file=sys.stderr)
        return 2
    try:
        done = completions.install(name)
    except completions.CannotInstall as error:
        print(f"gpython: {error}", file=sys.stderr)
        return 1
    print(f"gpython: the {name} completions are in {done.script}")
    if done.startup is not None:
        print(f"gpython: {'added a line to' if done.added else 'it is loaded already by'} {done.startup}")
    print("gpython: open a new terminal, then try: gpy --th + tab")
    return 0


def run_update(word: str) -> int:
    """`gpython --update [stable|dev]`: install the newest gpython with the tool that installed this one."""
    from . import update

    channel = update.channel_of(word)
    if channel is None:
        print(f"gpython: --update takes stable (the newest release) or dev (the newest code on GitHub), not {word}", file=sys.stderr)
        return 2
    outcome = update.run(channel, installed_version())
    print(f"gpython: {outcome.message}", file=sys.stdout if outcome.ok else sys.stderr)
    return 0 if outcome.ok else 1


def installed_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("gpynb")
    except PackageNotFoundError:
        return "unknown"


SETUP_COMMANDS = (
    ("install_completions", install_completions),
    ("completions", print_completions),
    ("scratchpad", set_scratchpad),
    ("update", run_update),
)


def main() -> None:
    opts = parse_args(sys.argv[1:])
    if opts["help"]:
        print(USAGE)
        return
    if opts["version"]:
        print(f"gpython {installed_version()}")
        return
    for key, command in SETUP_COMMANDS:
        if opts[key] is not None:
            sys.exit(command(opts[key]))
    if opts["groups"]:
        sys.exit(print_groups(opts["paths"]))

    os.environ.setdefault("TEXTUAL_FPS", "120")
    from . import envs, prespawn

    envs.launch_args = tuple(opts["uv"])

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
