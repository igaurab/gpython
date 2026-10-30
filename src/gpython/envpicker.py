"""Environment picker (f6, :env, or click the kernel chip in the header) and the env actions.

One bottom panel does everything about where the kernel runs:
- switch this notebook's kernel to another venv / conda env / interpreter,
- create a venv with `uv venv` or `python -m venv` (ipykernel is installed into it),
- install packages into the kernel's env (`uv pip install`) or add them to the project (`uv add`).

The same actions back the ex commands :env, :venv, :uv and :pip. Progress shows in the
bottom bar (the pane's kernel state); the result arrives as a notification.
"""

from __future__ import annotations

import asyncio
import shlex
import time
from pathlib import Path

from rich.table import Table
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from . import envs

KIND_TAGS = {
    "uv": ("uv", "green"), "venv": ("venv", "cyan"), "conda": ("conda", "magenta"),
    "builtin": ("built-in", "blue"), "uv-python": ("uv python", "yellow"), "system": ("python", "yellow"),
}  # fmt: skip
SECTIONS = {
    "project": "Project",
    "envs": "Environments",
    "interpreters": "Interpreters (picking one makes a venv from it if it has no ipykernel)",
}

_cache: dict[Path, list[envs.Env]] = {}  # last discovery per folder: shown at once, then refreshed


def _home(path: Path | str) -> str:
    text, home = str(path), str(Path.home())
    return "~" + text[len(home):] if text.startswith(home) else text


def _section(env: envs.Env, project: set[Path]) -> str:
    if env.prefix in project:
        return "project"
    return "interpreters" if env.kind in ("system", "uv-python") else "envs"


def _row(icon: str, name: str, version: str, tag: tuple[str, str], detail: str, current: bool = False) -> Table:
    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(width=2)
    grid.add_column(min_width=18, max_width=28, no_wrap=True)
    grid.add_column(width=8)
    grid.add_column(width=10)
    grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis", justify="right")
    grid.add_row(
        Text(icon, style="bold green" if current else "dim"),
        Text(name, style="bold" if current else ""),
        Text(version, style="dim"),
        Text(tag[0], style=tag[1]),
        Text(detail, style="dim"),
    )
    return grid


