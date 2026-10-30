"""The Textual front end: notebook windows in tabs and splits, plus a file explorer.

GPython (the App) owns what is global: the theme, the vim mode, the status bar, the
prompts and the `:` commands. Each open notebook is a NotebookPane (pane.py) living in
the Workspace (windows.py), which lays panes out in vim-style tabs and splits. Key
bindings are forwarded to the active pane.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.color import Color
from textual.containers import Horizontal
from textual.reactive import Reactive
from textual.screen import ModalScreen, Screen
from textual.widgets import Header, Input, Label, Static
from textual.widgets._header import HeaderIcon

from . import bigtext, config
from .cell import Cell, CellSnapshot, ends_with_string
from .explorer import Explorer
from .help import HelpScreen
from .markdown import MarkdownView, markdown_theme
from .pane import NotebookPane
from . import envpicker
from .envpicker import KernelChip
from .themepicker import ThemePicker
from .themes import CUSTOM_APP_THEMES, DEFAULT_THEME, TERMINAL_THEME, THEME_ALIASES, editor_themes, palette_for
from .vim import VimTextArea, _is_escape
from .windows import TabBar, Workspace

KEY_HINTS = "shift+enter run+next · ctrl+enter run · esc esc cell mode · / jump · ctrl+b files · ctrl+w splits · f1 help"
_MODE_STYLES = {
    "insert": "bold black on green",
    "normal": "bold black on yellow",
    "cell": "bold black on cyan",
    "files": "bold black on magenta",
    "visual": "bold black on bright_magenta",
    "v-line": "bold black on bright_magenta",
}
EXPLORER_WIDTH = 34

_WINDOW_KEYS = {
    "s": "split", "S": "split", "ctrl+s": "split", "n": "split", "ctrl+n": "split",
    "v": "vsplit", "ctrl+v": "vsplit",
    "h": "left", "left": "left", "ctrl+h": "left",
    "j": "down", "down": "down", "ctrl+j": "down",
    "k": "up", "up": "up", "ctrl+k": "up",
    "l": "right", "right": "right", "ctrl+l": "right",
    "w": "next", "ctrl+w": "next", "W": "prev", "p": "prev", "ctrl+p": "prev",
    "t": "first", "ctrl+t": "first", "b": "last", "ctrl+b": "last",
    "q": "close", "ctrl+q": "close", "c": "close", "ctrl+c": "close",
    "o": "only", "ctrl+o": "only",
    "T": "totab",
    "=": "equal",
    "+": "taller", "-": "shorter", ">": "wider", "<": "narrower",
}


class NotebookScreen(Screen):
    """The main screen. Textual's Screen repaints *everything* whenever the focus moves
    (its `focused` reactive has repaint=True); the widgets that gain and lose focus already
    repaint themselves, so here a focus change costs nothing extra (about 8 ms per cell move)."""

    focused: Reactive = Reactive(None, repaint=False)


class Prompt(ModalScreen[str | None]):
    """A one-line input at the bottom, used for ':' commands and 'save as'."""

    BINDINGS = [Binding("escape", "dismiss(None)", show=False)]

    def __init__(self, label: str, value: str = "", placeholder: str = "") -> None:
        super().__init__()
        self.label, self.value, self.placeholder = label, value, placeholder

    def compose(self) -> ComposeResult:
        with Horizontal(id="prompt"):
            yield Label(self.label)
            yield Input(value=self.value, placeholder=self.placeholder)

    @on(Input.Submitted)
    def _submit(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)


class KeyCapture(ModalScreen[str | None]):
    """Waits for exactly one key (the second key of ctrl+w); shows the prefix like vim's showcmd."""

    def __init__(self, hint: str) -> None:
        super().__init__()
        self.hint = hint

    def compose(self) -> ComposeResult:
        yield Static(Text(f" {self.hint} ", style="bold reverse"), id="keycapture")

    def on_key(self, event: events.Key) -> None:
        event.stop()
        event.prevent_default()
        if _is_escape(event):
            self.dismiss(None)
            return
        key = event.character if event.is_printable and event.character else event.key
        self.dismiss(key)


def _state_style(state: str) -> str:
    """Colour of the kernel dot in the bottom bar: ready, busy (starting/installing) or failed."""
    if state == "kernel ready":
        return "bold green"
    if "fail" in state or "died" in state:
        return "bold red"
    return "bold yellow"


