"""The Textual front end: notebook windows in tabs and splits, plus a file explorer.

GPython (the App) owns what is global: the theme, the vim mode, the status bar, the
prompts and the `:` commands. Each open notebook is a NotebookPane (pane.py) living in
the Workspace (windows.py), which lays panes out in vim-style tabs and splits. Key
bindings are forwarded to the active pane.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.color import Color
from textual.containers import Horizontal
from textual.reactive import Reactive
from textual.screen import ModalScreen, Screen
from textual.widgets import Header, Input, Label, Static, Tree
from textual.widgets._header import HeaderIcon

from . import bigtext, config, images, imageview
from .cell import Cell, CellSnapshot, ends_with_string
from .explorer import Explorer
from .help import HelpScreen
from .markdown import MarkdownView, markdown_theme
from .pane import NotebookPane
from . import envpicker
from .envpicker import KernelChip
from .themepicker import ThemePicker
from .themes import CUSTOM_APP_THEMES, DEFAULT_THEME, TERMINAL_THEME, THEME_ALIASES, editor_themes, palette_for
from .vim import CELL_MODE_COLOR, INSERT_COLOR, INSERT_CURSOR_SHAPES, MODE_CURSOR_COLORS, RESET_CURSOR_SHAPE, VimTextArea, _is_escape, parse_global, parse_line_command, parse_substitute, sync_terminal_cursor, write_to_terminal
from .windows import TabBar, Workspace

KEY_HINTS = "shift+enter run+next · ctrl+enter run · esc esc cell mode · / jump · ctrl+b files · ctrl+w splits · f1 help"
LAZY_MESSAGE = (
    "Lazy: a run runs that cell (and the stale cells it uses). Cells that use its variables are only "
    "marked stale; run them yourself with r 6, or f4 for all stale cells. f3 switches to reactive."
)
REACTIVE_MESSAGE = (
    "Reactive: a run also re-runs every cell that uses its variables, like marimo. f3 switches back to lazy."
)
AUTOINSTALL_MESSAGE = (
    "A cell that fails with ModuleNotFoundError gets its package installed into the kernel's env "
    "(uv pip install) and runs again. :set noautoinstall asks first instead."
)
ASK_INSTALL_MESSAGE = "A missing module opens the install prompt first (enter installs, esc skips). :set autoinstall makes it automatic."
_MODE_STYLES = {
    "insert": f"bold black on {INSERT_COLOR}",
    "normal": f"bold black on {MODE_CURSOR_COLORS['normal']}",
    "cell": f"bold white on {CELL_MODE_COLOR}",
    "files": "bold black on magenta",
    "outline": "bold black on magenta",
    "vars": "bold black on magenta",
    "debug": "bold white on red",
    "visual": f"bold black on {MODE_CURSOR_COLORS['visual']}",
    "v-line": f"bold black on {MODE_CURSOR_COLORS['visual']}",
    "v-block": f"bold black on {MODE_CURSOR_COLORS['visual']}",
    "replace": f"bold black on {MODE_CURSOR_COLORS['replace']}",
}
_CHIP_VIM_STYLES = {
    "insert": f"bold {INSERT_COLOR}",
    "replace": f"bold {MODE_CURSOR_COLORS['replace']}",
    "normal": f"bold {MODE_CURSOR_COLORS['normal']}",
    "visual": f"bold {MODE_CURSOR_COLORS['visual']}",
    "v-line": f"bold {MODE_CURSOR_COLORS['visual']}",
    "v-block": f"bold {MODE_CURSOR_COLORS['visual']}",
}
EXPLORER_WIDTH = 34
_VIM_MODES = ("normal", "visual", "v-line", "v-block", "insert", "replace")
_VIM_NORMAL_OWNS = ("delete_cell", "run_cell_ctrl_e", "toggle_explorer", "add_cell", "command_palette")
_VIM_INSERT_OWNS = ("delete_cell", "run_cell_ctrl_e", "add_cell", "command_palette", "window_prefix", "pick_theme", "interrupt")
_VIM_OWNS = {
    "normal": _VIM_NORMAL_OWNS,
    "visual": _VIM_NORMAL_OWNS,
    "v-line": _VIM_NORMAL_OWNS,
    "v-block": _VIM_NORMAL_OWNS,
    "insert": _VIM_INSERT_OWNS,
    "replace": _VIM_INSERT_OWNS,
}
_VIM_OWNED_ACTIONS = frozenset(_VIM_NORMAL_OWNS + _VIM_INSERT_OWNS)

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
    "T": "totab", "M": "fromtab",
    "z": "zoom", "ctrl+z": "zoom",
    "x": "exchange", "ctrl+x": "exchange",
    "r": "rotate", "ctrl+r": "rotate", "R": "rotate_back",
    "H": "edge", "J": "edge", "K": "edge", "L": "edge",
    " ": "manager", "space": "manager", "ctrl+@": "manager", "ctrl+space": "manager",
    "=": "equal",
    "+": "taller", "-": "shorter", ">": "wider", "<": "narrower",
}


def _debug_value(key: str, value) -> str:
    if key == "history":
        return f"{value / 1048576:g} MB"
    return str(value)


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
        Binding("ctrl+enter,ctrl+shift+enter", "run_cell", "Run", priority=True),
        Binding("ctrl+e", "run_cell_ctrl_e", "Run", show=False, priority=True),
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
        Binding("ctrl+h,backspace", "focus_left", "Window left", show=False, priority=True),
        Binding("ctrl+l", "focus_right", "Window right", show=False, priority=True),
        Binding("f1", "help", "Help"),
        Binding("ctrl+o", "toggle_outline", "Outline", show=False, priority=True),
        Binding("ctrl+v", "toggle_variables", "Variables", show=False, priority=True),
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
        Cell.show_timing = settings.get("timing") is True
        VimTextArea.insert_cursor = self._insert_cursor_setting(settings.get("insert_cursor"))
        bigtext.enabled = settings.get("experimental.true_markdown") is True
        VimTextArea.search = None

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

        self.lazy = settings.get("run_mode") != "reactive"
        self.autoinstall = settings.get("autoinstall") is True
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
        self._last_ex: str | None = None
        self._number_sequence = None
        self.cell_mode = False

    def get_default_screen(self) -> Screen:
        return NotebookScreen(id="_default")


    def post_display_hook(self) -> None:
        images.sync(self)

    def _record_terminal_output(self) -> None:
        """GPYTHON_TTY_LOG=file keeps every byte sent to the terminal; `cat file` replays the session."""
        path = os.environ.get("GPYTHON_TTY_LOG")
        driver = self._driver
        if not path or driver is None:
            return
        log = open(path, "ab")
        write = driver.write

        def record(data: str) -> None:
            log.write(data.encode("utf-8", "replace"))
            log.flush()
            write(data)

        driver.write = record

    @property
    def cursor_position(self):
        """Where Textual parks the terminal's cursor after each frame. While the bar cursor is shown
        it is read from the editor at that moment, so it never lags behind a scroll or a resize."""
        editor = VimTextArea.bar_editor
        if editor is not None and editor.is_attached:
            return editor.cursor_screen_offset
        return self._cursor_position

    @cursor_position.setter
    def cursor_position(self, offset) -> None:
        self._cursor_position = offset

    def _sync_terminal_cursor_on_screen_change(self, _screen=None) -> None:
        sync_terminal_cursor(self)

    def _restore_cursor_shape_on_suspend(self, _app=None) -> None:
        VimTextArea.bar_editor = None
        write_to_terminal(self, RESET_CURSOR_SHAPE)

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
        self._record_terminal_output()
        VimTextArea.mode = "normal"
        self.screen_change_signal.subscribe(self, self._sync_terminal_cursor_on_screen_change)
        self.app_suspend_signal.subscribe(self, self._restore_cursor_shape_on_suspend)
        self.app_resume_signal.subscribe(self, self._sync_terminal_cursor_on_screen_change)
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
        self.after_first_frame(self._prune_cell_layouts)

    def _prune_cell_layouts(self) -> None:
        """Forget plain-script cell layouts nobody used for a month (sidecar.prune), off the UI thread."""
        from . import sidecar

        self.run_worker(asyncio.to_thread(sidecar.prune), group="prune", exit_on_error=False)

    async def on_unmount(self) -> None:
        images.forget_all(self)
        write_to_terminal(self, RESET_CURSOR_SHAPE)
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
        """"cell" when a cell itself is selected, "files" in the explorer, "vars" or "debug" in the
        variables panel, else the vim mode."""
        focused = self.focused
        if isinstance(focused, Cell):
            return "cell"
        if isinstance(focused, Explorer):
            return "files"
        if isinstance(focused, Tree):
            return "outline"
        pane = self.pane if self.workspace.tabs else None
        panel = pane._variables if pane is not None else None
        if panel is not None and focused is not None and panel in focused.ancestors:
            return "debug" if pane.debug is not None else "vars"
        if not VimTextArea.enabled or VimTextArea.mode == "insert":
            return "insert"
        if VimTextArea.mode == "replace":
            return "replace"
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
        if self._number_sequence is not None:
            self._status_key = None
            self._status.update(self._number_sequence.status(), layout=False)
            return
        mode = self.ui_mode
        if pane.debug is not None and mode in ("normal", "cell"):
            mode = "debug"
        name = pane.title
        key = (mode, VimTextArea.enabled, name, self.lazy, pane.kernel_state, self.theme, pane.kernel_label)
        if key == self._status_key:
            return
        self._status_key = key
        show_mode = VimTextArea.enabled or mode in ("cell", "files", "outline", "vars", "debug")
        self.sub_title = name
        if self._kernel_chip is not None and pane.kernel_label != self._chip_label:
            self._chip_label = pane.kernel_label
            version, _, env = pane.kernel_label.partition(" · ")
            self._kernel_chip.update(Text.assemble((version, "dim"), (f" · {env}" if env else "", "bold"), (" ▾", "dim")))
        chip_mode = mode if mode in ("cell", "files", "outline", "vars", "debug") or VimTextArea.enabled else "edit"
        if self._mode_chip is not None and chip_mode != self._mode_chip_mode:
            self._mode_chip_mode = chip_mode
            if chip_mode in ("cell", "files", "outline", "edit", "vars", "debug"):
                text = Text(f" {chip_mode.upper()} ", style=_MODE_STYLES.get(chip_mode, _MODE_STYLES["insert"]))
            else:
                text = Text.assemble((" EDIT ", _MODE_STYLES["insert"]), (f" {chip_mode.upper()}", _CHIP_VIM_STYLES[chip_mode]))
            self._mode_chip.update(text, layout=False)
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
        in_side = not in_explorer and self.focused is not None and any(node.id == "side" for node in self.focused.ancestors)
        panes = self.workspace.tab_panes()
        if where in ("left", "right", "up", "down"):
            direction = {"left": "h", "right": "l", "up": "k", "down": "j"}[where]
            if in_side:
                if direction == "h":
                    current.focus_pane()
                elif direction in "jk":
                    current.focus_side(below=direction == "j")
                return
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
            if target is None and direction == "l" and current.focus_side():
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
            if target is not current:
                self.workspace.unzoom()
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
            await self.open_explorer()

    async def open_explorer(self) -> None:
        """Show and focus the explorer, rooted at the notebook's folder the first time."""
        root = None if self.explorer is not None else (self.pane.path.resolve().parent if self.pane.path else None)
        await self.show_explorer(root)

    async def action_focus_left(self) -> None:
        """ctrl+h (terminals send it as backspace): the window to the left, like ctrl+w h;
        from the leftmost window the explorer, opened when hidden. Insert mode keeps backspace."""
        if isinstance(self.focused, Explorer):
            return
        if self.workspace.neighbour(self.pane, "h") is None and not self.explorer_visible:
            await self.open_explorer()
        else:
            self.focus_window("left")

    def action_focus_right(self) -> None:
        """ctrl+l: the window to the right, like ctrl+w l; from the explorer, the leftmost window."""
        self.focus_window("right")

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
            case "fromtab":
                await self.merge_window(pane)
            case "zoom":
                self.toggle_zoom(pane)
            case "exchange":
                self.workspace.exchange(pane)
                pane.focus_pane()
            case "rotate" | "rotate_back":
                self.workspace.rotate(pane, 1 if command == "rotate" else -1)
                pane.focus_pane()
            case "edge":
                self.workspace.move_to_edge(pane, key)
                pane.focus_pane()
            case "manager":
                self.action_window_manager()
            case "equal":
                self.workspace.equalize()
            case "taller" | "shorter":
                self.workspace.resize(pane, False, 0.1 if command == "taller" else -0.1)
            case "wider" | "narrower":
                self.workspace.resize(pane, True, 0.1 if command == "wider" else -0.1)
            case _:
                self.notify(f"ctrl+w {key}: not a window command (space lists them all: s v h j k l w p q o z x r H J K L T M = + - < >)", severity="warning")

    def toggle_zoom(self, pane: NotebookPane | None = None) -> None:
        """ctrl+w z / :zoom: the window fills the tab, or the splits come back."""
        pane = pane or self.pane
        if len(self.workspace.tab_panes()) < 2 and not self.workspace.tab.zoom:
            self.notify("Only one window in this tab: nothing to zoom", timeout=2)
            return
        self.workspace.toggle_zoom(pane)
        pane.focus_pane()

    async def merge_window(self, pane: NotebookPane | None = None) -> None:
        """ctrl+w M / :tabmerge: the window goes back into the tab ctrl+w T took it from
        (else the previous tab), as a split."""
        pane = pane or self.pane
        if not await self.workspace.merge(pane):
            self.notify("No other tab to move this window into", timeout=2)
            return
        pane.focus_pane()

    def action_window_manager(self) -> None:
        """ctrl+w space / :windows: the floating window manager (imported on first use)."""
        from .manager import WindowManager

        if not isinstance(self.screen, WindowManager):
            self.push_screen(WindowManager())


    def start_run_by_number(self, pane: NotebookPane | None = None) -> None:
        """r (cell mode and vim normal mode; space r is a synonym): wait for a cell number, then run that cell."""
        from .runnumber import RunByNumber

        self._start_number_sequence(RunByNumber(self, pane or self.pane))

    def start_time_runs(self, pane: NotebookPane | None = None) -> None:
        """r t: wait for a number of runs, then time the current cell that many times."""
        from .runnumber import TimeRuns

        self._start_number_sequence(TimeRuns(self, pane or self.pane))

    def start_goto_by_number(self, pane: NotebookPane | None = None, count: int | None = None) -> None:
        """g (cell mode): wait for a cell number and select that cell; g g, g t, g T still work."""
        from .runnumber import GotoByNumber

        self._start_number_sequence(GotoByNumber(self, pane or self.pane, count))

    def _start_number_sequence(self, sequence) -> None:
        if self._number_sequence is not None:
            self._number_sequence.cancel()
        self._number_sequence = sequence
        self.update_status()

    def end_number_sequence(self, sequence) -> None:
        if self._number_sequence is sequence:
            self._number_sequence = None
            self.update_status()

    async def on_event(self, event: events.Event) -> None:
        """While a cell-number sequence waits, its keys go to it instead of the focused widget."""
        if (
            self._number_sequence is not None
            and isinstance(event, events.Key)
            and not event.is_forwarded
            and isinstance(self.screen, NotebookScreen)
        ):
            key = "escape" if _is_escape(event) else event.character if event.is_printable and event.character else event.key
            self._number_sequence.key(key)
            return
        await super().on_event(event)

    def _ex_timeit(self, arg: str = "") -> None:
        runs = int(arg) if arg.strip().isdigit() else 1
        self.pane.run_worker(self.pane.time_cell(runs), group="run")

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

    async def action_run_cell_ctrl_e(self) -> None:
        """ctrl+e outside the editor (in the editor it is vim's scroll / insert-mode copy)."""
        await self.pane.run_cell("stay")

    def _ex_segments(self, span: str, default_all: bool = False) -> list[tuple[VimTextArea, int, int]]:
        """An ex range as (editor, top row, bottom row) pieces, the notebook read as one file like
        vim's buffer: % (and no range when `default_all`) is every line of every cell, $ the
        notebook's last line, . the cursor line, N line N of the current cell (its gutter number),
        '<,'> the last visual area. No range is the cursor line in the editor and the whole
        selected cell in cell mode."""
        cells = [cell for cell in self.cells() if getattr(cell, "editor", None) is not None]
        current = self.current_cell()
        if current is None or current not in cells:
            raise ValueError("No cell to work on")
        index = cells.index(current)
        editor = current.editor
        in_editor = self.focused is editor

        def last_row(cell) -> int:
            return cell.editor.document.line_count - 1

        if span == "%" or (not span and default_all):
            return [(cell.editor, 0, last_row(cell)) for cell in cells]
        if not span:
            if in_editor:
                row = editor.cursor_location[0]
                return [(editor, row, row)]
            return [(editor, 0, last_row(current))]
        if span == "'<,'>":
            top, bottom = editor._range_rows(span)
            return [(editor, top, bottom)]

        def address(text: str) -> tuple[int, int]:
            if text == ".":
                return index, editor.cursor_location[0]
            if text == "$":
                return len(cells) - 1, last_row(cells[-1])
            return index, max(0, min(last_row(current), int(text) - 1))

        first, _, second = span.partition(",")
        start, end = sorted((address(first), address(second or first)))
        pieces = []
        for position in range(start[0], end[0] + 1):
            cell = cells[position]
            top = start[1] if position == start[0] else 0
            bottom = end[1] if position == end[0] else last_row(cell)
            pieces.append((cell.editor, top, bottom))
        return pieces

    def _substitute(self, span: str, pattern: str | None, replacement: str | None, flags: str) -> None:
        """:s over the ex range (_ex_segments): :%s changes every cell of the notebook, like vim's whole file."""
        from .vim import prepare_substitute

        try:
            segments = self._ex_segments(span)
            prepared = prepare_substitute(pattern, replacement, flags)
        except (ValueError, re.error) as error:
            self.notify(str(error), severity="error")
            return
        count = lines = cells = 0
        for editor, top, bottom in segments:
            made, rows, _ = editor.substitute_rows(top, bottom, *prepared)
            count += made
            lines += rows
            cells += bool(made)
        flags = prepared[2]
        where = f" in {cells} cells" if cells > 1 else ""
        if not count:
            self.notify(f"Pattern not found: {VimTextArea.last_pattern}", severity="warning")
        elif "n" in flags:
            self.notify(f"{count} match{'es' if count > 1 else ''} on {lines} line{'s' if lines > 1 else ''}{where}")
        elif count > 2 or cells > 1:
            self.notify(f"{count} substitutions on {lines} line{'s' if lines > 1 else ''}{where}")

    def _global(self, span: str, invert: bool, pattern: str, command: str) -> None:
        """:g/pattern/command (:g! and :v invert): every cell of the notebook unless a range is given.
        Without a command, the matching lines are listed."""
        from .vim import compile_pattern

        try:
            pattern = pattern or VimTextArea.last_pattern
            if not pattern:
                raise ValueError("No previous pattern")
            regex = compile_pattern(pattern)
            segments = self._ex_segments(span, default_all=True)
        except (ValueError, re.error) as error:
            self.notify(str(error), severity="error")
            return
        VimTextArea.last_pattern = pattern
        matches = [(editor, editor.matching_rows(top, bottom, regex, invert)) for editor, top, bottom in segments]
        if not any(rows for _, rows in matches):
            self.notify(f"Pattern not found: {pattern}", severity="warning")
            return
        if command in ("", "p", "print", "#", "nu", "number"):
            labels = {cell.editor: cell.label for cell in self.cells() if getattr(cell, "editor", None) is not None}
            listing = [f"{labels.get(editor, '?')}:{row + 1}  {editor.document.get_line(row)}" for editor, rows in matches for row in rows]
            self._show_text(f":g/{pattern}/", "\n".join(listing))
            return
        try:
            for editor, rows in matches:
                if rows:
                    editor.global_command(rows, command)
        except (ValueError, re.error) as error:
            self.notify(str(error), severity="error")

    def _line_command(self, span: str, name: str, arg: str) -> None:
        """:d :j :> :< :m :t :co :normal over the ex range; :m and :t addresses count in the cell
        of the range's first line."""
        try:
            segments = self._ex_segments(span)
            for editor, top, bottom in reversed(segments):
                editor.line_command(top, bottom, name, arg)
        except (ValueError, re.error) as error:
            self.notify(str(error), severity="error")

    def _show_text(self, title: str, text: str, ansi: bool = False) -> None:
        from .textview import TextView

        self.push_screen(TextView(title, text, ansi=ansi))

    @on(VimTextArea.HelpRequested)
    def _help_requested(self, event: VimTextArea.HelpRequested) -> None:
        self.run_worker(self._show_help(event), group="inspect", exclusive=True)

    async def _show_help(self, event: VimTextArea.HelpRequested) -> None:
        """K: the kernel's help for the name under the cursor (what `name?` prints in IPython)."""
        kernel = event.editor._kernel()
        if kernel is None or not kernel.ready.is_set():
            self.notify("The kernel is not running yet", severity="warning")
            return
        text = await kernel.inspect(event.code, event.offset)
        if not text:
            self.notify(f"No help for {event.name} (run the cell that defines it first?)", severity="warning")
            return
        self._show_text(f"K  {event.name}", text, ansi=True)

    @on(VimTextArea.FoldRequested)
    def _fold_requested(self, event: VimTextArea.FoldRequested) -> None:
        self.fold_cells(event.action)

    def fold_cells(self, action: str) -> None:
        """Folds are cells: "close" / "toggle" hide this cell's code (markdown: fold it) and select
        the cell; "close_all" / "open_all" do it for every cell; "toggle_all" picks by the current state."""
        cells = self.cells()
        current = self.current_cell()
        if action == "toggle_all":
            action = "open_all" if any(cell.code_hidden or getattr(cell, "folded", False) for cell in cells) else "close_all"
        targets = cells if action.endswith("_all") else [current] if current is not None else []
        if action == "toggle":
            hide = current is not None and not (current.code_hidden or getattr(current, "folded", False))
        else:
            hide = action in ("close", "close_all")
        for cell in targets:
            if cell.kind == "markdown":
                cell.set_folded(hide)
            else:
                cell.set_code_hidden(hide)
        self.pane.mark_dirty()
        if hide and current is not None and self.focused is current.editor:
            current.focus(scroll_visible=False)

    def _ex_digraphs(self, arg: str = "") -> None:
        from .digraphs import table

        pairs = sorted(table().items(), key=lambda item: item[1])
        columns = [f"{pair} {char!s:<2}" for pair, char in pairs if char.isprintable() and not char.isspace()]
        rows = ["    ".join(columns[i : i + 8]) for i in range(0, len(columns), 8)]
        self._show_text("Digraphs: insert mode ctrl+k, then the two characters", "\n".join(rows))

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

    def set_timing(self, on: bool) -> None:
        """t in cell mode, :set time|notime: show each cell's run time in its header; saved as `timing`."""
        Cell.show_timing = on
        for pane in self.workspace.buffers:
            for cell in pane.cells():
                cell.refresh_header()
        config.save({"timing": on})

    def show_search(self, regex) -> None:
        """Paint the matches of `regex` in every editor (None: no highlight); repaints only on a change."""
        if regex == VimTextArea.search:
            return
        VimTextArea.search = regex
        for pane in self.workspace.buffers:
            for cell in pane.cells():
                if cell._editor is not None:
                    cell._editor.repaint_search()

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

    def action_toggle_outline(self) -> None:
        self.pane.toggle_outline()

    def action_toggle_variables(self) -> None:
        self.pane.toggle_variables()

    def _ex_debug(self, arg: str = "") -> None:
        """:debug: post-mortem where the current cell failed, else run it paused on its first line."""
        pane = self.pane
        pane.run_worker(pane.debug_cell(), group="run")

    def _ex_stop(self, arg: str = "") -> None:
        """:stop if EXPR: while paused, run until EXPR is true on a line of a cell."""
        expr = arg.strip()
        if expr.startswith("if "):
            expr = expr[3:].strip()
        if self.pane.debug is None:
            self.notify(":stop if works while the debugger is paused (breakpoint(), r d, :debug)", severity="warning")
        elif expr:
            self.pane.debug_command(f"_gp_until {expr}")

    def action_toggle_lazy(self) -> None:
        self.set_lazy(not self.lazy)

    def set_autoinstall(self, on: bool) -> None:
        """On: a cell that fails with ModuleNotFoundError gets its package installed (uv pip install
        into the kernel's env) and runs again without a question. Off (the default): the install
        prompt opens first, prefilled. Saved."""
        self.autoinstall = on
        config.save({"autoinstall": on})
        self.notify(AUTOINSTALL_MESSAGE if on else ASK_INSTALL_MESSAGE, title="Missing modules")

    def set_lazy(self, on: bool) -> None:
        """Lazy (the default): a run runs that cell and the stale cells it uses; cells that use its
        variables are only marked stale, you run them yourself (r 6, f4). Reactive: they re-run too. Saved."""
        self.lazy = on
        config.save({"run_mode": "lazy" if on else "reactive"})
        self.update_status()
        self.notify(LAZY_MESSAGE if on else REACTIVE_MESSAGE, title="Run mode")

    async def action_restart_kernel(self) -> None:
        await self.pane.restart_kernel()


    @on(VimTextArea.ModeChanged)
    def _mode_changed(self, event: VimTextArea.ModeChanged) -> None:
        """The status bar, and the edited cell's bar, which takes the colour of the mode."""
        self.update_status()
        cell = next((node for node in event.editor.ancestors if isinstance(node, Cell)), None)
        if cell is not None:
            cell.paint_bar()

    @on(VimTextArea.SearchRequested)
    def _search(self, event: VimTextArea.SearchRequested) -> None:
        self._open_search(backward=event.backward)

    @on(VimTextArea.SearchNext)
    def _search_next(self, event: VimTextArea.SearchNext) -> None:
        mode = self.ui_mode
        if event.pattern is None:
            self.pane.search_next(event.step, mode)
        else:
            self.pane.search(event.pattern, event.step, mode)

    @on(VimTextArea.ExCommand)
    def _ex(self, event: VimTextArea.ExCommand) -> None:
        self._open_ex(event.initial)

    @on(VimTextArea.ExRun)
    async def _run_ex_message(self, event: VimTextArea.ExRun) -> None:
        """ZZ ZQ & g& @: from the editor: an ex command without the prompt (None repeats the last one)."""
        self._prompt_mode = self.ui_mode
        command = event.command if event.command is not None else self._last_ex
        if command:
            await self._run_ex(command)

    @on(VimTextArea.FindFiles)
    def _find_files(self) -> None:
        self.action_find_files()

    @on(VimTextArea.RunByNumber)
    def _run_by_number(self) -> None:
        self.start_run_by_number()

    @on(VimTextArea.TabSwitch)
    def _tab_switch(self, event: VimTextArea.TabSwitch) -> None:
        self.tab_switch(event.tab, event.step)

    def _open_ex(self, initial: str = "") -> None:
        self._prompt_mode = self.ui_mode
        self.push_screen(Prompt(":", value=initial), self._run_ex)

    def _open_search(self, backward: bool = False) -> None:
        self._prompt_mode = self.ui_mode
        if backward:
            self.push_screen(Prompt("?", placeholder="text to search backward"), self._jump_backward)
        else:
            self.push_screen(Prompt("/", placeholder="cell number or text to search"), self._jump)

    def _jump(self, target: str | None) -> None:
        if target:
            self.pane.jump(target, self._prompt_mode)

    def _jump_backward(self, target: str | None) -> None:
        if target:
            self.pane.jump(target, self._prompt_mode, backward=True)

    def ex_commands(self) -> list[tuple[tuple[str, ...], str, object]]:
        """(names, description, handler). Shared by ':' commands, the palette and help.

        Handlers take the rest of the command line as one argument and may be async.
        `:N` (a number) jumps to the N-th cell, like `/N`.
        """
        return [
            (("w", "write", "w!", "write!"), "Save notebook (:w name.py saves as)", self._ex_write),
            (("wa", "wall"), "Save every open notebook", lambda arg="": self._save_all()),
            (("wq", "x", "wq!", "x!", "exit"), "Save and close this window", lambda arg="": self._ex_write_quit(arg)),
            (("wqa", "wqall", "xa", "xall", "wqa!", "xa!"), "Save every notebook and quit (:wqa name.py names an untitled one)", self._ex_write_quit_all),
            (("q", "quit", "close", "clo"), "Close this window (quits when it is the last; asks if unsaved)", lambda arg="": self.close_window()),
            (("q!", "quit!"), "Close this window without saving", lambda arg="": self.close_window(force=True)),
            (("qa", "qall", "quitall"), "Quit (asks if anything is unsaved)", lambda arg="": self.action_quit()),
            (("qa!", "qall!"), "Quit without saving", lambda arg="": self.exit()),
            (("e", "edit", "open"), "Open a notebook here (:e file; :e dir or :e alone opens the explorer)", self._ex_edit),
            (("e!", "edit!"), "Open a notebook here, discarding unsaved changes", lambda arg="": self._ex_edit(arg, force=True)),
            (("sp", "split"), "Horizontal split (:sp file opens it there)", lambda arg="": self._ex_split("split", arg)),
            (("vs", "vsp", "vsplit"), "Vertical split (:vs file opens it there)", lambda arg="": self._ex_split("vsplit", arg)),
            (("on", "only"), "Close every other window in this tab", lambda arg="": self._ex_only()),
            (("zoom",), "Zoom this window to fill the tab, or restore the splits (ctrl+w z)", lambda arg="": self.toggle_zoom()),
            (("windows", "wins", "wm"), "Window manager: every tab, window and hidden buffer in a floating list (ctrl+w space)", lambda arg="": self.action_window_manager()),
            (("tabmerge", "tabjoin"), "Move this window back into the tab it came from (ctrl+w M)", lambda arg="": self.merge_window()),
            (("tabmove", "tabm"), "Move this tab: :tabmove 0 first, :tabmove last, :tabmove +1 / -1, :tabmove N after tab N", lambda arg="": self._ex_tabmove(arg)),
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
            (("noh", "nohlsearch"), "Stop highlighting search matches (n shows them again)", lambda arg="": self.pane.clear_search_highlight()),
            (("run", "runall"), "Run all cells; :run 6 runs cell c6 (stale cells it uses first)", self._ex_run),
            (("timeit", "profile"), "Time this cell: run it n times and print mean, median, std, variance, p50 / p90 / p99, min, max (:timeit 5; r t 5)", self._ex_timeit),
            (("runabove",), "Run the cells above the current one", lambda arg="": self.pane.run_relative(False)),
            (("runbelow",), "Run the current cell and the cells below", lambda arg="": self.pane.run_relative(True, inclusive=True)),
            (("stale", "runstale"), "Run stale cells", lambda arg="": self.action_run_stale()),
            (("restart",), "Restart kernel", lambda arg="": self.action_restart_kernel()),
            (("clear",), "Clear all outputs", lambda arg="": self.pane.clear_outputs()),
            (("img", "image"), "View this cell's image full screen (v in cell mode; :img 2 the second); :img save [N] [file] saves this cell's image(s) as PNG next to the notebook, :img open [N] opens them in the browser; :img save all / :img open all take every cell's", self._ex_image),
            (("set",), "Options: vim novim lazy nolazy nu nonu time notime autoinstall noautoinstall cursor=bar|underline|block; debugger history: debug_detail=full|balanced|lean|auto (full is the default; auto is full until 10,000 steps, then balanced), debug_history=200MB debug_events debug_thinning=on|off debug_repr_chars debug_strip_items debug_grid_size debug_stats=watched|cpu|off, debug_<key>? shows the value in effect", self._ex_set),
            (("copy", "yank"), "Copy to the system clipboard: :copy all the whole notebook as plain code, markdown as comments; :copy all metadata keeps the # %% cell lines (the file's text; a .ipynb as # %% code); :copy this cell", self._ex_copy),
            (("time", "timing"), "Show / hide each cell's run time in its header (t in cell mode; saved)", lambda arg="": self.set_timing(not Cell.show_timing)),
            (("outline", "tags"), "Outline panel: each cell with what it defines, live types after a run, its edges; enter jumps (ctrl+o)", lambda arg="": self.action_toggle_outline()),
            (("vars", "variables"), "Variables panel: name, type, shape, value, defining cell; enter shows the value, g goes to the cell, d deletes (ctrl+v)", lambda arg="": self.action_toggle_variables()),
            (("debug",), "Debugger: post-mortem where this cell failed, else run it paused on its first line (r d; breakpoint() in code)", self._ex_debug),
            (("stop",), "While paused: :stop if EXPR runs until EXPR is true (b in the debugger)", self._ex_stop),
            (("lazy",), "Toggle run mode: lazy (dependents only marked stale) / reactive (they re-run); saved (f3)", lambda arg="": self.action_toggle_lazy()),
            (("new",), "New cell below", lambda arg="": self.action_add_cell()),
            (("delete", "del", "d"), "Delete current cell (in cell mode; in the editor :d deletes lines as in vim)", lambda arg="": self.action_delete_cell()),
            (("foldclose", "foldc"), "Fold: hide this cell's code, fold a markdown cell (zc); :foldclose all every cell (zM)", lambda arg="": self.fold_cells("close_all" if arg.strip() == "all" else "close")),
            (("foldopen", "foldo"), "Unfold this cell; :foldopen all every cell (zR)", lambda arg="": self.fold_cells("open_all" if arg.strip() == "all" else "open")),
            (("digraphs", "dig"), "List the digraphs (insert mode ctrl+k then two characters)", self._ex_digraphs),
            (("md", "markdown"), "Cell → Markdown", lambda arg="": self._ex_kind("markdown")),
            (("code",), "Cell → Code", lambda arg="": self._ex_kind("code")),
            (("hide",), "Hide this cell's code, or fold a markdown cell (h in cell mode; code: saved as marimo hide_code)", lambda arg="": self._ex_hide(True)),
            (("show",), "Show this cell's code, or unfold a markdown cell", lambda arg="": self._ex_hide(False)),
            (("interrupt",), "Interrupt running code", lambda arg="": self.pane.kernel.interrupt()),
            (("theme",), "Set theme (:theme name) or show the current one", self._ex_theme),
            (("env", "kernel", "envs"), "Python environment for this kernel: pick (f6), or :env name|path switches", lambda arg="": envpicker.ex_env(self.pane, arg)),
            (("venv",), "New venv with python -m venv, then switch to it (:venv folder [python])", lambda arg="": envpicker.ex_venv(self.pane, arg)),
            (("uv",), "uv for this kernel: :uv add pkg · :uv pip install pkg · :uv venv [folder] [python]", lambda arg="": envpicker.ex_uv(self.pane, arg)),
            (("pip",), "Install into this kernel's env (:pip numpy; :pip alone offers what the current cell is missing; uses uv when available; :pip list, uninstall)", lambda arg="": envpicker.ex_pip(self.pane, arg)),
            (("find", "fin", "Telescope"), "Find files by fuzzy name (space f f; :find text starts the search)", lambda arg="": self.action_find_files(arg)),
            (("help", "h"), "Keys and commands", lambda arg="": self.action_help()),
        ]

    def view_image(self, cell: Cell | None = None, index: int = 0) -> bool:
        """Open the image viewer on `cell` (default: the current one). False when it has no image."""
        cell = cell or self.current_cell()
        pngs = cell.images if cell else []
        if not pngs:
            self.notify("No image in this cell", severity="warning")
            return False
        self.push_screen(imageview.ImageViewer(pngs, index, self._image_stem(cell), self._image_folder()))
        return True

    def _image_stem(self, cell: Cell) -> str:
        return f"{self.pane.path.stem if self.pane.path else 'untitled'}-{cell.label}"

    def _image_folder(self) -> Path:
        return self.pane.path.parent if self.pane.path else Path.cwd()

    def _ex_image(self, arg: str = "") -> None:
        """:img [N] · :img save [N] [file] · :img open [N] on this cell's images; `all` instead of N: every cell's."""
        words = arg.split()
        action = words.pop(0) if words and words[0] in ("save", "open", "browser") else "view"
        if words and words[0] == "all" and action != "view":
            self._ex_image_all(action)
            return
        number = int(words.pop(0)) if words and words[0].isdigit() else None
        cell = self.current_cell()
        pngs = cell.images if cell else []
        if not pngs:
            self.notify("No image in this cell", severity="warning")
            return
        if number is not None and not 1 <= number <= len(pngs):
            self.notify(f"This cell has {len(pngs)} image{'s' if len(pngs) > 1 else ''}", severity="warning")
            return
        if action == "view":
            self.view_image(cell, (number or 1) - 1)
            return
        stem = self._image_stem(cell)
        chosen = [number - 1] if number else range(len(pngs))
        names = [imageview.stem_for(stem, i, len(pngs)) for i in chosen]
        try:
            if action == "save":
                if words and len(names) > 1:
                    self.notify("Give the image number with a file name: :img save 2 plot.png", severity="warning")
                    return
                targets = [Path(words[0])] if words else [imageview.unused_path(self._image_folder(), n) for n in names]
                saved = [imageview.save_png(pngs[i], target) for i, target in zip(chosen, targets)]
                self.notify("Saved " + ", ".join(str(path) for path in saved))
            else:
                for i, name in zip(chosen, names):
                    imageview.open_in_browser(pngs[i], name)
        except OSError as error:
            self.notify(str(error), severity="error")

    def _ex_image_all(self, action: str) -> None:
        """Save or open every image of every cell, named <notebook>-<cell>[-N].png."""
        found = [(cell, png, i, len(cell.images)) for cell in self.pane.cells() for i, png in enumerate(cell.images)]
        if not found:
            self.notify("No image in this notebook", severity="warning")
            return
        names = [imageview.stem_for(self._image_stem(cell), i, count) for cell, _, i, count in found]
        try:
            if action == "save":
                saved = [imageview.save_png(png, imageview.unused_path(self._image_folder(), name)) for (_, png, _, _), name in zip(found, names)]
                self.notify(f"Saved {len(saved)} image{'s' if len(saved) > 1 else ''} in {self._image_folder()}")
            else:
                for (_, png, _, _), name in zip(found, names):
                    imageview.open_in_browser(png, name)
        except OSError as error:
            self.notify(str(error), severity="error")

    @on(imageview.ImageCard.View)
    def _card_view(self, event: imageview.ImageCard.View) -> None:
        cell = event.card.query_ancestor(Cell)
        try:
            imageview.open_in_browser(event.card.gp_png, self._card_stem(event.card, cell))
        except OSError as error:
            self.notify(str(error), severity="error")

    @on(imageview.ImageCard.Download)
    def _card_download(self, event: imageview.ImageCard.Download) -> None:
        cell = event.card.query_ancestor(Cell)
        default = imageview.unused_path(self._image_folder(), self._card_stem(event.card, cell))
        png = event.card.gp_png
        self.push_screen(Prompt("Save image as: ", value=str(default)), lambda text: self._save_image(png, text))

    def _card_stem(self, card: imageview.ImageCard, cell: Cell) -> str:
        pngs = cell.images
        return imageview.stem_for(self._image_stem(cell), pngs.index(card.gp_png), len(pngs))

    def _save_image(self, png: bytes, text: str | None) -> None:
        if not text:
            return
        try:
            self.notify(f"Saved {imageview.save_png(png, Path(text))}")
        except OSError as error:
            self.notify(str(error), severity="error")

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
            self.set_lazy(option == "lazy")
        elif option in ("nu", "number", "nonu", "nonumber"):
            self.set_line_numbers(not option.startswith("no"))
        elif option in ("time", "timing", "notime", "notiming"):
            self.set_timing(not option.startswith("no"))
        elif option in ("autoinstall", "noautoinstall"):
            self.set_autoinstall(option == "autoinstall")
        elif option.startswith("cursor"):
            self.set_insert_cursor(option.partition("=")[2].strip())
        elif option.startswith("debug_"):
            self.set_debug_option(option)
        else:
            self.notify(f"Unknown option: {option or '(none)'}  (vim novim lazy nolazy nu nonu time notime autoinstall noautoinstall cursor=bar|underline|block debug_detail=full|balanced|lean|auto debug_<limit>=…)", severity="error")

    def set_debug_option(self, option: str) -> None:
        """:set debug_detail=full|balanced|lean|auto picks the recorder's preset; :set debug_<limit>=value
        overrides one limit (debug_history=20MB, debug_events, debug_thinning=on|off,
        debug_repr_chars, debug_strip_items, debug_grid_size, debug_stats=watched|cpu|off; an empty
        value goes back to the preset); :set debug_<key>? shows the value in effect and where it
        comes from. Saved; the kernels use it from their next recording."""
        from .kernel import debug_settings
        from .kernel_side import DETAILS, SETTINGS, resolve

        name, equals, value = option.partition("=")
        asking = name.endswith("?")
        name = name.rstrip("?").strip()
        key = name[len("debug_"):]
        if key != "detail" and key not in SETTINGS:
            self.notify(f"Unknown option {name}: debug_detail, " + ", ".join(f"debug_{k}" for k in SETTINGS), severity="error")
            return
        settings = debug_settings()
        if asking or not equals:
            limits, sources = resolve(settings)
            if key == "detail":
                self.notify(f"debug_detail = {limits['detail']}" + ("" if "debug_detail" in settings else " (the default)"))
            else:
                where = "override" if sources[key] == "override" else f"from the {sources[key]} preset"
                self.notify(f"debug_{key} = {_debug_value(key, limits[key])} ({where})")
            return
        value = value.strip()
        if key == "detail" and value not in DETAILS:
            self.notify(f"debug_detail is one of: {', '.join(DETAILS)}", severity="error")
            return
        if key != "detail" and value and resolve({name: value})[1][key] != "override":
            self.notify(f"Not a value for {name}: {value}", severity="error")
            return
        config.save({name: value})
        settings[name] = value
        for pane in self.workspace.buffers:
            pane.run_worker(pane.kernel.configure_debugger(settings), group="debug-settings")
        limits = resolve(settings)[0]
        shown = limits["detail"] if key == "detail" else _debug_value(key, limits[key])
        self.notify(f"debug_{key} = {shown}: the next recording uses it", title="Debugger")

    @staticmethod
    def _insert_cursor_setting(value) -> str:
        return value if value in INSERT_CURSOR_SHAPES else "block"

    def set_insert_cursor(self, shape: str) -> None:
        """:set cursor=bar|underline|block: the cursor in insert mode; saved as `insert_cursor`."""
        if shape not in INSERT_CURSOR_SHAPES:
            self.notify(f"insert cursor is {VimTextArea.insert_cursor}; choose one of: {', '.join(INSERT_CURSOR_SHAPES)}", severity="warning" if shape else "information")
            return
        VimTextArea.insert_cursor = "block"
        sync_terminal_cursor(self)
        VimTextArea.insert_cursor = shape
        sync_terminal_cursor(self)
        config.save({"insert_cursor": shape})

    def _ex_copy(self, arg: str = "") -> None:
        """:copy all puts the whole notebook on the system clipboard as plain code (:copy all metadata keeps the # %% lines); :copy alone, this cell."""
        what = " ".join(arg.split())
        if what in ("all", "all clean", "all metadata"):
            text = self.pane.copy_text(clean=what != "all metadata")
            label = self.pane.path.name if self.pane.path else "the notebook"
        elif not what:
            cell = self.current_cell()
            if cell is None:
                self.notify("No cell to copy", severity="warning")
                return
            text, label = cell.source, f"cell {cell.label}"
        else:
            self.notify(":copy all copies the whole notebook without # %% lines, :copy all metadata with them; :copy alone, this cell", severity="warning")
            return
        self.copy_to_clipboard(text)
        lines = text.count("\n") + (not text.endswith("\n") and bool(text))
        self.notify(f"Copied {label}: {lines} line{'s' if lines != 1 else ''}")

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

    def _ex_tabmove(self, arg: str = "") -> None:
        arg = arg.strip()
        workspace = self.workspace
        here = workspace.index
        if arg in ("", "$", "last"):
            to = len(workspace.tabs) - 1
        elif arg[0] in "+-" and arg[1:].isdigit():
            to = here + int(arg)
        elif arg.isdigit():
            n = int(arg)
            to = n if n <= here else n - 1
        else:
            self.notify(f"tabmove {arg}: give 0, N, +N, -N or last", severity="warning")
            return
        workspace.move_tab(here, to)

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
        """Run an ex command. A save / quit name (w, q, x ...) that matches nothing exactly is tried
        in lower case, so the common slips :Q! :WQ! :Wq :W :X :QA! work like :q! :wq! ... Only that
        family: :D must not become :d (delete cell)."""
        if not command:
            return
        self._last_ex = command
        if (substitute := parse_substitute(command)) is not None:
            self._substitute(*substitute)
            return
        if (global_command := parse_global(command)) is not None:
            self._global(*global_command)
            return
        line_command = parse_line_command(command)
        cell_delete = line_command is not None and line_command[0] == "" and line_command[1] in ("d", "delete") and not isinstance(self.focused, VimTextArea)
        if line_command is not None and not cell_delete:
            self._line_command(*line_command)
            return
        name, _, arg = command.strip().partition(" ")
        if name.isdigit():
            mode = self._prompt_mode
            pane = self.pane
            pane._after_loaded(lambda: pane.goto_cell(int(name), mode, clamp=False))
            return
        commands = self.ex_commands()
        handler = next((h for names, _, h in commands if name in names), None)
        if handler is None and name[:1].lower() in "wqx":
            handler = next((h for names, _, h in commands if name.lower() in names), None)
        if handler is not None:
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
        if action in getattr(self.screen, "APP_ACTIONS_OFF", ()):
            return False
        if action in ("focus_left", "focus_right"):
            return self._window_keys_apply(action == "focus_left")
        if action in ("toggle_outline", "toggle_variables"):
            return self._window_keys_apply(True) and self.ui_mode not in _VIM_MODES
        if action in _VIM_OWNED_ACTIONS and action in _VIM_OWNS.get(self.ui_mode, ()) and isinstance(self.screen, NotebookScreen):
            return parameters != () if action == "add_cell" else False
        return True

    def _window_keys_apply(self, needs_backspace: bool) -> bool:
        """ctrl+h, ctrl+l, ctrl+o and ctrl+v act only on the notebook screen, never in a text field;
        ctrl+h arrives as backspace, ctrl+o is vim's one-shot command and ctrl+v the editor's
        paste, so insert mode keeps them."""
        if not isinstance(self.screen, NotebookScreen) or isinstance(self.focused, Input):
            return False
        return not (needs_backspace and self.ui_mode in ("insert", "replace"))

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
