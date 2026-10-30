"""The Textual front end: a column of cells plus a variables panel."""

from __future__ import annotations

import asyncio
import itertools
import time
from dataclasses import dataclass
from pathlib import Path

from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import DataTable, Header, Input, Label, Static
from textual.widgets._header import HeaderIcon

from . import config, notebook
from .graph import Graph, build
from .help import HelpScreen
from .kernel import Kernel
from .outputs import OutputArea
from .themes import CUSTOM_APP_THEMES, DEFAULT_THEME, editor_themes, palette_for
from .vim import VimTextArea, _is_escape

STATUS_ICONS = {
    "idle": ("○", "dim"),
    "stale": ("◌", "yellow"),
    "queued": ("…", "cyan"),
    "running": ("●", "cyan"),
    "ok": ("✓", "green"),
    "error": ("✗", "red"),
    "skipped": ("⊘", "red"),
}
KEY_HINTS = "shift+enter run+next · ctrl+enter run · esc esc cell mode · / jump · f1 help · ctrl+p palette"
INITIAL_CELLS = 12  # cells composed before the first frame; the rest mount right after
MOUNT_BATCH = 10
MAX_UNDO = 50
_MODE_STYLES = {"insert": "bold green", "normal": "bold yellow", "cell": "bold cyan"}

# Non-printable keys that cell mode handles; others (f-keys, ctrl+...) reach app bindings.
_CELL_KEYS = {"escape", "enter", "up", "down", "shift+j", "shift+k", "shift+o", "question_mark"}

_ids = itertools.count(1)


@dataclass
class CellSnapshot:
    """Everything needed to bring a deleted (or copied) cell back."""

    index: int
    source: str
    kind: str
    ran: bool
    outputs: list[tuple[str, object]]


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