class EnvPicker(ModalScreen[tuple | None]):
    """Bottom panel: filter Input over actions and environments.

    Dismisses with one of: ("use", Env) · ("create", tool, folder, python) ·
    ("install", packages) · ("add", packages) · ("restart",) · None.
    """

    BINDINGS = [
        Binding("escape", "back", show=False),
        Binding("up", "move(-1)", show=False, priority=True),
        Binding("down", "move(1)", show=False, priority=True),
    ]

    def __init__(self, current: envs.Env, start: Path, title: str = "", ask: tuple | None = None) -> None:
        super().__init__()
        self.current, self.start, self.title_text = current, start, title
        self.root = envs.project_root(start)
        self.found: list[envs.Env] = _cache.get(start, [])
        self._ask: tuple | None = None  # ("create", tool, python) or ("install"|"add",): the Input asks for text
        self._first_ask = ask

    def compose(self) -> ComposeResult:
        with Vertical(id="env-picker"):
            yield Static(id="env-title")
            yield Input(placeholder="Filter, or type a path to a venv or python…  (enter use · esc close)")
            yield OptionList()

    def on_mount(self) -> None:
        if self._first_ask:
            self._start_ask(*self._first_ask)
        else:
            self._fill("")
        self.query_one(Input).focus()
        self.run_worker(self._discover(), group="discover")

    async def _discover(self) -> None:
        found = await asyncio.to_thread(envs.discover, self.start)
        _cache[self.start] = found
        if [e.python for e in found] != [e.python for e in self.found]:
            self.found = found
            if self._ask is None:
                self._fill(self.query_one(Input).value, keep=True)

    # ── list mode ────────────────────────────────────────────────────────

    def _title(self, text: str) -> None:
        self.query_one("#env-title", Static).update(Text.assemble((" Python environment ", "bold reverse"), f"  {text}"))

    def _fill(self, query: str, keep: bool = False) -> None:
        cur = self.current
        self._title(self.title_text or f"kernel runs in {cur.name} · {_home(cur.python)}")
        q = query.strip().lower()
        rows: list[Option] = []
        if not q:
            uv_ok = envs.uv() is not None
            rows.append(Option(Text("Actions", style="bold dim"), disabled=True))
            if uv_ok:
                rows.append(Option(_row("+", "New uv env", "", ("uv", "green"), "uv venv (fast, any Python version) + ipykernel → switch"), id="act:new-uv"))
            rows.append(Option(_row("+", "New venv", "", ("venv", "cyan"), "python -m venv + ipykernel → switch"), id="act:new-venv"))
            how = "uv pip install" if uv_ok else "pip install"
            rows.append(Option(_row("⇣", "Install packages", "", ("pkgs", "magenta"), f"{how} into {cur.name}"), id="act:install"))
            if uv_ok and (self.root / "pyproject.toml").exists():
                rows.append(Option(_row("⇣", "Add to project", "", ("uv add", "green"), f"uv add → {_home(self.root / 'pyproject.toml')}"), id="act:add"))
            rows.append(Option(_row("↻", "Restart kernel", "", ("kernel", "blue"), "same env, fresh process"), id="act:restart"))
        elif query.strip()[:1] in ("/", "~", ".") or "/" in query:
            path = Path(query.strip()).expanduser()
            if not path.is_absolute():
                path = self.start / path
            rows.append(Option(_row("→", f"Use {_home(path)}", "", ("path", "blue"), "a venv folder or a python executable"), id="path:" + str(path)))
        project = {e.prefix for e in envs.project_venvs(self.start)} if self.found else set()
        section = None
        for i, env in enumerate(self.found):
            hay = f"{env.name} {env.version} {env.kind} {env.python}".lower()
            if q and q not in hay:
                continue
            if (s := _section(env, project)) != section:
                section = s
                rows.append(Option(Text(SECTIONS[s], style="bold dim"), disabled=True))
            current = env.same(cur)
            detail = _home(env.prefix or env.python)
            if env.has_ipykernel is False:
                detail = "needs ipykernel (installed on pick) · " + detail
            tag = KIND_TAGS.get(env.kind, (env.kind, ""))
            rows.append(Option(_row("●" if current else " ", env.name, env.version or "?", tag, detail, current), id=f"env:{i}"))
        if not self.found:
            rows.append(Option(Text("  looking for environments…", style="dim italic"), disabled=True))
        options = self.query_one(OptionList)
        old = options.highlighted_option.id if keep and options.highlighted_option else None
        options.set_options(rows)
        ids = [o.id for o in rows]
        if old is not None and old in ids:
            options.highlighted = ids.index(old)
        else:
            options.highlighted = next((i for i, o in enumerate(rows) if not o.disabled), None)

    @on(Input.Changed)
    def _filter(self, event: Input.Changed) -> None:
        if self._ask is None:
            self._fill(event.value)

    def action_move(self, delta: int) -> None:
        if self._ask is None:
            options = self.query_one(OptionList)
            options.action_cursor_down() if delta > 0 else options.action_cursor_up()

    @on(Input.Submitted)
    def _submit(self, event: Input.Submitted) -> None:
        event.stop()
        if self._ask is not None:
            self._answer(event.value)
            return
        options = self.query_one(OptionList)
        if (option := options.highlighted_option) is not None:
            self._choose(option.id)

    @on(OptionList.OptionSelected)
    def _selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self._choose(event.option.id)

    def _choose(self, oid: str | None) -> None:
        if not oid:
            return
        kind, _, rest = oid.partition(":")
        if kind == "env":
            self.dismiss(("use", self.found[int(rest)]))
        elif kind == "path":
            self.dismiss(("use", envs.from_python(Path(rest), self.found)))
        elif rest == "new-uv":
            self._start_ask("create", "uv", None)
        elif rest == "new-venv":
            self._start_ask("create", "venv", None)
        elif rest in ("install", "add"):
            self._start_ask(rest)
        elif rest == "restart":
            self.dismiss(("restart",))

    # ── ask mode: the Input takes a folder or package names ─────────────

    def _start_ask(self, what: str, tool: str | None = None, python: str | None = None) -> None:
        self._ask = (what, tool, python)
        self.query_one("#env-picker").add_class("-ask")
        field = self.query_one(Input)
        options = self.query_one(OptionList)
        options.clear_options()
        if what == "create":
            folder = ".venv" if not (self.start / ".venv").exists() else ".venv-" + (python or "2").replace(".", "")
            field.value = folder + (f" {python}" if python else "")
            field.placeholder = "folder [python version, e.g. 3.12]"
            cmd = "uv venv" if tool == "uv" else "python -m venv"
            self._title(f"new env with {cmd} — folder (relative to {_home(self.start)}) and optional Python version")
            lines = ["enter creates it, installs ipykernel and switches the kernel to it · esc back"]
            if tool == "uv":
                lines.append("versions uv can fetch: 3.10 … 3.14 (downloaded on demand)")
        else:
            field.value = ""
            field.placeholder = "package names, e.g. numpy pandas 'polars>=1'"
            if what == "add":
                self._title(f"uv add → {_home(self.root / 'pyproject.toml')}, synced into {self.current.name}")
            else:
                self._title(f"install into {self.current.name} ({_home(self.current.python)})")
            lines = ["enter installs · new imports work without restarting the kernel · esc back"]
            if self.current.kind == "builtin":
                lines.insert(0, "note: this is gpython's own environment; a project venv (New uv env) keeps them apart")
        for line in lines:
            options.add_option(Option(Text("  " + line, style="dim"), disabled=True))
        field.cursor_position = len(field.value)

    def _answer(self, value: str) -> None:
        what, tool, _python = self._ask
        try:
            words = shlex.split(value)
        except ValueError:
            words = value.split()
        if not words:
            return
        if what == "create":
            self.dismiss(("create", tool, words[0], words[1] if len(words) > 1 else None))
        else:
            self.dismiss((what, words))

    def action_back(self) -> None:
        if self._ask is not None and self._first_ask is None:
            self._ask = None
            self.query_one("#env-picker").remove_class("-ask")
            field = self.query_one(Input)
            field.value = ""
            field.placeholder = "Filter, or type a path to a venv or python…  (enter use · esc close)"
            self._fill("")
        else:
            self.dismiss(None)


