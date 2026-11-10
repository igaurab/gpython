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

from . import config, envs, packages

KIND_TAGS = {
    "uv": ("uv", "green"), "venv": ("venv", "cyan"), "conda": ("conda", "magenta"),
    "builtin": ("built-in", "blue"), "uv-python": ("uv python", "yellow"), "system": ("python", "yellow"),
    "uv-project": ("uv run", "green"),
}
ASK_MODES = ("install", "add", "sync")
SECTIONS = {
    "project": "Project",
    "envs": "Environments",
    "interpreters": "Python interpreters (if one has no ipykernel, gpython makes an environment from it)",
}

_cache: dict[Path, list[envs.Env]] = {}


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
    ("install", packages) · ("add", packages) · ("sync", flags) · ("restart",) · None.
    """

    BINDINGS = [
        Binding("escape", "back", show=False),
        Binding("up", "move(-1)", show=False, priority=True),
        Binding("down", "move(1)", show=False, priority=True),
    ]

    def __init__(
        self, current: envs.Env, start: Path, title: str = "", ask: tuple | None = None, prefill: str = "", after: str = "",
        sync: tuple[list[str], str] | None = None,
    ) -> None:
        super().__init__()
        self.current, self.start, self.title_text, self.prefill, self.after_text = current, start, title, prefill, after
        self.sync = sync
        self.root = envs.project_root(start)
        self.found: list[envs.Env] = _cache.get(start, [])
        self._ask: tuple | None = None
        self._first_ask = ask

    def compose(self) -> ComposeResult:
        with Vertical(id="env-picker"):
            yield Static(id="env-title")
            yield Input(placeholder="Type to filter, or type the path to an environment or Python  (enter: use · esc: close)")
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


    def _title(self, text: str) -> None:
        self.query_one("#env-title", Static).update(Text.assemble((" Python environment ", "bold reverse"), f"  {text}"))

    def _fill(self, query: str, keep: bool = False) -> None:
        cur = self.current
        self._title(self.title_text or f"The kernel uses {cur.name} · {_home(cur.python)}")
        q = query.strip().lower()
        rows: list[Option] = []
        if not q:
            uv_ok = envs.uv() is not None
            rows.append(Option(Text("Actions", style="bold dim"), disabled=True))
            if uv_ok:
                rows.append(Option(_row("+", "New uv environment", "", ("uv", "green"), "make it with uv (fast), install ipykernel, use it"), id="act:new-uv"))
            rows.append(Option(_row("+", "New venv environment", "", ("venv", "cyan"), "make it with python -m venv, install ipykernel, use it"), id="act:new-venv"))
            how = "uv pip install" if uv_ok else "pip install"
            rows.append(Option(_row("⇣", "Install packages", "", ("pkgs", "magenta"), f"{how} into {cur.name}"), id="act:install"))
            if uv_ok and (self.root / "pyproject.toml").exists():
                rows.append(Option(_row("⇣", "Add to the project", "", ("uv add", "green"), f"uv add: write to {_home(self.root / 'pyproject.toml')}"), id="act:add"))
            rows.append(Option(_row("↻", "Restart the kernel", "", ("kernel", "blue"), "same environment, new process"), id="act:restart"))
        elif query.strip()[:1] in ("/", "~", ".") or "/" in query:
            path = Path(query.strip()).expanduser()
            if not path.is_absolute():
                path = self.start / path
            rows.append(Option(_row("→", f"Use {_home(path)}", "", ("path", "blue"), "an environment folder or a Python program"), id="path:" + str(path)))
        project = {e.prefix for e in envs.project_venvs(self.start)} if self.found else set()
        uv_venv = envs.project_env_dir(self.root) if envs.is_uv_project(self.root) else None
        section = None
        for i, env in enumerate(self.found):
            hay = f"{env.name} {env.version} {env.kind} {env.python}".lower()
            if q and q not in hay:
                continue
            if (s := _section(env, project)) != section:
                section = s
                rows.append(Option(Text(SECTIONS[s], style="bold dim"), disabled=True))
            current = env.same(cur) or (cur.kind == "uv-project" and env.prefix == cur.prefix)
            detail = _home(env.prefix or env.python)
            if current and cur.kind == "uv-project":
                detail = f"uv run --with ipykernel {' '.join(cur.uv_args)}".rstrip() + " · " + detail
            elif env.has_ipykernel is False and env.prefix == uv_venv:
                detail = "uv project: runs through uv run --with ipykernel, nothing installed into it · " + detail
            elif env.has_ipykernel is False:
                detail = "no ipykernel: gpython installs it when you pick it · " + detail
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
        elif self._ask[0] in ASK_MODES:
            self._ask_title()

    @on(OptionList.OptionHighlighted)
    def _highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if self._ask is not None and self._ask[0] in ASK_MODES:
            options = self.query_one(OptionList)
            for row in self._mode_rows(self._mode()):
                options.replace_option_prompt(row.id, row.prompt)
            self._ask_title()

    def action_move(self, delta: int) -> None:
        if self._ask is None or self._ask[0] in ASK_MODES:
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
        elif kind == "mode":
            self._answer(self.query_one(Input).value)
        elif rest == "restart":
            self.dismiss(("restart",))

    def can_add(self) -> bool:
        """`uv add` is on offer when uv and a pyproject.toml at the project root exist."""
        return envs.uv() is not None and (self.root / "pyproject.toml").exists()

    def _mode_rows(self, chosen: str) -> list[Option]:
        """How to install: `uv add` to the project (when it has one) or into the env only.
        The chosen row carries the ● marker, like the current env in the list."""
        env = self.current
        rows = []
        if self.sync is not None:
            flags, reason = self.sync
            on = chosen == "sync"
            detail = f"declared in {reason}: install it into {env.name}, pyproject.toml unchanged"
            rows.append(Option(_row("●" if on else " ", "uv sync", "", ("project", "green"), detail, on), id="mode:sync"))
        if self.can_add():
            detail = f"write to {_home(self.root / 'pyproject.toml')}, then install into {env.name}"
            on = chosen == "add"
            rows.append(Option(_row("●" if on else " ", "uv add", "", ("project", "green"), detail, on), id="mode:add"))
        on = chosen == "install"
        detail = f"into {env.name} ({_home(env.python)})"
        rows.append(Option(_row("●" if on else " ", self._pip(), "", ("kernel", "magenta"), detail, on), id="mode:install"))
        return rows

    @staticmethod
    def _pip() -> str:
        return "uv pip install" if envs.uv() else "pip install"

    def _command(self) -> str:
        """What enter runs now: the highlighted way with the typed packages, and where it lands."""
        names = self.query_one(Input).value.strip() or "…"
        if self._mode() == "sync" and self.sync is not None:
            return " ".join(["uv sync --inexact", *self.sync[0]]) + f" → {self.current.name}"
        if self._mode() == "add":
            return f"uv add {names} → {_home(self.root / 'pyproject.toml')}"
        return f"{self._pip()} {names} → {self.current.name}"

    def _ask_title(self) -> None:
        lead = self.title_text or "Install packages"
        switch = " · ↑↓: change the way" if self.can_add() or self.sync is not None else ""
        back = "go back" if self._first_ask is None else "skip" if self.after_text else "close"
        self._title(f"{lead} · enter: {self._command()}{self.after_text}{switch} · esc: {back}")


    def _start_ask(self, what: str, tool: str | None = None, python: str | None = None) -> None:
        self._ask = (what, tool, python)
        self.query_one("#env-picker").add_class("-ask")
        field = self.query_one(Input)
        options = self.query_one(OptionList)
        options.clear_options()
        if what == "create":
            folder = ".venv" if not (self.start / ".venv").exists() else ".venv-" + (python or "2").replace(".", "")
            field.value = folder + (f" {python}" if python else "")
            field.placeholder = "folder [Python version, for example 3.12]"
            cmd = "uv venv" if tool == "uv" else "python -m venv"
            self._title(f"Make a new environment with {cmd}. Type a folder name (relative to {_home(self.start)}). You can add a Python version.")
            lines = ["enter: make it, install ipykernel and use it for the kernel · esc: go back"]
            if tool == "uv":
                lines.append("Python versions uv can download: 3.10 to 3.14")
        else:
            field.value = self.prefill
            field.placeholder = "package names, for example numpy pandas 'polars>=1'"
            rows = self._mode_rows(what)
            for row in rows:
                options.add_option(row)
            options.highlighted = next((i for i, row in enumerate(rows) if row.id == f"mode:{what}"), 0)
            self._ask_title()
            lines = ["You do not need to restart the kernel after the install."]
            if len(rows) > 1:
                lines[0] = "↑↓: pick the way to install. gpython keeps your choice (install_mode in the config). " + lines[0]
            if self.current.kind == "builtin":
                lines.insert(0, "Note: this is the gpython environment. Make a project environment to keep your packages apart.")
        for line in lines:
            options.add_option(Option(Text("  " + line, style="dim"), disabled=True))
        field.cursor_position = len(field.value)

    def _mode(self) -> str:
        """The highlighted install row's mode, "add" or "install"."""
        option = self.query_one(OptionList).highlighted_option
        if option is not None and option.id and option.id.startswith("mode:"):
            return option.id.partition(":")[2]
        return self._ask[0]

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
            return
        mode = self._mode()
        if mode == "sync" and self.sync is not None:
            self.dismiss(("sync", self.sync[0]))
            return
        if self.can_add() and mode != config.load().get("install_mode"):
            config.save({"install_mode": mode})
        self.dismiss((mode, words))

    def action_back(self) -> None:
        if self._ask is not None and self._first_ask is None:
            self._ask = None
            self.query_one("#env-picker").remove_class("-ask")
            field = self.query_one(Input)
            field.value = ""
            field.placeholder = "Type to filter, or type the path to an environment or Python  (enter: use · esc: close)"
            self._fill("")
        else:
            self.dismiss(None)