class Cell(Vertical):
    """One code cell: a header line, an editor and an output area.

    The cell itself can take focus: that is "cell mode" (Jupyter's command mode),
    where keys act on whole cells (see keymap.py and GPython.cell_command).
    """

    can_focus = True

    def __init__(self, source: str = "", kind: str = "code") -> None:
        self.cid = f"c{next(_ids)}"  # internal, stable graph key
        super().__init__(id=self.cid)
        self.initial_source = source
        self.kind = kind
        self.label = self.cid  # what the header shows: the display position, see set_label
        self.ran_source: str | None = None
        self.last_defs: set[str] = set()
        self.status = "idle"
        self.info = ""
        self.exec_count = 0
        self.duration: float | None = None
        self._run_start = 0.0
        self._ticker: Timer | None = None
        self._log: list[tuple] = []  # outputs received, for undo/paste replay
        self._log_clear_pending = False  # clear_output(wait=True) seen, no output since
        self._header: Static | None = None
        self._editor: VimTextArea | None = None
        self._outputs: OutputArea | None = None
        self._note: Static | None = None
        self._header_key: tuple | None = None

    def compose(self) -> ComposeResult:
        self._header = Static(classes="cell-header")
        self._editor = VimTextArea(self.initial_source)
        self._outputs = OutputArea()
        yield self._header
        yield self._editor
        yield self._outputs

    def on_mount(self) -> None:
        self._render_header()

    @property
    def editor(self) -> VimTextArea:
        return self._editor or self.query_one(VimTextArea)

    @property
    def output_box(self) -> OutputArea:
        return self._outputs or self.query_one(OutputArea)

    @property
    def source(self) -> str:
        # The editor does not exist until the cell is composed.
        return self._editor.text if self._editor is not None else self.initial_source

    @property
    def output_text(self) -> str:
        return self.output_box.text

    def set_label(self, n: int) -> None:
        """Show the 1-based display position (c1, c2, ...) in the header."""
        label = f"c{n}"
        if label != self.label:
            self.label = label
            self._render_header()

    def set_status(self, status: str) -> None:
        if status == self.status:
            return
        self.remove_class(f"-{self.status}")
        self.add_class(f"-{status}")
        self.status = status
        self._render_header()

    def set_info(self, graph: Graph) -> None:
        parts = []
        if graph.defs.get(self.cid):
            parts.append("defines " + ", ".join(sorted(graph.defs[self.cid])))
        if graph.refs.get(self.cid):
            parts.append("uses " + ", ".join(sorted(graph.refs[self.cid])))
        if graph.mutates.get(self.cid):
            parts.append("mutates " + ", ".join(sorted(graph.mutates[self.cid])))
        self.info = "  ·  ".join(parts)
        self._render_header()

    # ── running ──────────────────────────────────────────────────────────

    def begin_run(self, count: int) -> None:
        """Mark the cell running as In [count]; the header counts seconds while it runs."""
        self.exec_count = count
        self.duration = None
        self._run_start = time.perf_counter()
        self.set_status("running")
        if self._ticker is None:
            self._ticker = self.set_interval(1, self._render_header)

    def end_run(self, status: str) -> None:
        self.duration = time.perf_counter() - self._run_start
        if self._ticker is not None:
            self._ticker.stop()
            self._ticker = None
        self.set_status(status)
        self._render_header()

    # ── outputs ──────────────────────────────────────────────────────────

    def clear_output(self) -> None:
        self._log.clear()
        self._log_clear_pending = False
        self.output_box.clear()
        self._update_note()

    def show_error(self, message: str) -> None:
        self._log[:] = [("_error", message, None)]
        self._log_clear_pending = False
        self.output_box.show_error(message)
        self._update_note()

    def add_output(self, kind: str, content: dict) -> None:
        log = self._log
        if kind == "clear_output" and content.get("wait"):
            self._log_clear_pending = True
            self.output_box.add(kind, content)
            return
        if self._log_clear_pending:
            log.clear()
            self._log_clear_pending = False
        if kind == "stream":
            if log and log[-1][0] == "stream" and log[-1][1] == content["name"]:
                parts = log[-1][2]
                parts.append(content["text"])
                if len(parts) > 2000:  # bound memory for endless output
                    parts[:] = ["".join(parts)[-200_000:]]
            else:
                log.append(("stream", content["name"], [content["text"]]))
        elif kind == "clear_output":
            log.clear()
        else:
            log.append((kind, content, None))
        self.output_box.add(kind, content)
        if self._note is not None:
            self._update_note()

    def outputs_snapshot(self) -> list[tuple[str, object]]:
        out: list[tuple[str, object]] = []
        for kind, content, parts in self._log:
            if kind == "stream":
                out.append(("stream", {"name": content, "text": "".join(parts)}))
            else:
                out.append((kind, content))
        return out

    def replay(self, outputs: list[tuple[str, object]]) -> None:
        for kind, content in outputs:
            if kind == "_error":
                self.show_error(content)
            else:
                self.add_output(kind, content)

    def toggle_collapsed(self) -> None:
        self.set_class(not self.has_class("-collapsed"), "-collapsed")
        self._update_note()

    def _update_note(self) -> None:
        """'… N more lines' under a collapsed output."""
        text = self.output_text
        hidden = (text.rstrip("\n").count("\n") + 1 - 3) if text else 0
        if self.has_class("-collapsed") and hidden > 0:
            if self._note is None:
                self._note = Static(classes="collapsed-note")
                self.mount(self._note, after=self.output_box)
            self._note.update(f"… {hidden} more lines")
        elif self._note is not None:
            self._note.remove()
            self._note = None

    # ── header ───────────────────────────────────────────────────────────

    def _render_header(self) -> None:
        if self._header is None:
            return
        elapsed = int(time.perf_counter() - self._run_start) if self.status == "running" else 0
        key = (self.status, self.info, self.label, self.exec_count, self.duration, elapsed)
        if key == self._header_key:
            return
        self._header_key = key
        icon, style = STATUS_ICONS[self.status]
        palette = getattr(self.app, "palette", None)
        if palette is not None:  # colour the icon from the current theme
            theme = self.app.current_theme
            role = {"ok": "success", "stale": "warning", "error": "error", "skipped": "error"}.get(self.status, "primary")
            style = palette.comment if self.status == "idle" else (getattr(theme, role, None) or style)
        parts: list[tuple[str, str]] = [(f"{icon} ", style), (self.label, "bold")]
        if self.status == "running":
            parts.append((f"  running… {elapsed}s", "cyan"))
        else:
            if self.exec_count:
                parts.append((f" [{self.exec_count}]", "dim"))
            if self.duration is not None and self.duration >= 0.1:
                parts.append((f" {self.duration:.2f}s", "dim"))
        parts.append(("  " + self.info, "dim"))
        self._header.update(Text.assemble(*parts))

    # ── focus, keys, mouse ───────────────────────────────────────────────

    def _on_descendant_focus(self, event: events.DescendantFocus) -> None:
        self.add_class("-focused")

    def _on_descendant_blur(self, event: events.DescendantBlur) -> None:
        self.remove_class("-focused")

    def on_focus(self) -> None:
        self.add_class("-selected")
        self.app.update_status()

    def on_blur(self) -> None:
        self.remove_class("-selected")
        if not isinstance(self.app.focused, Cell):  # the next cell's on_focus updates it
            self.app.update_status()

    def on_key(self, event: events.Key) -> None:
        if self.screen.focused is not self:  # a key bubbling up from the editor
            return
        key = event.character if event.is_printable and event.character else event.key
        if _is_escape(event):
            key = "escape"
        if event.is_printable or key in _CELL_KEYS:  # printable keys never leak anywhere
            event.stop()
            event.prevent_default()
            # Run on the app's queue: a command may remove this cell, which would
            # cancel a handler still running on the cell's own queue.
            self.app.call_later(self.app.cell_command, self, key)

    @on(VimTextArea.LeaveEditor)
    def _leave_editor(self, event: VimTextArea.LeaveEditor) -> None:
        event.stop()
        self.focus(scroll_visible=False)

    def on_click(self, event) -> None:
        """Clicking the header selects the cell (cell mode); anywhere else focuses its editor."""
        if event.widget is self._header:
            self.focus(scroll_visible=False)
        elif not isinstance(event.widget, VimTextArea):
            self.editor.focus()

    @on(VimTextArea.Changed)
    def _mark_stale(self) -> None:
        if self.ran_source is None or self.status == "running":
            return
        document = self.editor.document
        same = document.line_count == self.ran_source.count("\n") + 1 and document.text == self.ran_source
        self.set_status("ok" if same else "stale")