class GPython(App):
    CSS_PATH = "app.tcss"
    TITLE = "gpython"
    BINDINGS = [
        Binding("ctrl+enter,ctrl+shift+enter,ctrl+e", "run_cell", "Run", priority=True),
        Binding("shift+enter", "run_cell('next')", "Run + next", priority=True),
        Binding("alt+enter", "run_cell('insert')", "Run + insert", priority=True),
        Binding("ctrl+n", "add_cell", "New cell", priority=True),
        Binding("ctrl+shift+n", "add_cell(True)", "New cell above", priority=True),
        Binding("ctrl+d", "delete_cell", "Delete cell", priority=True),
        Binding("alt+up,ctrl+up", "focus_cell(-1)", "Prev cell", show=False, priority=True),
        Binding("alt+down,ctrl+down", "focus_cell(1)", "Next cell", show=False, priority=True),
        Binding("ctrl+home", "focus_edge(False)", "First cell", show=False, priority=True),
        Binding("ctrl+end", "focus_edge(True)", "Last cell", show=False, priority=True),
        Binding("alt+shift+up", "move_cell(-1)", "Move cell up", show=False, priority=True),
        Binding("alt+shift+down", "move_cell(1)", "Move cell down", show=False, priority=True),
        Binding("ctrl+s", "save", "Save", priority=True),
        Binding("ctrl+k", "interrupt", "Interrupt", priority=True),
        Binding("ctrl+w", "window_prefix", "Window", show=False, priority=True),
        Binding("ctrl+b", "toggle_explorer", "Files", priority=True),
        Binding("f1", "help", "Help"),
        Binding("f2", "toggle_vars", "Variables"),
        Binding("f3", "toggle_lazy", "Lazy"),
        Binding("f4", "run_stale", "Run stale"),
        Binding("f5", "run_all", "Run all"),
        Binding("ctrl+t", "pick_theme", "Theme"),
        Binding("f6", "pick_env", "Env", priority=True),
    ]
    _previewing_theme = False

    def __init__(
        self,
        path: Path | None = None,
        vim: bool | None = None,
        theme: str | None = None,
        paths: list[Path] | None = None,
    ) -> None:
        super().__init__()
        settings = config.load()
        VimTextArea.mode = "normal"
        self._editor_theme_cache: dict[str, tuple] = {}
        self._markdown_theme_pushed = False
        self._theme_warning: str | None = None
        for custom in (*CUSTOM_APP_THEMES.values(), TERMINAL_THEME):
            self.register_theme(custom)
        self._loaded_theme = settings.get("theme")
        name = theme or self._loaded_theme or DEFAULT_THEME
        name = THEME_ALIASES.get(name, name)
        if name not in self.available_themes:
            self._theme_warning = f"Unknown theme {name!r}; using {DEFAULT_THEME}"
            name = DEFAULT_THEME
        self._loaded_theme = name
        if self.theme == name:
            self.watch_theme(name)
        self.theme = name
        if vim is None:
            vim = settings.get("vim", True) is not False
        VimTextArea.enabled = vim
        VimTextArea.line_numbers = settings.get("line_numbers", True) is not False
        bigtext.enabled = settings.get("experimental.true_markdown") is True

        files = [p for p in ([path] if path is not None else []) + list(paths or [])]
        self.explorer_root = Path.cwd()
        self._explorer_open_at_start = False
        notebooks: list[Path] = []
        for file in files:
            if file.is_dir():
                self.explorer_root = file
                self._explorer_open_at_start = True
            else:
                notebooks.append(file)
        if notebooks and not any(f.is_dir() for f in files):
            self.explorer_root = notebooks[0].resolve().parent
        self._initial_panes = [NotebookPane(p) for p in notebooks] or [NotebookPane(None)]

        self.lazy = False
        self._first_frame_done = False
        self._first_frame_callbacks: list = []
        self.workspace = Workspace()
        self.explorer: Explorer | None = None
        self._tabbar = TabBar()
        self._status: Static | None = None
        self._kernel_chip: Static | None = None
        self._chip_label: str | None = None
        self._mode_chip: Static | None = None
        self._mode_chip_mode: str | None = None
        self._status_key: tuple | None = None
        self._cell_clipboard: CellSnapshot | None = None
        self._prompt_mode = "normal"
        self._run_number = None

    def get_default_screen(self) -> Screen:
        return NotebookScreen(id="_default")


    def after_first_frame(self, callback) -> None:
        if self._first_frame_done:
            self.call_after_refresh(callback)
        else:
            self._first_frame_callbacks.append(callback)

    def on_ready(self) -> None:
        self.call_after_refresh(self._first_frame)

    def _first_frame(self) -> None:
        self._first_frame_done = True
        callbacks, self._first_frame_callbacks = self._first_frame_callbacks, []
        for callback in callbacks:
            callback()

    def compose(self) -> ComposeResult:
        yield Header()
        yield self._tabbar
        with Horizontal(id="body"):
            yield self.workspace
        self._status = Static(id="status")
        yield self._status

    async def on_mount(self) -> None:
        VimTextArea.mode = "normal"
        header = self.query_one(Header)
        self._mode_chip = Static(id="mode-chip")
        await header.mount(self._mode_chip, after=header.query_one(HeaderIcon))
        self._kernel_chip = KernelChip(id="kernel")
        await header.mount(self._kernel_chip)
        for pane in self._initial_panes:
            await self.workspace.add(pane, "tab")
        self.workspace.switch(0)
        if self._theme_warning:
            self.notify(self._theme_warning, severity="warning")
        self.pane.focus_pane()
        if self._explorer_open_at_start:
            await self.show_explorer()
        self.update_status()

    async def on_unmount(self) -> None:
        await asyncio.gather(*(pane.kernel.shutdown() for pane in self.workspace.buffers), return_exceptions=True)


    @property
    def pane(self) -> NotebookPane:
        """The active window's notebook."""
        active = self.workspace.active
        if active is None:
            return self._initial_panes[0]
        return active

    @property
    def kernel(self):
        return self.pane.kernel

    @property
    def path(self) -> Path | None:
        return self.pane.path

    @property
    def dirty(self) -> bool:
        return self.pane.dirty

    @property
    def markers(self) -> bool:
        return self.pane.markers

    @property
    def loaded(self) -> asyncio.Event:
        return self.pane.loaded

    def cells(self) -> list[Cell]:
        return self.pane.cells()

    def current_cell(self) -> Cell | None:
        return self.pane.current_cell()

    async def run_cells(self, roots: list[str], downstream: bool | None = None) -> None:
        await self.pane.run_cells(roots, downstream)

    def graph(self):
        return self.pane.graph()

    def refresh_info(self):
        return self.pane.refresh_info()

    async def set_cell_kind(self, cell: Cell, kind: str, record: bool = True) -> None:
        await (cell.pane or self.pane).set_cell_kind(cell, kind, record)

    def select(self, cell: Cell, mode: str, row: int | None = None) -> None:
        (cell.pane or self.pane).select(cell, mode, row)

    def goto_cell(self, n: int, mode: str, row: int = 0, clamp: bool = True) -> bool:
        return self.pane.goto_cell(n, mode, row, clamp)


    @property
    def ui_mode(self) -> str:
        """"cell" when a cell itself is selected, "files" in the explorer, else the vim mode."""
        focused = self.focused
        if isinstance(focused, Cell):
            return "cell"
        if isinstance(focused, Explorer):
            return "files"
        if not VimTextArea.enabled or VimTextArea.mode == "insert":
            return "insert"
        return getattr(focused, "visual_mode", "") or "normal"

    def watch_theme(self, name: str) -> None:
        """Point every editor and cell header at the new theme; remember the choice."""
        self.ansi_color = self.current_theme.ansi
        self.palette = palette_for(self.current_theme)
        pair = self._editor_theme_cache.get(self.palette.name)
        if pair is None:
            pair = self._editor_theme_cache[self.palette.name] = editor_themes(self.palette)
        self.editor_theme_pair = pair
        if self._markdown_theme_pushed:
            self.console.pop_theme()
        self.console.push_theme(markdown_theme(self.palette))
        self._markdown_theme_pushed = True
        variables = self.get_css_variables()
        self.bar_colors = {role: variables[role] for role in ("panel", "accent", "warning", "primary", "error")}
        try:
            if Color.parse(self.bar_colors["panel"]).a == 0:
                self.bar_colors["panel"] = "ansi_bright_black" if self.current_theme.ansi else variables["foreground-muted"]
        except Exception:
            pass
        if self.is_running:
            for editor in self.query(VimTextArea):
                editor.set_editor_theme(*pair)
            for cell in self.query(Cell):
                cell._header_key = None
                cell._render_header()
                cell.paint_bar()
            for view in self.query(MarkdownView):
                view.restyle()
            self.update_status()
        if name != self._loaded_theme and not self._previewing_theme:
            config.save({"theme": name})
            self._loaded_theme = name

    def pane_focused(self, pane: NotebookPane) -> None:
        """A widget inside `pane` took focus: it is the active window now."""
        if self.workspace.active is not pane:
            self.workspace.set_active(pane)

    def workspace_changed(self) -> None:
        """Tabs or windows changed: redraw the tab line and the header."""
        tabs = self.workspace.tabs
        hidden = [(self.workspace.number(p), p) for p in self.workspace.hidden()]
        self.set_class(len(tabs) > 1 or bool(hidden), "-tabs")
        if tabs:
            self._tabbar.show(tabs, self.workspace.index, hidden)
        self._status_key = None
        self.update_status()

    def update_status(self, kernel_state: str | None = None) -> None:
        """Header: kernel chip + file subtitle. Status line: vim mode, run mode, kernel state, theme, hints."""
        if self._status is None or not self.workspace.tabs:
            return
        pane = self.pane
        if kernel_state is not None:
            pane.set_kernel_state(kernel_state)
            return
        if self._run_number is not None:
            self._status_key = None
            self._status.update(self._run_number.status(), layout=False)
            return
        mode = self.ui_mode
        name = pane.title
        key = (mode, VimTextArea.enabled, name, self.lazy, pane.kernel_state, self.theme, pane.kernel_label)
        if key == self._status_key:
            return
        self._status_key = key
        show_mode = VimTextArea.enabled or mode in ("cell", "files")
        self.sub_title = name
        if self._kernel_chip is not None and pane.kernel_label != self._chip_label:
            self._chip_label = pane.kernel_label
            version, _, env = pane.kernel_label.partition(" · ")
            self._kernel_chip.update(Text.assemble((version, "dim"), (f" · {env}" if env else "", "bold"), (" ▾", "dim")))
        chip_mode = "cell" if mode == "cell" else "edit"
        if self._mode_chip is not None and chip_mode != self._mode_chip_mode:
            self._mode_chip_mode = chip_mode
            style = _MODE_STYLES["cell"] if chip_mode == "cell" else _MODE_STYLES["insert"]
            self._mode_chip.update(Text(f" {chip_mode.upper()} ", style=style), layout=False)
        self._status.update(
            Text.assemble(
                (f" {mode.upper()} ", _MODE_STYLES[mode]) if show_mode else "",
                f"  {'lazy' if self.lazy else 'reactive'}  ·  ",
                ("● ", _state_style(pane.kernel_state)),
                f"{pane.kernel_state}  ·  {self.theme}",
                (f"    {KEY_HINTS}", "dim"),
            ),
            layout=False,
        )


    def find_open(self, path: Path) -> NotebookPane | None:
        """The buffer (shown or hidden) for `path`, if the file is open."""
        target = path.resolve()
        for pane in self.workspace.buffers:
            if pane.path is not None and pane.path.resolve() == target:
                return pane
        return None

    async def open_file(self, path: Path | str, how: str = "tab", force: bool = False) -> NotebookPane | None:
        """Open a notebook in a new "tab" (the default, also for the explorer), in the
        current window ("here", vim's :edit), a "split" or a "vsplit".

        Every notebook is a buffer with its own kernel, so a file that is already open is
        shown (its tab focused, or a hidden buffer brought back) instead of opened twice.
        A blank untitled window is reused rather than left behind. "here" refuses to
        replace a notebook with unsaved changes unless `force` (:e!).
        """
        path = Path(path).expanduser()
        if path.is_dir():
            await self.show_explorer(path)
            return None
        current = self.workspace.active
        if (existing := self.find_open(path)) is not None:
            if existing in self.workspace.panes():
                self.workspace.set_active(existing)
            elif how == "here" and current is not None:
                await self.workspace.show(existing, current)
            else:
                await self.workspace.add(existing, how if how in ("split", "vsplit") else "tab", current)
            existing.focus_pane()
            return existing
        if path.exists() and not path.is_file():
            self.notify(f"Not a file: {path}", severity="error")
            return None
        if not path.exists() and path.suffix not in (".py", ".ipynb"):
            self.notify(f"New notebooks need a .py or .ipynb name: {path}", severity="error")
            return None
        pane = NotebookPane(path)
        if current is not None and (how == "here" or current.is_blank):
            if current.dirty and not force:
                self.notify(f"No write since last change for {current.name} (:e! {path} discards, or :w first)", severity="warning")
                return None
            await self.workspace.replace(current, pane)
        elif how in ("split", "vsplit") and current is not None:
            await self.workspace.add(pane, how, current)
        else:
            await self.workspace.add(pane, "tab")
        pane.focus_pane()
        return pane


    def buffer_lines(self) -> list[str]:
        """One line per buffer, vim style: number, %a current / a shown / h hidden, + dirty, name."""
        shown = set(self.workspace.panes())
        current = self.pane
        lines = []
        for n, pane in enumerate(self.workspace.buffers, 1):
            flag = "%a" if pane is current else (" a" if pane in shown else " h")
            tab = self.workspace.tab_of(pane)
            where = f"tab {tab + 1}" if tab is not None else "hidden"
            lines.append(f"{n:3d} {flag}{'+' if pane.dirty else ' '} {pane.name:<24} {where}")
        return lines

    async def show_buffer(self, key: str | int) -> None:
        """:b N / :b name: show that buffer in the current window."""
        pane = self.workspace.buffer(str(key))
        if pane is None:
            self.notify(f"No buffer {key} (:ls lists them)", severity="warning")
            return
        await self.workspace.show(pane, self.workspace.active)
        pane.focus_pane()

    async def cycle_buffer(self, step: int) -> None:
        """:bn / :bp: the next / previous buffer in the current window."""
        buffers = self.workspace.buffers
        if len(buffers) < 2:
            return
        current = self.pane
        index = buffers.index(current) if current in buffers else 0
        await self.show_buffer(str((index + step) % len(buffers) + 1))

    async def delete_buffer(self, key: str = "", force: bool = False) -> None:
        """:bd[!] [N | name]: close the buffer (asks when it has unsaved changes) and its kernel."""
        pane = self.workspace.buffer(key) if key.strip() else self.pane
        if pane is None:
            self.notify(f"No buffer {key} (:ls lists them)", severity="warning")
            return
        if pane.dirty and not force:
            self.push_screen(
                Prompt(f"Unsaved changes in {pane.name}. Delete the buffer anyway? (y/n) "),
                lambda answer: self._confirm(answer, lambda: self.delete_buffer(key, force=True)),
            )
            return
        nxt = await self.workspace.delete(pane)
        if nxt is None:
            await self.new_pane("tab")
        else:
            nxt.focus_pane()

    async def new_pane(self, how: str) -> NotebookPane:
        """An untitled notebook in a split or tab."""
        pane = NotebookPane(None)
        await self.workspace.add(pane, how, self.workspace.active)
        pane.focus_pane()
        return pane

    async def close_window(self, force: bool = False, save: bool = False) -> None:
        """vim's :q / :wq / :q!: close the current window (its notebook stays a hidden
        buffer); closing the last window quits, asking first about any unsaved buffer."""
        pane = self.pane
        if save:
            if pane.path is None:
                self.notify("No file name. Use :wq name.py", severity="warning")
                return
            pane.write_file()
        if len(self.workspace.panes()) == 1:
            if force:
                self.exit()
            else:
                await self.action_quit()
            return
        nxt = await self.workspace.close(pane)
        if nxt is None:
            self.exit()
        else:
            nxt.focus_pane()

    def _confirm(self, answer: str | None, then) -> None:
        if answer and answer.lower().startswith("y"):
            result = then()
            if asyncio.iscoroutine(result):
                self.run_worker(result)

    def focus_window(self, where: str) -> None:
        """h/j/k/l: the window in that direction; next/prev/first/last cycle through them."""
        current = self.pane
        explorer_open = self.explorer_visible
        in_explorer = isinstance(self.focused, Explorer)
        panes = self.workspace.tab_panes()
        if where in ("left", "right", "up", "down"):
            direction = {"left": "h", "right": "l", "up": "k", "down": "j"}[where]
            if in_explorer:
                if direction == "l" and panes:
                    rects = self.workspace.tab.rects
                    leftmost = min(panes, key=lambda p: (rects[p].x, rects[p].y) if p in rects else (0, 0))
                    self.workspace.set_active(leftmost)
                    leftmost.focus_pane()
                return
            target = self.workspace.neighbour(current, direction)
            if target is None and direction == "h" and explorer_open:
                self.explorer.focus()
                return
        elif where in ("next", "prev"):
            ring: list = ([self.explorer] if explorer_open else []) + panes
            at = ring.index(self.explorer) if in_explorer else (ring.index(current) if current in ring else 0)
            target = ring[(at + (1 if where == "next" else -1)) % len(ring)]
            if isinstance(target, Explorer):
                target.focus()
                return
        elif where == "first":
            panes = self.workspace.tab_panes()
            target = panes[0] if panes else None
        else:
            panes = self.workspace.tab_panes()
            target = panes[-1] if panes else None
        if target is not None:
            self.workspace.set_active(target)
            target.focus_pane()

    def tab_switch(self, n: int | None, step: int) -> None:
        """gt / gT / :tabn: tab `n` (1-based) when given, else `step` tabs forward."""
        tabs = self.workspace.tabs
        if not tabs:
            return
        if n is not None:
            index = max(0, min(len(tabs) - 1, n - 1))
        else:
            index = (self.workspace.index + step) % len(tabs)
        if index != self.workspace.index:
            self.workspace.switch(index)
            self.pane.focus_pane()

    async def tab_close(self, force: bool = False) -> None:
        """:tabclose: the tab's notebooks stay open as hidden buffers (:ls)."""
        panes = self.workspace.tab_panes()
        if len(self.workspace.tabs) == 1:
            await self.action_quit() if not force else self.exit()
            return
        for pane in panes:
            await self.workspace.close(pane)
        self.pane.focus_pane()

    async def tab_only(self) -> None:
        keep = self.workspace.index
        for i, tab in reversed(list(enumerate(self.workspace.tabs))):
            if i == keep:
                continue
            for pane in list(self.workspace.panes()):
                if self.workspace.tab_of(pane) == i:
                    await self.workspace.close(pane)
                    if self.workspace.tab_of(self.pane) is None:
                        break
        self.workspace.switch(self.workspace.tab_of(self.pane) or 0)

    @property
    def explorer_visible(self) -> bool:
        return self.explorer is not None and self.explorer.display

    async def show_explorer(self, root: Path | None = None) -> None:
        """Show the file tree (mounted on first use: its directory loader is a long-lived worker) and focus it."""
        explorer = self.explorer
        if explorer is None:
            explorer = self.explorer = Explorer(root or self.explorer_root)
            await self.query_one("#body").mount(explorer, before=self.workspace)
        elif root is not None and Path(root).resolve() != Path(explorer.path).resolve():
            explorer.path = root
        explorer.display = True
        explorer.focus()
        self.update_status()

    def hide_explorer(self) -> None:
        if self.explorer is not None:
            self.explorer.display = False
        self.pane.focus_pane()
        self.update_status()

    async def action_toggle_explorer(self) -> None:
        """ctrl+b: show the file tree (rooted at the notebook's folder) and focus it; hide it when shown."""
        if self.explorer_visible:
            self.hide_explorer()
        else:
            root = None if self.explorer is not None else (self.pane.path.resolve().parent if self.pane.path else None)
            await self.show_explorer(root)

    @on(Explorer.Open)
    async def _explorer_open(self, event: Explorer.Open) -> None:
        event.stop()
        await self.open_file(event.path, event.how)

    @on(Explorer.Closed)
    def _explorer_closed(self) -> None:
        self.hide_explorer()

    @on(Explorer.SearchRequested)
    def _explorer_search(self) -> None:
        self.push_screen(Prompt("/", placeholder="file name"), lambda text: text and self.explorer.search(text))

    @on(Explorer.NewFile)
    def _explorer_new_file(self, event: Explorer.NewFile) -> None:
        directory = event.directory

        async def create(name: str | None) -> None:
            if not name:
                return
            path = Path(name).expanduser()
            if not path.is_absolute():
                path = directory / path
            if path.suffix not in (".py", ".ipynb"):
                path = path.with_suffix(".py")
            if path.exists():
                await self.open_file(path, "here")
                return
            pane = await self.open_file(path, "here")
            if pane is not None:
                pane.write_file()
                self.explorer.reload()

        self.push_screen(Prompt("New notebook: ", placeholder="name.py"), create)

    async def action_window_prefix(self) -> None:
        """ctrl+w: wait for the window command (s v h j k l w q o T = ...)."""
        focused = self.focused
        if isinstance(focused, VimTextArea) and self.ui_mode == "insert":
            focused.action_delete_word_left()
            return
        if isinstance(focused, Input):
            return
        if not isinstance(self.screen, KeyCapture):
            await self.push_screen(KeyCapture("^W"), self._window_command)

    async def _window_command(self, key: str | None) -> None:
        if key is None:
            return
        command = _WINDOW_KEYS.get(key)
        pane = self.pane
        match command:
            case "split" | "vsplit":
                await self.new_pane(command)
            case "left" | "right" | "up" | "down" | "next" | "prev" | "first" | "last":
                self.focus_window(command)
            case "close":
                await self.close_window()
            case "only":
                await self.workspace.only(pane)
                pane.focus_pane()
            case "totab":
                await self.workspace.to_new_tab(pane)
                pane.focus_pane()
            case "equal":
                self.workspace.equalize()
            case "taller" | "shorter":
                self.workspace.resize(pane, False, 0.1 if command == "taller" else -0.1)
            case "wider" | "narrower":
                self.workspace.resize(pane, True, 0.1 if command == "wider" else -0.1)
            case _:
                self.notify(f"ctrl+w {key}: not a window command (s v h j k l w p q o T = + - < >)", severity="warning")


    def start_run_by_number(self, pane: NotebookPane | None = None) -> None:
        """r (cell mode) / space r (vim normal mode): wait for a cell number, then run that cell."""
        from .runnumber import RunByNumber

        if self._run_number is not None:
            self._run_number.cancel()
        self._run_number = RunByNumber(self, pane or self.pane)
        self.update_status()

    def end_run_by_number(self, sequence) -> None:
        if self._run_number is sequence:
            self._run_number = None
            self.update_status()

    async def on_event(self, event: events.Event) -> None:
        """While a run-by-number waits, its keys go to it instead of the focused widget."""
        if (
            self._run_number is not None
            and isinstance(event, events.Key)
            and not event.is_forwarded
            and isinstance(self.screen, NotebookScreen)
        ):
            key = "escape" if _is_escape(event) else event.character if event.is_printable and event.character else event.key
            self._run_number.key(key)
            return
        await super().on_event(event)

    def _ex_run(self, arg: str = "") -> None:
        arg = arg.strip()
        if not arg:
            self.action_run_all()
        elif arg.removeprefix("c").isdigit():
            self.run_worker(self.pane.run_number(int(arg.removeprefix("c"))), group="run-number")
        else:
            self.notify(f"Not a cell number: {arg}  (:run 6 runs c6, :run alone runs all)", severity="error")

    async def action_run_cell(self, advance: str = "stay") -> None:
        await self.pane.run_cell(advance)

    async def action_run_and_add(self) -> None:
        """Old name of shift+enter."""
        await self.pane.run_cell("next")

    def action_run_all(self) -> None:
        self.pane.run_all()

    def action_run_stale(self) -> None:
        self.pane.run_stale()

    async def action_add_cell(self, above: bool = False) -> None:
        await self.pane.add_cell(above)

    async def action_delete_cell(self, count: int = 1) -> None:
        await self.pane.delete_cell(count)

    async def action_undo(self) -> None:
        await self.pane.undo()

    action_undo_delete = action_undo

    def action_copy_cell(self) -> None:
        self.pane.copy_cell()

    async def action_cut_cell(self) -> None:
        await self.pane.cut_cell()

    async def action_paste_cell(self, above: bool = False) -> None:
        await self.pane.paste_cell(above)

    def action_move_cell(self, step: int) -> None:
        self.pane.move_cell(step)

    def action_focus_cell(self, step: int) -> None:
        self.pane.focus_cell(step)

    def action_focus_edge(self, last: bool) -> None:
        self.pane.focus_edge(last)

    def set_line_numbers(self, on: bool) -> None:
        VimTextArea.line_numbers = on
        for pane in self.workspace.buffers:
            for cell in pane.cells():
                cell.editor.show_line_numbers = on
        config.save({"line_numbers": on})

    def set_vim(self, on: bool) -> None:
        VimTextArea.enabled = on
        VimTextArea.mode = "normal" if on else "insert"
        for pane in self.workspace.buffers:
            for cell in pane.cells():
                cell.editor.pending = cell.editor.count = ""
                cell.editor._apply_mode()
        config.save({"vim": on})
        self.update_status()

    def action_save(self) -> None:
        self.pane.save()

    def write_file(self) -> None:
        self.pane.write_file()

    async def action_quit(self) -> None:
        """ctrl+q / :qa: quit the whole app; ask first when any buffer has unsaved changes."""
        dirty = [p.name for p in self.workspace.buffers if p.dirty]
        if dirty:
            self.push_screen(Prompt(f"Unsaved changes in {', '.join(dirty)}. Quit without saving? (y/n) "), self._confirm_quit)
        else:
            self.exit()

    def _confirm_quit(self, answer: str | None) -> None:
        if answer and answer.lower().startswith("y"):
            self.exit()

    async def action_interrupt(self) -> None:
        await self.pane.interrupt()

    def action_help(self) -> None:
        if not isinstance(self.screen, HelpScreen):
            commands = [(names[0], desc) for names, desc, _ in self.ex_commands()]
            self.push_screen(HelpScreen(commands))

    def action_toggle_vars(self) -> None:
        self.pane.toggle_vars()

    def action_toggle_lazy(self) -> None:
        self.lazy = not self.lazy
        self.update_status()
        self.notify("Lazy: dependents are marked stale, run them with f4" if self.lazy else "Reactive: dependents re-run automatically")

    async def action_restart_kernel(self) -> None:
        await self.pane.restart_kernel()


    @on(VimTextArea.ModeChanged)
    def _mode_changed(self) -> None:
        self.update_status()

    @on(VimTextArea.SearchRequested)
    def _search(self) -> None:
        self._open_search()

    @on(VimTextArea.ExCommand)
    def _ex(self) -> None:
        self._open_ex()

    @on(VimTextArea.FindFiles)
    def _find_files(self) -> None:
        self.action_find_files()

    @on(VimTextArea.RunByNumber)
    def _run_by_number(self) -> None:
        self.start_run_by_number()

    @on(VimTextArea.TabSwitch)
    def _tab_switch(self, event: VimTextArea.TabSwitch) -> None:
        self.tab_switch(event.tab, event.step)

    def _open_ex(self) -> None:
        self._prompt_mode = self.ui_mode
        self.push_screen(Prompt(":"), self._run_ex)

    def _open_search(self) -> None:
        self._prompt_mode = self.ui_mode
        self.push_screen(Prompt("/", placeholder="cell number or text"), self._jump)

    def _jump(self, target: str | None) -> None:
        if target:
            self.pane.jump(target, self._prompt_mode)

    def ex_commands(self) -> list[tuple[tuple[str, ...], str, object]]:
        """(names, description, handler). Shared by ':' commands, the palette and help.

        Handlers take the rest of the command line as one argument and may be async.
        `:N` (a number) jumps to the N-th cell, like `/N`.
        """
        return [
            (("w", "write"), "Save notebook (:w name.py saves as)", self._ex_write),
            (("wa", "wall"), "Save every open notebook", lambda arg="": self._save_all()),
            (("wq", "x"), "Save and close this window", lambda arg="": self._ex_write_quit(arg)),
            (("wqa", "wqall", "xa", "xall"), "Save every notebook and quit (:wqa name.py names an untitled one)", self._ex_write_quit_all),
            (("q", "quit", "close", "clo"), "Close this window (quits when it is the last; asks if unsaved)", lambda arg="": self.close_window()),
            (("q!", "quit!"), "Close this window without saving", lambda arg="": self.close_window(force=True)),
            (("qa", "qall", "quitall"), "Quit (asks if anything is unsaved)", lambda arg="": self.action_quit()),
            (("qa!", "qall!"), "Quit without saving", lambda arg="": self.exit()),
            (("e", "edit", "open"), "Open a notebook here (:e file; :e dir or :e alone opens the explorer)", self._ex_edit),
            (("e!", "edit!"), "Open a notebook here, discarding unsaved changes", lambda arg="": self._ex_edit(arg, force=True)),
            (("sp", "split"), "Horizontal split (:sp file opens it there)", lambda arg="": self._ex_split("split", arg)),
            (("vs", "vsp", "vsplit"), "Vertical split (:vs file opens it there)", lambda arg="": self._ex_split("vsplit", arg)),
            (("on", "only"), "Close every other window in this tab", lambda arg="": self._ex_only()),
            (("tabnew", "tabe", "tabedit"), "New tab (:tabnew file opens it there)", lambda arg="": self._ex_split("tab", arg)),
            (("tabc", "tabclose"), "Close this tab", lambda arg="": self.tab_close(force=arg.strip() == "!")),
            (("tabo", "tabonly"), "Close every other tab", lambda arg="": self.tab_only()),
            (("tabn", "tabnext"), "Next tab (:tabn 3 goes to tab 3)", lambda arg="": self.tab_switch(int(arg) if arg.strip().isdigit() else None, 1)),
            (("tabp", "tabprevious", "tabN", "tabNext"), "Previous tab", lambda arg="": self.tab_switch(None, -1)),
            (("tabfirst", "tabr", "tabrewind"), "First tab", lambda arg="": self.tab_switch(1, 0)),
            (("tablast",), "Last tab", lambda arg="": self.tab_switch(len(self.workspace.tabs), 0)),
            (("ls", "buffers", "files", "tabs"), "List the buffers (open notebooks): %a current, a shown, h hidden, + unsaved", lambda arg="": self._ex_list()),
            (("b", "buffer"), "Show buffer N or name in this window (:b 2, :b two-sum)", lambda arg="": self.show_buffer(arg) if arg.strip() else self._ex_list()),
            (("bn", "bnext"), "Next buffer in this window", lambda arg="": self.cycle_buffer(1)),
            (("bp", "bprevious", "bN"), "Previous buffer in this window", lambda arg="": self.cycle_buffer(-1)),
            (("bd", "bdelete"), "Delete buffer N or name (default: this one); stops its kernel", lambda arg="": self.delete_buffer(arg)),
            (("bd!", "bdelete!"), "Delete buffer without asking about unsaved changes", lambda arg="": self.delete_buffer(arg, force=True)),
            (("Ex", "Explore", "explorer", "tree", "Lex", "Vex"), "Toggle the file explorer (ctrl+b)", lambda arg="": self._ex_explore(arg)),
            (("run", "runall"), "Run all cells; :run 6 runs cell c6 (stale cells it uses first)", self._ex_run),
            (("runabove",), "Run the cells above the current one", lambda arg="": self.pane.run_relative(False)),
            (("runbelow",), "Run the current cell and the cells below", lambda arg="": self.pane.run_relative(True)),
            (("stale", "runstale"), "Run stale cells", lambda arg="": self.action_run_stale()),
            (("restart",), "Restart kernel", lambda arg="": self.action_restart_kernel()),
            (("clear",), "Clear all outputs", lambda arg="": self.pane.clear_outputs()),
            (("set",), "Options: vim novim lazy nolazy nu nonu", self._ex_set),
            (("variables", "vars"), "Toggle variables panel", lambda arg="": self.action_toggle_vars()),
            (("lazy",), "Toggle lazy mode", lambda arg="": self.action_toggle_lazy()),
            (("new",), "New cell below", lambda arg="": self.action_add_cell()),
            (("delete", "del"), "Delete current cell", lambda arg="": self.action_delete_cell()),
            (("md", "markdown"), "Cell → Markdown", lambda arg="": self._ex_kind("markdown")),
            (("code",), "Cell → Code", lambda arg="": self._ex_kind("code")),
            (("hide",), "Hide this cell's code, or fold a markdown cell (h in cell mode; code: saved as marimo hide_code)", lambda arg="": self._ex_hide(True)),
            (("show",), "Show this cell's code, or unfold a markdown cell", lambda arg="": self._ex_hide(False)),
            (("interrupt",), "Interrupt running code", lambda arg="": self.pane.kernel.interrupt()),
            (("theme",), "Set theme (:theme name) or show the current one", self._ex_theme),
            (("env", "kernel", "envs"), "Python environment for this kernel: pick (f6), or :env name|path switches", lambda arg="": envpicker.ex_env(self.pane, arg)),
            (("venv",), "New venv with python -m venv, then switch to it (:venv folder [python])", lambda arg="": envpicker.ex_venv(self.pane, arg)),
            (("uv",), "uv for this kernel: :uv add pkg · :uv pip install pkg · :uv venv [folder] [python]", lambda arg="": envpicker.ex_uv(self.pane, arg)),
            (("pip",), "Install into this kernel's env (:pip numpy; uses uv when available; :pip list, uninstall)", lambda arg="": envpicker.ex_pip(self.pane, arg)),
            (("find", "fin", "Telescope"), "Find files by fuzzy name (space f f; :find text starts the search)", lambda arg="": self.action_find_files(arg)),
            (("help", "h"), "Keys and commands", lambda arg="": self.action_help()),
        ]

    async def _ex_kind(self, kind: str) -> None:
        if cell := self.current_cell():
            await self.set_cell_kind(cell, kind)

    def _ex_hide(self, hidden: bool) -> None:
        if cell := self.current_cell():
            if cell.kind == "markdown":
                cell.set_folded(hidden)
            else:
                cell.set_code_hidden(hidden)
                self.pane.mark_dirty()
            if hidden and self.focused is cell.editor:
                cell.focus(scroll_visible=False)

    def _ex_set(self, arg: str = "") -> None:
        option = arg.strip()
        if option in ("vim", "novim"):
            self.set_vim(option == "vim")
        elif option in ("lazy", "nolazy"):
            if self.lazy != (option == "lazy"):
                self.action_toggle_lazy()
        elif option in ("nu", "number", "nonu", "nonumber"):
            self.set_line_numbers(not option.startswith("no"))
        else:
            self.notify(f"Unknown option: {option or '(none)'}  (vim novim lazy nolazy nu nonu)", severity="error")

    async def _ex_write(self, arg: str = "") -> None:
        if arg:
            await self.pane.saved_as(arg)
        else:
            self.pane.save()

    def _save_all(self, then=None) -> None:
        for pane in self.workspace.buffers:
            if pane.path is None:
                if pane.dirty:
                    self.notify(f"An untitled notebook has no file name: use :w name.py in it", severity="warning")
                    return
                continue
            if pane.dirty:
                pane.write_file()
        if then is not None:
            then()

    async def _ex_write_quit_all(self, arg: str = "") -> None:
        if arg:
            await self.pane.saved_as(arg)
        self._save_all(then=self.exit)

    async def _ex_write_quit(self, arg: str = "") -> None:
        pane = self.pane
        if arg:
            await pane.saved_as(arg)
            await self.close_window(force=True)
        elif pane.path is None:
            pane.save(then=lambda: self.run_worker(self.close_window(force=True)))
        else:
            await self.close_window(save=True)

    async def _ex_edit(self, arg: str = "", force: bool = False) -> None:
        arg = arg.strip()
        if not arg:
            await self.action_toggle_explorer()
            return
        await self.open_file(arg, "here", force=force)

    async def _ex_split(self, how: str, arg: str = "") -> None:
        arg = arg.strip()
        if arg:
            await self.open_file(arg, how)
        else:
            await self.new_pane(how)

    async def _ex_only(self) -> None:
        pane = self.pane
        await self.workspace.only(pane)
        pane.focus_pane()

    async def _ex_explore(self, arg: str = "") -> None:
        arg = arg.strip()
        if arg:
            await self.show_explorer(Path(arg).expanduser())
        else:
            await self.action_toggle_explorer()

    def _ex_list(self) -> None:
        self.notify("\n".join(self.buffer_lines()), title="Buffers  (:b N shows one, :bd N closes it)", timeout=10)

    def _ex_theme(self, arg: str = "") -> None:
        if not arg:
            self.notify(f"Theme: {self.theme}")
        elif (arg := THEME_ALIASES.get(arg, arg)) in self.available_themes:
            self.theme = arg
        else:
            names = ", ".join(sorted(self.available_themes)[:6])
            self.notify(f"Unknown theme {arg!r}. Try: {names}, …", severity="error")

    async def _run_ex(self, command: str | None) -> None:
        if not command:
            return
        name, _, arg = command.strip().partition(" ")
        if name.isdigit():
            mode = self._prompt_mode
            pane = self.pane
            pane._after_loaded(lambda: pane.goto_cell(int(name), mode, clamp=False))
            return
        for names, _, handler in self.ex_commands():
            if name in names:
                result = handler(arg.strip())
                if asyncio.iscoroutine(result):
                    await result
                return
        self.notify(f"Unknown command: {command}  (try :help)", severity="error")

    def action_pick_theme(self) -> None:
        """Theme picker with live preview; the config is written only on confirm."""
        if not isinstance(self.screen, ThemePicker):
            self.push_screen(ThemePicker(), self._theme_picked)

    def action_find_files(self, query: str = "") -> None:
        """space f f: the fuzzy file finder (imported on first use, like telescope)."""
        from .finder import open_finder

        open_finder(self, query)

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        return action not in getattr(self.screen, "APP_ACTIONS_OFF", ())

    def action_pick_env(self) -> None:
        """Env picker: switch the kernel's environment, create a venv, install packages."""
        envpicker.open_picker(self.pane)

    def _theme_picked(self, name: str | None) -> None:
        if name:
            config.save({"theme": name})
            self._loaded_theme = name

    def get_system_commands(self, screen):
        for command in super().get_system_commands(screen):
            if command.title != "Theme":
                yield command
        yield SystemCommand("Change theme", "Pick a colorscheme with live preview", self.action_pick_theme)
        for names, desc, handler in self.ex_commands():
            if names[0] in ("q", "q!", "qa!", "help", "set", "e!", "tabn", "tabp", "wq", "b", "bn", "bp", "bd!"):
                continue
            yield SystemCommand(f"{desc}  (:{names[0]})", f"Also available as :{names[0]}", self._palette(handler))

    def _palette(self, handler):
        def callback() -> None:
            result = handler("")
            if asyncio.iscoroutine(result):
                self.run_worker(result)

        return callback