class KernelChip(Static):
    """The header chip: 'Python 3.13.7 · .venv'. Clicking it opens the env picker."""

    def on_click(self) -> None:
        self.app.action_pick_env()


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
    """Switch the pane's kernel to env, installing ipykernel into it first if it is a venv without it
    (a uv project's venv runs through `uv run --with ipykernel` instead)."""
    env = envs.runnable(env, _base(pane))
    if env.same(pane.kernel.env):
        pane.notify(f"The kernel already uses {env.name}")
        return
    state = pane.kernel_state
    try:
        await envs.ensure_ipykernel(env, _progress(pane, f"Installing ipykernel into {env.name}"))
    except envs.EnvError:
        pane.set_kernel_state(state)
        if env.prefix is None:
            pane.app.push_screen(
                EnvPicker(pane.kernel.env, _base(pane), ask=("create", "uv" if envs.uv() else "venv", str(env.python))),
                lambda choice: handle(pane, choice),
            )
            pane.notify(f"{env.name} has no ipykernel. enter: make an environment from it · esc: stop", timeout=6)
        else:
            pane.notify(f"Could not install ipykernel into {env.name}", severity="error")
        return
    await pane.switch_env(env)


async def create(pane, tool: str, folder: str, python: str | None = None) -> None:
    path = Path(folder).expanduser()
    if not path.is_absolute():
        path = _base(pane) / path
    if (existing := envs.venv_at(path)) is not None:
        pane.notify(f"{_home(path)} is already an environment. The kernel uses it now.")
        await use(pane, existing)
        return
    if path.exists() and any(path.iterdir()):
        pane.notify(f"{_home(path)} is not empty. Type a different folder.", severity="error")
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
    pane.notify(f"Made {env.name} (Python {env.version}, {cmd})")
    await pane.switch_env(env)