class GPython(App):
    CSS_PATH = "app.tcss"
    TITLE = "gpython"
    # Every key here must be listed in keymap.KEYMAP (tests/test_commands.py checks).
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
        Binding("f1", "help", "Help"),
        Binding("f2", "toggle_vars", "Variables"),
        Binding("f3", "toggle_lazy", "Lazy"),
        Binding("f4", "run_stale", "Run stale"),
        Binding("f5", "run_all", "Run all"),
        Binding("ctrl+t", "change_theme", "Theme"),
    ]

    def __init__(self, path: Path | None = None, vim: bool | None = None, theme: str | None = None) -> None:
        super().__init__()
        settings = config.load()
        VimTextArea.mode = "normal"  # before compose, so every editor mounts in the same mode
        self._editor_theme_cache: dict[str, tuple] = {}
        self._theme_warning: str | None = None
        for custom in CUSTOM_APP_THEMES.values():
            self.register_theme(custom)
        self._loaded_theme = settings.get("theme")
        name = theme or self._loaded_theme or DEFAULT_THEME
        if name not in self.available_themes:
            self._theme_warning = f"Unknown theme {name!r}; using {DEFAULT_THEME}"
            name = DEFAULT_THEME
        self._loaded_theme = name  # only persist choices that differ from what we started with
        if self.theme == name:  # unchanged (e.g. "textual-dark"): Textual skips the watcher
            self.watch_theme(name)
        self.theme = name
        if vim is None:  # an explicit argument wins over the config file
            vim = settings.get("vim", True) is not False
        VimTextArea.enabled = vim
        VimTextArea.line_numbers = settings.get("line_numbers", True) is not False
        self.path = path
        self.markers = True
        self.lazy = False
        self.dirty = False
        self.exec_counter = 0  # Jupyter's In [n]
        self.kernel_state = "starting kernel…"
        self.kernel = Kernel(file=path)
        self._run_lock = asyncio.Lock()
        self.loaded = asyncio.Event()  # set once every cell of the file is mounted
        self._pending_sources: list[str] = []  # cells not mounted yet (progressive mount)
        self._cells_box: VerticalScroll | None = None
        self._status: Static | None = None
        self._mode: Static | None = None
        self._status_key: tuple | None = None
        self._deleted: list[CellSnapshot] = []  # undo stack for cell deletion
        self._cell_clipboard: CellSnapshot | None = None
        self._cmd_pending = ""  # cell mode: first key of gg / dd / yy
        self._cmd_count = ""
        self._prompt_mode = "normal"  # ui mode when a ':' or '/' prompt was opened

    def on_load(self) -> None:
        # Boot the kernel while the widgets are being built.
        self.run_worker(self._start_kernel(), group="kernel")

    def compose(self) -> ComposeResult:
        yield Header()
        sources = [""]
        if self.path is not None:
            specs, self.markers = notebook.load_cells(self.path)
            sources = [spec.source for spec in specs] or [""]
        sources, self._pending_sources = sources[:INITIAL_CELLS], sources[INITIAL_CELLS:]
        with Horizontal():
            with VerticalScroll(id="cells") as self._cells_box:
                for n, source in enumerate(sources, 1):
                    cell = Cell(source)
                    cell.set_label(n)
                    yield cell
            table = DataTable(id="vars", cursor_type="row", zebra_stripes=True)
            table.add_columns("name", "type", "value")
            table.display = False  # f2 / :vars shows it
            yield table
        self._status = Static(id="status")
        yield self._status  # no Footer: it rebuilt itself on every focus change (half the cost of a cell move)

    async def on_mount(self) -> None:
        VimTextArea.mode = "normal"
        header = self.query_one(Header)
        self._mode = Static(id="mode")
        await header.mount(self._mode, after=header.query_one(HeaderIcon))
        self.update_status()
        if self._theme_warning:
            self.notify(self._theme_warning, severity="warning")
        self.cells()[0].editor.focus()
        self.call_after_refresh(lambda: self.run_worker(self._mount_rest(), group="mount"))

    async def _mount_rest(self) -> None:
        """Mount the cells left out of compose(), a batch at a time, after the first frame."""
        while self._pending_sources:
            batch = [Cell(source) for source in self._pending_sources[:MOUNT_BATCH]]
            del self._pending_sources[:MOUNT_BATCH]
            first = len(self._cells_box.children) + 1
            for n, cell in enumerate(batch, first):
                cell.set_label(n)
            await self._cells_box.mount_all(batch)
            await asyncio.sleep(0)  # let keys and repaints through between batches
        self.relabel()
        self.refresh_info()
        self.loaded.set()

    async def _start_kernel(self) -> None:
        await self.kernel.start()
        self.update_status("kernel ready")

    async def on_unmount(self) -> None:
        await self.kernel.shutdown()

    # ── helpers ──────────────────────────────────────────────────────────

    @property
    def ui_mode(self) -> str:
        """"cell" when a cell itself is selected, else the vim mode ("insert" without vim)."""
        if isinstance(self.focused, Cell):
            return "cell"
        if not VimTextArea.enabled or VimTextArea.mode == "insert":
            return "insert"
        return "normal"

    def watch_theme(self, name: str) -> None:
        """Point every editor and cell header at the new theme; remember the choice."""
        self.palette = palette_for(self.current_theme)
        pair = self._editor_theme_cache.get(self.palette.name)
        if pair is None:
            pair = self._editor_theme_cache[self.palette.name] = editor_themes(self.palette)
        self.editor_theme_pair = pair
        if self.is_running:
            for editor in self.query(VimTextArea):
                editor.set_editor_theme(*pair)
            for cell in self.query(Cell):
                cell._header_key = None
                cell._render_header()
            self.update_status()
        if name != self._loaded_theme:
            config.save({"theme": name})
            self._loaded_theme = name

    def update_status(self, kernel_state: str | None = None) -> None:
        """The mode chip in the header and the status line: mode, file, run mode, kernel."""
        if kernel_state is not None:
            self.kernel_state = kernel_state
        if self._status is None:  # not composed yet (the kernel boots during compose)
            return
        mode = self.ui_mode
        name = (str(self.path) if self.path else "untitled") + (" •" if self.dirty else "")
        key = (mode, VimTextArea.enabled, name, self.lazy, self.kernel_state)
        if key == self._status_key:
            return
        self._status_key = key
        show_mode = VimTextArea.enabled or mode == "cell"
        self.sub_title = name
        if self._mode is not None:
            self._mode.display = show_mode
            self._mode.update(Text(mode.upper(), style=_MODE_STYLES[mode]))
        # The vim mode lives in the header chip and the file name in the subtitle,
        # so the bottom bar only carries run mode, kernel state and key hints.
        self._status.update(
            Text.assemble(
                f" {'lazy' if self.lazy else 'reactive'}  ·  {self.kernel_state}",
                (f"    {KEY_HINTS}", "dim"),
            )
        )

    def _set_dirty(self, dirty: bool = True) -> None:
        if dirty != self.dirty:
            self.dirty = dirty
            self.update_status()

    def cells(self) -> list[Cell]:
        """Cells in document order. They are the direct children of #cells, so no DOM search."""
        box = self._cells_box
        if box is None:
            return list(self.query(Cell))
        return [node for node in box.children if isinstance(node, Cell)]

    def current_cell(self) -> Cell | None:
        node = self.focused
        while node is not None and not isinstance(node, Cell):
            node = node.parent
        return node

    def relabel(self) -> None:
        """Headers show display positions; renumber after any insert, delete or move."""
        for n, cell in enumerate(self.cells(), 1):
            cell.set_label(n)

    def graph(self) -> Graph:
        return build({c.cid: c.source for c in self.cells()})

    def refresh_info(self) -> Graph:
        graph = self.graph()
        for cell in self.cells():
            cell.set_info(graph)
        return graph

    async def run_cells(self, roots: list[str], downstream: bool | None = None) -> None:
        """Run roots (and, unless lazy, every cell downstream) in dependency order."""
        await self.loaded.wait()  # the graph needs every cell
        async with self._run_lock:
            graph = self.refresh_info()
            by_id = {c.cid: c for c in self.cells()}
            roots = graph.expand_roots([r for r in roots if r in by_id])
            if downstream is None:
                downstream = not self.lazy
            if downstream:
                order = graph.run_order(roots)
            else:
                order = graph.sort(set(roots))
                for cid in graph.descendants(roots) - set(roots):
                    by_id[cid].set_status("stale")
            for cid in order:
                by_id[cid].set_status("queued")

            failed: set[str] = set()
            for cid in order:
                cell = by_id[cid]
                if not cell.is_mounted:  # deleted while queued
                    continue
                if cid in graph.errors:
                    cell.show_error(graph.errors[cid])
                    cell.set_status("error")
                    failed.add(cid)
                    continue
                if graph.parents[cid] & failed:
                    cell.show_error("not run: an upstream cell failed")
                    cell.set_status("skipped")
                    failed.add(cid)
                    continue

                source = cell.source
                await self.kernel.delete(cell.last_defs | graph.defs[cid])
                # Deferred clear: the old output stays until the first new output arrives,
                # so the cell does not collapse and grow again (the page would jump).
                cell.add_output("clear_output", {"wait": True})
                self.exec_counter += 1
                cell.begin_run(self.exec_counter)
                ok = await self.kernel.execute(source, cell.add_output)
                if cell.output_box._clear_pending:  # no output at all: clear the old one now
                    cell.clear_output()
                cell.ran_source = source
                cell.last_defs = graph.defs[cid]
                cell.end_run("ok" if ok and cell.source == source else "error" if not ok else "stale")
                if not ok:
                    failed.add(cid)

            await self.refresh_vars(graph)

    async def refresh_vars(self, graph: Graph) -> None:
        names = set().union(*graph.defs.values()) if graph.defs else set()
        rows = await self.kernel.variables(names)
        table = self.query_one("#vars", DataTable)
        table.clear()
        for row in rows:
            table.add_row(*row)

    def write_file(self) -> None:
        sources = [c.source for c in self.cells()] + self._pending_sources
        notebook.save(self.path, sources, self.markers)
        self._set_dirty(False)
        self.notify(f"Saved {self.path}")

    def _after_loaded(self, callback) -> None:
        """Call `callback` now if every cell is mounted, else once they are."""
        if self.loaded.is_set():
            callback()
            return

        async def later() -> None:
            await self.loaded.wait()
            callback()

        self.run_worker(later(), group="nav")

    def select(self, cell: Cell, mode: str, row: int | None = None) -> None:
        """Focus `cell` in `mode`: "cell" selects it, otherwise its editor gets focus.

        `mode` "normal"/"insert" also switches the vim mode; "keep" leaves it.
        `row` puts the cursor at the start of that line (-1 = last line).
        """
        if mode == "cell":
            cell.focus(scroll_visible=False)
        else:
            editor = cell.editor
            editor.focus(scroll_visible=False)  # Textual's own scroll is animated
            if mode in ("normal", "insert") and VimTextArea.mode != mode:
                editor.set_mode(mode)
            if row is not None:
                editor.move_cursor((editor.document.line_count - 1 if row < 0 else row, 0))
        cell.scroll_visible(animate=False, immediate=True)
        # A cell mounted in this same frame has no region yet: scroll again after layout.
        self.call_after_refresh(cell.scroll_visible, animate=False, immediate=True)

    def goto_cell(self, n: int, mode: str, row: int = 0, clamp: bool = True) -> bool:
        """Focus the n-th cell by display position (1-based), keeping `mode`."""
        cells = self.cells()
        if clamp:
            n = max(1, min(len(cells), n))
        if not 1 <= n <= len(cells):
            self.notify(f"No cell {n} (there are {len(cells)})", severity="warning")
            return False
        self.select(cells[n - 1], mode if mode == "cell" else "keep", row)
        return True

    def _snapshot(self, cell: Cell, index: int) -> CellSnapshot:
        return CellSnapshot(index, cell.source, cell.kind, cell.ran_source is not None, cell.outputs_snapshot())

    async def _insert(self, anchor: Cell | None, above: bool = False, snapshot: CellSnapshot | None = None) -> Cell:
        """Mount a new (or restored) cell next to `anchor`; renumber; mark dirty."""
        cell = Cell(snapshot.source, snapshot.kind) if snapshot else Cell()
        box = self._cells_box
        if anchor is None:
            await box.mount(cell)
        elif above:
            await box.mount(cell, before=anchor)
        else:
            await box.mount(cell, after=anchor)
        if snapshot:
            cell.replay(snapshot.outputs)
            if snapshot.ran:
                cell.set_status("stale")
        self.relabel()
        self.refresh_info()
        self._set_dirty()
        return cell

    # ── actions ──────────────────────────────────────────────────────────

    async def action_run_cell(self, advance: str = "stay") -> None:
        """Run the current cell. advance: "stay", "next" (Jupyter shift+enter) or "insert" (alt+enter)."""
        cell = self.current_cell()
        if cell is None:
            return
        mode = self.ui_mode
        self.run_worker(self.run_cells([cell.cid]), group="run")
        if advance == "next":
            cells = self.cells()
            if cells[-1] is cell and self._pending_sources:
                await self.loaded.wait()
                cells = self.cells()
            index = cells.index(cell) if cell in cells else len(cells) - 1
            if index + 1 < len(cells):
                self.select(cells[index + 1], "cell" if mode == "cell" else "normal", row=0)
                return
            advance = "insert"  # last cell: open a fresh one
        if advance == "insert":
            new = await self._insert(cell)
            self.select(new, "insert")

    async def action_run_and_add(self) -> None:
        """Old name of shift+enter."""
        await self.action_run_cell("next")

    def action_run_all(self) -> None:
        self.run_worker(self.run_cells([c.cid for c in self.cells()], downstream=True), group="run")

    def action_run_stale(self) -> None:
        stale = [c.cid for c in self.cells() if c.status in ("stale", "idle") and c.source.strip()]
        if stale:
            self.run_worker(self.run_cells(stale, downstream=True), group="run")

    def _run_relative(self, below: bool) -> None:
        cell = self.current_cell()
        cells = self.cells()
        if cell is None or cell not in cells:
            return
        index = cells.index(cell)
        subset = cells[index:] if below else cells[:index]
        if subset:
            self.run_worker(self.run_cells([c.cid for c in subset], downstream=False), group="run")

    async def action_add_cell(self, above: bool = False) -> None:
        cell = await self._insert(self.current_cell(), above)
        self.select(cell, "insert")

    async def action_delete_cell(self, count: int = 1) -> None:
        """Delete `count` cells from the current one (dependents re-run); z in cell mode undoes."""
        cell = self.current_cell()
        if cell is None:
            return
        mode = self.ui_mode
        cells = self.cells()
        index = cells.index(cell)
        victims = cells[index : index + max(1, count)]
        ids = [c.cid for c in victims]
        graph = self.graph()
        downstream = graph.descendants(ids) - set(ids)
        defs = set().union(*(c.last_defs for c in victims))
        for offset in range(len(victims) - 1, -1, -1):  # z restores the first one first
            self._deleted.append(self._snapshot(victims[offset], index + offset))
        del self._deleted[:-MAX_UNDO]
        await self._cells_box.remove_children(victims)
        if not self.cells():
            await self._cells_box.mount(Cell())
        remaining = self.cells()
        self.relabel()
        self._set_dirty()
        self.select(remaining[min(index, len(remaining) - 1)], "cell" if mode == "cell" else "keep")

        # Reactivity: the deleted cells' variables vanish, so dependents re-run.
        await self.kernel.delete(defs)
        if downstream:
            self.run_worker(self.run_cells(sorted(downstream)), group="run")
        else:
            self.refresh_info()
            self.run_worker(self.refresh_vars(self.graph()), group="run")

    async def action_undo_delete(self) -> None:
        if not self._deleted:
            self.notify("Nothing to undo")
            return
        mode = self.ui_mode
        snapshot = self._deleted.pop()
        cells = self.cells()
        if snapshot.index < len(cells):
            cell = await self._insert(cells[snapshot.index], above=True, snapshot=snapshot)
        else:
            cell = await self._insert(cells[-1] if cells else None, snapshot=snapshot)
        self.select(cell, "cell" if mode == "cell" else "keep")

    def action_copy_cell(self) -> None:
        if cell := self.current_cell():
            self._cell_clipboard = self._snapshot(cell, self.cells().index(cell))

    async def action_cut_cell(self) -> None:
        self.action_copy_cell()
        await self.action_delete_cell()

    async def action_paste_cell(self, above: bool = False) -> None:
        if self._cell_clipboard is None:
            self.notify("No cell copied (yy or x in cell mode)")
            return
        mode = self.ui_mode
        cell = await self._insert(self.current_cell(), above, snapshot=self._cell_clipboard)
        self.select(cell, "cell" if mode == "cell" else "keep")

    def action_move_cell(self, step: int) -> None:
        cell = self.current_cell()
        cells = self.cells()
        if cell is None or cell not in cells:
            return
        target = cells.index(cell) + step
        if not 0 <= target < len(cells):
            return
        if step > 0:
            self._cells_box.move_child(cell, after=cells[target])
        else:
            self._cells_box.move_child(cell, before=cells[target])
        self.relabel()
        self.refresh_info()
        self._set_dirty()
        cell.scroll_visible(animate=False)

    def action_focus_cell(self, step: int) -> None:
        cells = self.cells()
        current = self.current_cell()
        index = cells.index(current) + step if current in cells else 0
        if abs(step) > 1:  # J/K with a count stop at the first/last cell
            index = max(0, min(len(cells) - 1, index))
        if 0 <= index < len(cells) and cells[index] is not current:
            if isinstance(self.focused, Cell):
                self.select(cells[index], "cell")
                return
            editor = cells[index].editor
            editor.focus(scroll_visible=False)  # Textual's own scroll is animated
            # Land on the nearest line, like moving through one long buffer.
            editor.move_cursor((0, 0) if step > 0 else (editor.document.line_count - 1, 0))
            cells[index].scroll_visible(animate=False, immediate=True)

    def action_focus_edge(self, last: bool) -> None:
        mode = self.ui_mode
        if last:
            self._after_loaded(lambda: self.goto_cell(len(self.cells()), mode, row=-1))
        else:
            self.goto_cell(1, mode)

    def set_cell_kind(self, cell: Cell, kind: str) -> None:
        """Switch a cell between code and markdown (plan 04 implements markdown)."""
        if kind == cell.kind:
            return
        self.notify("markdown cells arrive in plan 04")

    def set_line_numbers(self, on: bool) -> None:
        VimTextArea.line_numbers = on
        for cell in self.cells():
            cell.editor.show_line_numbers = on
        config.save({"line_numbers": on})

    def set_vim(self, on: bool) -> None:
        VimTextArea.enabled = on
        VimTextArea.mode = "normal" if on else "insert"
        for cell in self.cells():
            cell.editor.pending = cell.editor.count = ""
            cell.editor._apply_mode()
        config.save({"vim": on})
        self.update_status()

    def action_save(self) -> None:
        if self.path is None:
            self.push_screen(Prompt("Save as: ", placeholder="notebook.py"), self._saved_as)
        else:
            self.write_file()

    async def _saved_as(self, name: str | None) -> None:
        if not name:
            return
        self.path = Path(name)
        self.write_file()
        self.update_status()
        if self.kernel.ready.is_set():
            await self.kernel.set_file(self.path)

    async def action_quit(self) -> None:
        """ctrl+q / :q: ask first when there are unsaved changes."""
        if self.dirty:
            self.push_screen(Prompt("Unsaved changes. Quit without saving? (y/n) "), self._confirm_quit)
        else:
            self.exit()

    def _confirm_quit(self, answer: str | None) -> None:
        if answer and answer.lower().startswith("y"):
            self.exit()

    async def action_interrupt(self) -> None:
        await self.kernel.interrupt()

    def action_help(self) -> None:
        if not isinstance(self.screen, HelpScreen):
            commands = [(names[0], desc) for names, desc, _ in self.ex_commands()]
            self.push_screen(HelpScreen(commands))

    def action_toggle_vars(self) -> None:
        table = self.query_one("#vars")
        table.display = not table.display

    def action_toggle_lazy(self) -> None:
        self.lazy = not self.lazy
        self.update_status()
        self.notify("Lazy: dependents are marked stale, run them with f4" if self.lazy else "Reactive: dependents re-run automatically")

    async def action_restart_kernel(self) -> None:
        self.update_status("restarting kernel…")
        await self.kernel.restart()
        for cell in self.cells():
            cell.last_defs = set()
            if cell.ran_source is not None:
                cell.set_status("stale")
        self.query_one("#vars", DataTable).clear()
        self.update_status("kernel ready")

    def on_click(self, event) -> None:
        """Clicking the empty space below the last cell focuses the nearest cell."""
        if event.widget is self._cells_box and (cells := self.cells()):
            cells[-1].editor.focus()

    # ── cell mode ────────────────────────────────────────────────────────

    async def cell_command(self, cell: Cell, key: str) -> bool:
        """One key in cell mode (the cell itself has focus). Returns False if unknown."""
        if key.isdigit() and (key != "0" or self._cmd_count):
            self._cmd_count += key
            return True
        had_count = bool(self._cmd_count)
        n = int(self._cmd_count or 1)
        pending, self._cmd_pending, self._cmd_count = self._cmd_pending, "", ""
        if pending == "g":
            if key == "g":
                self.goto_cell(n, "cell")
            return True
        if pending == "d":
            if key == "d":
                await self.action_delete_cell(n)
            return True
        if pending == "y":
            if key == "y":
                self.action_copy_cell()
                return True
            # `y` alone already made the cell code; this key is a new command.
        match key:
            case "g" | "d" | "y":
                if key == "y":
                    self.set_cell_kind(cell, "code")
                self._cmd_pending = key
                self._cmd_count = str(n) if had_count else ""
            case "j" | "down":
                self.action_focus_cell(n)
            case "k" | "up":
                self.action_focus_cell(-n)
            case "G":
                if had_count:
                    self.goto_cell(n, "cell")
                else:
                    self._after_loaded(lambda: self.goto_cell(len(self.cells()), "cell"))
            case "enter":
                self.select(cell, "normal")
            case "i":
                self.select(cell, "insert")
            case "a" | "b":
                new = await self._insert(cell, above=key == "a")
                self.select(new, "insert")
            case "z":
                await self.action_undo_delete()
            case "x":
                await self.action_cut_cell()
            case "p" | "P":
                await self.action_paste_cell(above=key == "P")
            case "J" | "shift+j":
                self.action_move_cell(1)
            case "K" | "shift+k":
                self.action_move_cell(-1)
            case "m":
                self.set_cell_kind(cell, "markdown")
            case "o":
                cell.toggle_collapsed()
            case "O" | "shift+o":
                cell.clear_output()
            case "l":
                self.set_line_numbers(not VimTextArea.line_numbers)
            case "/":
                self._open_search()
            case ":":
                self._open_ex()
            case "?" | "question_mark":
                self.action_help()
            case "escape":
                pass
            case _:
                return False
        return True

    # ── vim glue ─────────────────────────────────────────────────────────

    @on(VimTextArea.ModeChanged)
    def _mode_changed(self) -> None:
        self.update_status()

    @on(VimTextArea.Changed)
    def _edited(self) -> None:
        if not self.dirty:
            self._set_dirty()

    @on(VimTextArea.EdgeReached)
    def _edge(self, event: VimTextArea.EdgeReached) -> None:
        self.action_focus_cell(event.step)

    @on(VimTextArea.DeleteCell)
    async def _delete_cell(self, event: VimTextArea.DeleteCell) -> None:
        await self.action_delete_cell(event.count)

    @on(VimTextArea.SearchRequested)
    def _search(self) -> None:
        self._open_search()

    @on(VimTextArea.ExCommand)
    def _ex(self) -> None:
        self._open_ex()

    def _open_ex(self) -> None:
        self._prompt_mode = self.ui_mode
        self.push_screen(Prompt(":"), self._run_ex)

    def _open_search(self) -> None:
        self._prompt_mode = self.ui_mode
        self.push_screen(Prompt("/", placeholder="cell number or text"), self._jump)

    def _jump(self, target: str | None) -> None:
        """'/3' focuses the third cell by display position; '/text' the next cell containing text."""
        if not target:
            return
        mode = self._prompt_mode

        def go() -> None:
            if target.isdigit():
                self.goto_cell(int(target), mode, clamp=False)
                return
            cells = self.cells()
            current = self.current_cell()
            start = cells.index(current) + 1 if current in cells else 0
            needle = target.lower()
            for i in range(len(cells)):
                cell = cells[(start + i) % len(cells)]
                if needle in cell.source.lower():
                    self.select(cell, mode if mode == "cell" else "keep")
                    return
            self.notify(f"Not found: {target}", severity="warning")

        self._after_loaded(go)

    def ex_commands(self) -> list[tuple[tuple[str, ...], str, object]]:
        """(names, description, handler). Shared by ':' commands, the palette and help.

        Handlers take the rest of the command line as one argument and may be async.
        `:N` (a number) jumps to the N-th cell, like `/N`.
        """
        return [
            (("w", "write"), "Save notebook (:w name.py saves as)", self._ex_write),
            (("wq", "x", "wqa", "wqall", "xa", "xall"), "Save and quit", self._ex_write_quit),
            (("q", "qa", "qall", "quit"), "Quit (asks if unsaved)", lambda arg="": self.action_quit()),
            (("q!", "qa!", "quit!"), "Quit without saving", lambda arg="": self.exit()),
            (("run", "runall"), "Run all cells", lambda arg="": self.action_run_all()),
            (("runabove",), "Run the cells above the current one", lambda arg="": self._run_relative(False)),
            (("runbelow",), "Run the current cell and the cells below", lambda arg="": self._run_relative(True)),
            (("stale", "runstale"), "Run stale cells", lambda arg="": self.action_run_stale()),
            (("restart",), "Restart kernel", lambda arg="": self.action_restart_kernel()),
            (("clear",), "Clear all outputs", lambda arg="": [c.clear_output() for c in self.cells()]),
            (("set",), "Options: vim novim lazy nolazy nu nonu", self._ex_set),
            (("variables", "vars"), "Toggle variables panel", lambda arg="": self.action_toggle_vars()),
            (("lazy",), "Toggle lazy mode", lambda arg="": self.action_toggle_lazy()),
            (("new",), "New cell below", lambda arg="": self.action_add_cell()),
            (("delete", "del"), "Delete current cell", lambda arg="": self.action_delete_cell()),
            (("md", "markdown"), "Make the cell markdown", lambda arg="": self._ex_kind("markdown")),
            (("code",), "Make the cell code", lambda arg="": self._ex_kind("code")),
            (("interrupt",), "Interrupt running code", lambda arg="": self.kernel.interrupt()),
            (("theme",), "Set theme (:theme name) or show the current one", self._ex_theme),
            (("help", "h"), "Keys and commands", lambda arg="": self.action_help()),
        ]

    def _ex_kind(self, kind: str) -> None:
        if cell := self.current_cell():
            self.set_cell_kind(cell, kind)

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
            await self._saved_as(arg)
        else:
            self.action_save()

    async def _ex_write_quit(self, arg: str = "") -> None:
        if self.path is None and not arg:
            self.notify("No file name. Use :wq name.py", severity="warning")
            return
        await self._saved_as(arg or str(self.path))
        self.exit()

    def _ex_theme(self, arg: str = "") -> None:
        if not arg:
            self.notify(f"Theme: {self.theme}")
        elif arg in self.available_themes:
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
            self._after_loaded(lambda: self.goto_cell(int(name), mode, clamp=False))
            return
        for names, _, handler in self.ex_commands():
            if name in names:
                result = handler(arg.strip())
                if asyncio.iscoroutine(result):
                    await result
                return
        self.notify(f"Unknown command: {command}  (try :help)", severity="error")

    def get_system_commands(self, screen):
        yield from super().get_system_commands(screen)
        for names, desc, handler in self.ex_commands():
            if names[0] in ("q", "q!", "help", "set"):
                continue
            yield SystemCommand(f"{desc}  (:{names[0]})", f"Also available as :{names[0]}", self._palette(handler))

    def _palette(self, handler):
        def callback() -> None:
            result = handler("")
            if asyncio.iscoroutine(result):
                self.run_worker(result)

        return callback
