"""The Textual front end: a column of cells plus a variables panel."""

from __future__ import annotations

import asyncio
import base64
import io
import itertools
from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, Label, Static
from textual_image.widget import Image  # must be imported before the app starts

from . import notebook
from .graph import Graph, build
from .kernel import Kernel
from .vim import VimTextArea

STATUS_ICONS = {
    "idle": ("○", "dim"),
    "stale": ("◌", "yellow"),
    "queued": ("…", "cyan"),
    "running": ("●", "cyan"),
    "ok": ("✓", "green"),
    "error": ("✗", "red"),
    "skipped": ("⊘", "red"),
}
MAX_STREAM_CHARS = 50_000

_ids = itertools.count(1)


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
    """One code cell: a header line, an editor and an output area."""

    def __init__(self, source: str = "") -> None:
        self.cid = f"c{next(_ids)}"
        super().__init__(id=self.cid)
        self.initial_source = source
        self.ran_source: str | None = None
        self.last_defs: set[str] = set()
        self.status = "idle"
        self.info = ""
        self._last_text: tuple[str, Static, str] | None = None  # (kind, widget, text)

    def compose(self) -> ComposeResult:
        yield Static(classes="cell-header")
        yield VimTextArea(self.initial_source)
        yield Vertical(classes="cell-output")

    def on_mount(self) -> None:
        self._render_header()

    @property
    def editor(self) -> VimTextArea:
        return self.query_one(VimTextArea)

    @property
    def output_box(self) -> Vertical:
        return self.query_one(".cell-output", Vertical)

    @property
    def source(self) -> str:
        editors = self.query(VimTextArea)  # may not be composed yet right after mounting
        return editors.first().text if editors else self.initial_source

    @property
    def output_text(self) -> str:
        return "".join(w.gp_text for w in self.output_box.query(Static) if hasattr(w, "gp_text"))

    def set_status(self, status: str) -> None:
        self.status = status
        for name in STATUS_ICONS:
            self.set_class(name == status, f"-{name}")
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

    def clear_output(self) -> None:
        self._last_text = None
        box = self.output_box
        box.remove_children()
        box.display = False

    def show_error(self, message: str) -> None:
        self.clear_output()
        self._add_text("error", message)

    def add_output(self, kind: str, content: dict) -> None:
        if kind == "clear_output":
            self.clear_output()
        elif kind == "stream":
            self._add_text(content["name"], content["text"])
        elif kind in ("execute_result", "display_data"):
            data = content.get("data", {})
            if "image/png" in data:
                self._add_image(data["image/png"])
            elif "text/plain" in data:
                self._add_text("result", data["text/plain"])
            else:
                self._add_text("note", f"[{', '.join(data)} output not supported yet]")
        elif kind == "error":
            self._add_text("traceback", "\n".join(content["traceback"]))

    def _add_text(self, kind: str, text: str) -> None:
        box = self.output_box
        box.display = True
        if kind in ("stdout", "stderr") and self._last_text and self._last_text[0] == kind:
            _, widget, old = self._last_text
            text = (old + text)[-MAX_STREAM_CHARS:]
        else:
            widget = Static()
            box.mount(widget)
        widget.gp_text = text
        self._last_text = (kind, widget, text) if kind in ("stdout", "stderr") else None
        widget.update(_render(kind, text))

    def _add_image(self, b64: str) -> None:
        from PIL import Image as PILImage

        self._last_text = None
        box = self.output_box
        box.display = True
        box.mount(Image(PILImage.open(io.BytesIO(base64.b64decode(b64)))))

    def _render_header(self) -> None:
        if not self.is_mounted:
            return
        icon, style = STATUS_ICONS[self.status]
        header = Text.assemble((f"{icon} ", style), (self.cid, "bold"), ("  " + self.info, "dim"))
        self.query_one(".cell-header", Static).update(header)

    @on(VimTextArea.Changed)
    def _mark_stale(self) -> None:
        if self.ran_source is not None and self.status != "running":
            self.set_status("stale" if self.source != self.ran_source else "ok")


def _render(kind: str, text: str) -> Text:
    if kind == "error":
        return Text(text, style="bold red")
    if kind == "note":
        return Text(text, style="dim italic")
    return Text.from_ansi(text, style="red" if kind == "stderr" else "")