async def install(pane, names: list[str], project: bool = False) -> str | None:
    """Install into the kernel's env; with project=True, `uv add` them to pyproject.toml too.

    Returns None when the packages are in place and the kernel sees them, else the failure:
    the command, its exit code and what it said (shown as a notification too)."""
    env = pane.kernel.env
    text = " ".join(names)
    state = pane.kernel_state
    on_line = _progress(pane, f"{'uv add' if project else 'installing'} {text}")
    try:
        if project and not await envs.add(env, names, _base(pane), on_line):
            pane.notify("There is no pyproject.toml here. gpython installs only into the kernel environment.")
            project = False
        if not project:
            await envs.install(env, names, on_line)
    except envs.EnvError as error:
        pane.notify(str(error), title="Install failed", severity="error", timeout=15)
        return str(error)
    finally:
        pane.set_kernel_state(state)
    await _refresh_imports(pane)
    where = f"{_home(envs.project_root(_base(pane)) / 'pyproject.toml')} and {env.name}" if project else env.name
    pane.notify(f"Added {text} to {where}" if project else f"Installed {text} into {where}")
    return None


async def _refresh_imports(pane) -> None:
    if pane.kernel.ready.is_set():
        await pane.kernel._execute("__import__('importlib').invalidate_caches()", None, store_history=False)