class KernelChip(Static):
    """The header chip: 'Python 3.13.7 · .venv'. Clicking it opens the env picker."""

    def on_click(self) -> None:
        self.app.action_pick_env()


# ── actions (shared by the picker, :env, :venv, :uv and :pip) ────────────


def _progress(pane, label: str):
    """An on_line callback: the latest output line in the bottom bar, at most ~10 redraws a second."""
    last = [0.0]

    def on_line(line: str) -> None:
        now = time.monotonic()
        if now - last[0] >= 0.1:
            last[0] = now
            pane.set_kernel_state(f"{label} · {line.strip()[:70]}")

    pane.set_kernel_state(f"{label}…")
    return on_line


def _base(pane) -> Path:
    return pane.path.resolve().parent if pane.path is not None else Path.cwd()


async def use(pane, env: envs.Env) -> None:
    """Switch the pane's kernel to env, installing ipykernel into it first if it is a venv without it."""
    if env.same(pane.kernel.env):
        pane.notify(f"Already running in {env.name}")
        return
    state = pane.kernel_state
    try:
        await envs.ensure_ipykernel(env, _progress(pane, f"installing ipykernel into {env.name}"))
    except envs.EnvError:
        pane.set_kernel_state(state)
        if env.prefix is None:  # a bare interpreter: offer a venv made from it
            pane.app.push_screen(
                EnvPicker(pane.kernel.env, _base(pane), ask=("create", "uv" if envs.uv() else "venv", str(env.python))),
                lambda choice: handle(pane, choice),
            )
            pane.notify(f"{env.name} has no ipykernel: make a venv from it (enter), or esc", timeout=6)
        else:
            pane.notify(f"Could not install ipykernel into {env.name}", severity="error")
        return
    await pane.switch_env(env)


async def create(pane, tool: str, folder: str, python: str | None = None) -> None:
    path = Path(folder).expanduser()
    if not path.is_absolute():
        path = _base(pane) / path
    if (existing := envs.venv_at(path)) is not None:
        pane.notify(f"{_home(path)} already is an environment: switching to it")
        await use(pane, existing)
        return
    if path.exists() and any(path.iterdir()):
        pane.notify(f"{_home(path)} exists and is not empty", severity="error")
        return
    state = pane.kernel_state
    cmd = "uv venv" if tool == "uv" and envs.uv() else "python -m venv"
    try:
        env = await envs.create(path, tool, python, _progress(pane, f"{cmd} {path.name}"))
    except envs.EnvError as error:
        pane.set_kernel_state(state)
        pane.notify(f"{cmd} failed: {error}", severity="error", timeout=10)
        return
    _cache.clear()
    pane.notify(f"Created {env.name} (Python {env.version}, {cmd})")
    await pane.switch_env(env)


async def install(pane, packages: list[str], project: bool = False) -> None:
    """Install into the kernel's env; with project=True, `uv add` them to pyproject.toml too."""
    env = pane.kernel.env
    names = " ".join(packages)
    state = pane.kernel_state
    on_line = _progress(pane, f"{'uv add' if project else 'installing'} {names}")
    try:
        if project and not await envs.add(env, packages, _base(pane), on_line):
            pane.notify("No pyproject.toml here: installing into the env only")
            project = False
        if not project:
            await envs.install(env, packages, on_line)
    except envs.EnvError as error:
        pane.notify(f"Install failed: {error}", severity="error", timeout=10)
        return
    finally:
        pane.set_kernel_state(state)
    # New packages import without a restart once the import system forgets its cached listings.
    if pane.kernel.ready.is_set():
        await pane.kernel._execute("__import__('importlib').invalidate_caches()", None, store_history=False)
    pane.notify(f"{'Added' if project else 'Installed'} {names} → {env.name}")