class GPython(App):
    CSS_PATH = "app.tcss"
    TITLE = "gpython"
    BINDINGS = [
        Binding("ctrl+enter,ctrl+e", "run_cell", "Run", priority=True),
        Binding("shift+enter", "run_and_add", "Run + new", priority=True),
        Binding("ctrl+n", "add_cell", "New cell", priority=True),
        Binding("ctrl+d", "delete_cell", "Delete cell", priority=True),
        Binding("alt+up,ctrl+up", "focus_cell(-1)", "Prev cell", show=False, priority=True),
        Binding("alt+down,ctrl+down", "focus_cell(1)", "Next cell", show=False, priority=True),
        Binding("ctrl+s", "save", "Save", priority=True),
        Binding("ctrl+k", "interrupt", "Interrupt", priority=True),
        Binding("f2", "toggle_vars", "Variables"),
        Binding("f3", "toggle_lazy", "Lazy"),
        Binding("f4", "run_stale", "Run stale"),
    ]

    def __init__(self, path: Path | None = None, vim: bool = True) -> None:
        super().__init__()
        VimTextArea.enabled = vim
        self.path = path
        self.markers = True
        self.lazy = False
        self.kernel_state = "starting kernel…"
        self.kernel = Kernel(file=path)
        self._run_lock = asyncio.Lock()

    def compose(self) -> ComposeResult:
        yield Header()
        sources = [""]
        if self.path is not None:
            sources, self.markers = notebook.load(self.path)
        with Horizontal():
            with VerticalScroll(id="cells"):
                for source in sources:
                    yield Cell(source)
            table = DataTable(id="vars", cursor_type="row", zebra_stripes=True)
            table.add_columns("name", "type", "value")
            yield table
        yield Static(id="status")
        yield Footer()

    async def on_mount(self) -> None:
        VimTextArea.mode = "normal"
        self.update_status()
        self.cells()[0].editor.focus()
        self.refresh_info()
        self.run_worker(self._start_kernel(), group="kernel")

    async def _start_kernel(self) -> None:
        await self.kernel.start()
        self.update_status("kernel ready")

    async def on_unmount(self) -> None:
        await self.kernel.shutdown()

    # ── helpers ──────────────────────────────────────────────────────────

    def update_status(self, kernel_state: str | None = None) -> None:
        """One status line for the whole app: vim mode, file, run mode, kernel."""
        if kernel_state is not None:
            self.kernel_state = kernel_state
        self.sub_title = str(self.path) if self.path else "untitled"
        if not VimTextArea.enabled:
            mode = ("", "")
        elif VimTextArea.mode == "insert":
            mode = (" INSERT ", "bold black on green")
        else:
            mode = (" NORMAL ", "bold black on yellow")
        self.query_one("#status", Static).update(
            Text.assemble(mode, f"  {self.sub_title}  ·  {'lazy' if self.lazy else 'reactive'}  ·  {self.kernel_state}")
        )

    def cells(self) -> list[Cell]:
        return list(self.query(Cell))

    def current_cell(self) -> Cell | None:
        node = self.focused
        while node is not None and not isinstance(node, Cell):
            node = node.parent
        return node

    def graph(self) -> Graph:
        return build({c.cid: c.source for c in self.cells()})

    def refresh_info(self) -> Graph:
        graph = self.graph()
        for cell in self.cells():
            cell.set_info(graph)
        return graph

    async def run_cells(self, roots: list[str], downstream: bool | None = None) -> None:
        """Run roots (and, unless lazy, every cell downstream) in dependency order."""
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
                cell.clear_output()
                cell.set_status("running")
                ok = await self.kernel.execute(source, cell.add_output)
                cell.ran_source = source
                cell.last_defs = graph.defs[cid]
                cell.set_status("ok" if ok and cell.source == source else "error" if not ok else "stale")
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
        notebook.save(self.path, [c.source for c in self.cells()], self.markers)
        self.notify(f"Saved {self.path}")

    # ── actions ──────────────────────────────────────────────────────────

    def action_run_cell(self) -> None:
        if cell := self.current_cell():
            self.run_worker(self.run_cells([cell.cid]), group="run")

    def action_run_all(self) -> None:
        self.run_worker(self.run_cells([c.cid for c in self.cells()], downstream=True), group="run")

    def action_run_stale(self) -> None:
        stale = [c.cid for c in self.cells() if c.status in ("stale", "idle") and c.source.strip()]
        if stale:
            self.run_worker(self.run_cells(stale, downstream=True), group="run")

    async def action_add_cell(self) -> None:
        cell = Cell()
        current = self.current_cell()
        container = self.query_one("#cells", VerticalScroll)
        if current:
            await container.mount(cell, after=current)
        else:
            await container.mount(cell)
        cell.editor.focus()
        cell.editor.set_mode("insert")
        cell.scroll_visible()

    async def action_run_and_add(self) -> None:
        """shift+enter: run this cell, then start typing in a fresh cell below."""
        cell = self.current_cell()
        if cell is None:
            return
        await self.action_add_cell()
        self.run_worker(self.run_cells([cell.cid]), group="run")

    async def action_delete_cell(self) -> None:
        cell = self.current_cell()
        if cell is None:
            return
        cells = self.cells()
        graph = self.graph()
        downstream = graph.descendants([cell.cid]) - {cell.cid}
        index = cells.index(cell)
        defs = cell.last_defs
        await cell.remove()
        if not self.cells():
            await self.query_one("#cells").mount(Cell())
        remaining = self.cells()
        remaining[min(index, len(remaining) - 1)].editor.focus()

        # Reactivity: the deleted cell's variables vanish, so dependents re-run.
        await self.kernel.delete(defs)
        if downstream:
            self.run_worker(self.run_cells(sorted(downstream)), group="run")
        else:
            self.refresh_info()
            self.run_worker(self.refresh_vars(self.graph()), group="run")

    def action_focus_cell(self, step: int) -> None:
        cells = self.cells()
        current = self.current_cell()
        index = cells.index(current) + step if current in cells else 0
        if 0 <= index < len(cells):
            editor = cells[index].editor
            editor.focus()
            # Land on the nearest line, like moving through one long buffer.
            editor.move_cursor((0, 0) if step > 0 else (editor.document.line_count - 1, 0))
            cells[index].scroll_visible()

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

    async def action_interrupt(self) -> None:
        await self.kernel.interrupt()

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

    # ── vim glue ─────────────────────────────────────────────────────────

    @on(VimTextArea.ModeChanged)
    def _mode_changed(self) -> None:
        self.update_status()

    @on(VimTextArea.EdgeReached)
    def _edge(self, event: VimTextArea.EdgeReached) -> None:
        self.action_focus_cell(event.step)

    @on(VimTextArea.ExCommand)
    def _ex(self) -> None:
        self.push_screen(Prompt(":"), self._run_ex)

    def ex_commands(self) -> list[tuple[tuple[str, ...], str, object]]:
        """(names, description, handler). Shared by ':' commands and the palette.

        Handlers take the rest of the command line as one argument and may be async.
        """
        return [
            (("w", "write"), "Save notebook (:w name.py saves as)", self._ex_write),
            (("wq", "x", "wqa", "wqall", "xa", "xall"), "Save and quit", self._ex_write_quit),
            (("q", "q!", "qa", "qall", "quit"), "Quit", lambda arg="": self.exit()),
            (("run", "runall"), "Run all cells", lambda arg="": self.action_run_all()),
            (("stale", "runstale"), "Run stale cells", lambda arg="": self.action_run_stale()),
            (("restart",), "Restart kernel", lambda arg="": self.action_restart_kernel()),
            (("variables", "vars"), "Toggle variables panel", lambda arg="": self.action_toggle_vars()),
            (("lazy",), "Toggle lazy mode", lambda arg="": self.action_toggle_lazy()),
            (("new",), "New cell below", lambda arg="": self.action_add_cell()),
            (("delete", "del"), "Delete current cell", lambda arg="": self.action_delete_cell()),
            (("interrupt",), "Interrupt running code", lambda arg="": self.kernel.interrupt()),
            (("help", "h"), "List : commands", lambda arg="": self._ex_help()),
        ]

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

    def _ex_help(self) -> None:
        lines = [f":{names[0]:<10} {desc}" for names, desc, _ in self.ex_commands()]
        self.notify("\n".join(lines), title="Commands", timeout=15)

    async def _run_ex(self, command: str | None) -> None:
        if not command:
            return
        name, _, arg = command.strip().partition(" ")
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
            if names[0] in ("q", "help"):
                continue
            yield SystemCommand(f"{desc}  (:{names[0]})", f"Also available as :{names[0]}", self._palette(handler))

    def _palette(self, handler):
        def callback() -> None:
            result = handler("")
            if asyncio.iscoroutine(result):
                self.run_worker(result)

        return callback