def project_sync(pane, names: list[str]) -> tuple[list[str], str] | None:
    """When the kernel runs in its uv project's environment and every one of `names` is declared
    in pyproject.toml: the `uv sync` flags that install them and where they are declared."""
    env = pane.kernel.env
    root = envs.project_root(_base(pane))
    if not names or not envs.is_uv_project(root) or env.prefix != envs.project_env_dir(root):
        return None
    places = [packages.declared(root, name) for name in names]
    if None in places:
        return None
    flags = list(dict.fromkeys(flag for where in places for flag in envs.sync_flags(where)))
    reason = ", ".join(sorted({f"the {name} {kind}" if kind != "main" else "the project's dependencies" for kind, name in places}))
    return flags, reason


async def sync(pane, flags: list[str]) -> str | None:
    """`uv sync --inexact` with flags into the kernel's project env; remembered for later launches.
    Returns None when done, else the failure (shown as a notification too)."""
    root = envs.project_root(_base(pane))
    shown = " ".join(["uv sync", *flags])
    state = pane.kernel_state
    try:
        await envs.sync(pane.kernel.env, root, flags, _progress(pane, shown))
    except envs.EnvError as error:
        pane.notify(str(error), title="Sync failed", severity="error", timeout=15)
        return str(error)
    finally:
        pane.set_kernel_state(state)
    await _refresh_imports(pane)
    pane.notify(f"{shown}: done. gpython passes {' '.join(flags)} on later launches" if flags else f"{shown}: done")
    return None


def default_mode(pane) -> str:
    """"add" (`uv add` to the project) or "install" (into the env only): the way last chosen,
    else "install". `uv add` rewrites pyproject.toml and uv.lock, so it is never the first default:
    a notebook inside some tool's checkout would add its imports to that tool's dependencies."""
    root = envs.project_root(_base(pane))
    if not envs.uv() or not (root / "pyproject.toml").exists():
        return "install"
    saved = config.load().get("install_mode")
    return saved if saved in ("add", "install") else "install"