async def run_tool(pane, argv: list[str], title: str) -> None:
    """Any other uv/pip command: output in a notification."""
    lines: list[str] = []
    state = pane.kernel_state
    on_line = _progress(pane, title)

    def collect(line: str) -> None:
        lines.append(line)
        on_line(line)

    code = await envs.run(argv, collect, cwd=envs.project_root(_base(pane)), env=envs.kernel_env(pane.kernel.env))
    pane.set_kernel_state(state)
    pane.notify("\n".join(lines[-12:]) or "(no output)", title=title, severity="error" if code else "information", timeout=10)


def handle(pane, choice: tuple | None) -> None:
    """The picker's result → a background worker on the app (survives the picker closing)."""
    if not choice:
        return
    what, *args = choice
    if what == "use":
        work = use(pane, args[0])
    elif what == "create":
        work = create(pane, *args)
    elif what in ("install", "add"):
        work = install(pane, args[0], project=what == "add")
    elif what == "restart":
        work = pane.restart_kernel()
    else:
        return
    pane.app.run_worker(work, group="env")


def open_picker(pane, ask: tuple | None = None) -> None:
    if not isinstance(pane.app.screen, EnvPicker):
        pane.app.push_screen(EnvPicker(pane.kernel.env, _base(pane), ask=ask), lambda choice: handle(pane, choice))


# ── ex commands ──────────────────────────────────────────────────────────


def _words(arg: str) -> list[str]:
    try:
        return shlex.split(arg)
    except ValueError:
        return arg.split()


def ex_env(pane, arg: str = "") -> None:
    """:env opens the picker; :env NAME|PATH switches to a matching env."""
    arg = arg.strip()
    if not arg:
        open_picker(pane)
        return
    path = Path(arg).expanduser()
    if path.exists():
        handle(pane, ("use", envs.from_python(path if path.is_absolute() else _base(pane) / path)))
        return
    found = envs.discover(_base(pane))
    match = next((e for e in found if e.name == arg), None) or next((e for e in found if arg in e.name), None)
    if match is None:
        pane.notify(f"No environment matches {arg!r} (f6 lists them)", severity="error")
    else:
        handle(pane, ("use", match))


def ex_venv(pane, arg: str = "", tool: str = "venv") -> None:
    """:venv [folder] [python] — python -m venv; with no folder the picker asks."""
    words = _words(arg)
    if not words:
        open_picker(pane, ask=("create", tool, None))
    else:
        handle(pane, ("create", tool, words[0], words[1] if len(words) > 1 else None))


def ex_pip(pane, arg: str = "") -> None:
    """:pip numpy · :pip install numpy · :pip uninstall numpy · :pip list — for the kernel's env."""
    words = _words(arg)
    if not words:
        open_picker(pane, ask=("install",))
        return
    if words[0] == "install":
        words = words[1:]
    elif words[0] in ("uninstall", "list", "freeze", "show", "tree", "check"):
        env = pane.kernel.env
        if envs.uv():
            argv = [envs.uv(), "pip", words[0], "--python", str(env.python), *words[1:]]
        else:
            argv = [str(env.python), "-m", "pip", *words, *(["-y"] if words[0] == "uninstall" else [])]
        pane.app.run_worker(run_tool(pane, argv, f"pip {words[0]}"), group="env")
        return
    if words:
        handle(pane, ("install", words))


def ex_uv(pane, arg: str = "") -> None:
    """:uv add pkg · :uv pip install pkg · :uv venv [folder] [python] · :uv <anything> (in the project)."""
    words = _words(arg)
    if not envs.uv():
        pane.notify("uv is not installed: https://docs.astral.sh/uv/", severity="error")
        return
    if not words:
        open_picker(pane)
    elif words[0] == "venv":
        ex_venv(pane, " ".join(shlex.quote(w) for w in words[1:]), tool="uv")
    elif words[0] == "add" and len(words) > 1 and not any(w.startswith("-") for w in words[1:]):
        handle(pane, ("add", words[1:]))
    elif words[0] == "pip":
        ex_pip(pane, " ".join(shlex.quote(w) for w in words[1:]))
    else:
        pane.app.run_worker(run_tool(pane, [envs.uv(), *words], f"uv {words[0]}"), group="env")