def install_missing(pane, cells: list, after_install: bool = False) -> None:
    """Cells whose run ended in ModuleNotFoundError (`cell.missing`): marimo's missing-package banner.

    The picker asks first, prefilled with the PyPI names; enter installs them into the kernel's
    env and runs the cells again. Every run of the cell asks again, except the re-run after an
    install, which leaves out the modules just tried (a wrong name must not loop). With
    `:set autoinstall` the first try of a module installs without a question; a module whose
    install failed is asked about on the next run instead.
    """
    new = [cell for cell in cells if not (after_install and cell.missing in pane.tried)]
    if not new:
        return
    tried_before = any(cell.missing in pane.tried for cell in new)
    pane.tried.update(cell.missing for cell in new)
    names = sorted({packages.pypi_name(cell.missing, cell.source) for cell in new})
    cids = [cell.cid for cell in new]
    declared = project_sync(pane, names)
    mode = "sync" if declared is not None else default_mode(pane)
    if pane.app.autoinstall and not tried_before:
        work = _sync_then_run(pane, declared[0], cids) if declared is not None else _install_then_run(pane, names, cids, mode == "add")
        pane.app.run_worker(work, group="env")
        return
    if isinstance(pane.app.screen, ModalScreen):
        return
    env = pane.kernel.env
    modules = sorted({cell.missing for cell in new})
    verb = "is" if len(modules) == 1 else "are"
    again = ", then run " + ", ".join(cell.label for cell in new) + " again"

    def done(choice: tuple | None) -> None:
        if choice and choice[0] == "sync":
            pane.app.run_worker(_sync_then_run(pane, choice[1], cids), group="env")
        elif choice and choice[0] in ("install", "add"):
            pane.app.run_worker(_install_then_run(pane, choice[1], cids, choice[0] == "add"), group="env")

    picker = EnvPicker(
        env, _base(pane), title=f"{', '.join(modules)} {verb} not installed", ask=(mode,), prefill=" ".join(names),
        after=again, sync=declared,
    )
    pane.app.push_screen(picker, done)


async def _sync_then_run(pane, flags: list[str], cids: list[str]) -> None:
    await _after(pane, await sync(pane, flags), cids)


async def _install_then_run(pane, names: list[str], cids: list[str], project: bool) -> None:
    """Install, then run the cells again; a failure is written under each of them as well."""
    await _after(pane, await install(pane, names, project), cids)


async def _after(pane, failure: str | None, cids: list[str]) -> None:
    if failure is None:
        await pane.run_cells(cids, after_install=True)
        return
    for state in pane.states:
        if state.cid in cids:
            state.show_hint(failure)


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
    elif what == "sync":
        work = sync(pane, args[0])
    elif what == "restart":
        work = pane.restart_kernel()
    else:
        return
    pane.app.run_worker(work, group="env")


def open_picker(pane, ask: tuple | None = None, prefill: str = "") -> None:
    if not isinstance(pane.app.screen, EnvPicker):
        pane.app.push_screen(EnvPicker(pane.kernel.env, _base(pane), ask=ask, prefill=prefill), lambda choice: handle(pane, choice))


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
        pane.notify(f"No environment matches {arg!r}. Press f6 to see all environments.", severity="error")
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
    """:pip numpy · :pip install numpy · :pip uninstall numpy · :pip list — for the kernel's env.

    :pip alone asks, prefilled with the package the current cell failed to import."""
    words = _words(arg)
    if not words:
        open_picker(pane, ask=(default_mode(pane),), prefill=pane.missing_package())
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


def ex_sync(pane, arg: str = "") -> None:
    """:sync [extra or group ...]: `uv sync --inexact` in the kernel's uv project. With no names,
    the extras the current cell's missing package needs, if any."""
    root = envs.project_root(_base(pane))
    env = pane.kernel.env
    if not envs.is_uv_project(root) or env.prefix != envs.project_env_dir(root):
        pane.notify(f"The kernel does not run in a uv project's environment (it uses {env.name}). :pip installs into it", severity="warning")
        return
    flags = []
    for name in _words(arg):
        kind = packages.dependency_table(root, name)
        if kind is None:
            pane.notify(f"{name} is not an extra or a dependency group in {_home(root / 'pyproject.toml')}", severity="error")
            return
        flags.append(f"--{kind}={name}")
    if not flags and (missing := pane.missing_package()) and (declared := project_sync(pane, [missing])) is not None:
        flags = declared[0]
    cell = pane.current_cell()
    cids = [cell.cid] if cell is not None and cell.missing and not arg else []
    pane.app.run_worker(_sync_then_run(pane, flags, cids) if cids else sync(pane, flags), group="env")


def ex_uv(pane, arg: str = "") -> None:
    """:uv add pkg · :uv pip install pkg · :uv venv [folder] [python] · :uv <anything> (in the project)."""
    words = _words(arg)
    if not envs.uv():
        pane.notify("uv is not installed. Get it from https://docs.astral.sh/uv/", severity="error")
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
