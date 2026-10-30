"""The Textual front end: a column of cells plus a variables panel."""

from __future__ import annotations

import asyncio
import itertools
from pathlib import Path

from rich.console import Group
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, Footer, Header, Static, TextArea

from . import notebook
from .graph import Graph, build
from .kernel import Kernel

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


def _render(kind: str, text: str) -> Text:
    if kind == "error":
        return Text(text, style="bold red")
    if kind == "note":
        return Text(text, style="dim italic")
    return Text.from_ansi(text, style="red" if kind == "stderr" else "")


class Cell(Vertical):
    """One code cell: a header line, an editor and an output area."""

    def __init__(self, source: str = "") -> None:
        self.cid = f"c{next(_ids)}"
        super().__init__(id=self.cid)
        self.initial_source = source
        self.ran_source: str | None = None
        self.last_defs: set[str] = set()
        self.status = "idle"
        self.outputs: list[tuple[str, str]] = []  # (kind, text)
        self.info = ""

    def compose(self) -> ComposeResult:
        yield Static(classes="cell-header")
        yield TextArea.code_editor(self.initial_source, language="python", theme="monokai")
        yield Static(classes="cell-output")

    def on_mount(self) -> None:
        self._render_header()
        self._render_output()

    @property
    def editor(self) -> TextArea:
        return self.query_one(TextArea)

    @property
    def source(self) -> str:
        return self.editor.text

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
        self.info = "  ·  ".join(parts)
        self._render_header()

    def clear_output(self) -> None:
        self.outputs = []
        self._render_output()

    def show_error(self, message: str) -> None:
        self.outputs = [("error", message)]
        self._render_output()

    def add_output(self, kind: str, content: dict) -> None:
        if kind == "clear_output":
            self.outputs = []
        elif kind == "stream":
            name = content["name"]
            if self.outputs and self.outputs[-1][0] == name:
                name, text = self.outputs.pop()
                self.outputs.append((name, (text + content["text"])[-MAX_STREAM_CHARS:]))
            else:
                self.outputs.append((name, content["text"]))
        elif kind in ("execute_result", "display_data"):
            data = content.get("data", {})
            if "text/plain" in data:
                self.outputs.append(("result", data["text/plain"]))
            else:
                self.outputs.append(("note", f"[{', '.join(data)} output not supported yet]"))
        elif kind == "error":
            self.outputs.append(("traceback", "\n".join(content["traceback"])))
        self._render_output()

    def _render_header(self) -> None:
        if not self.is_mounted:
            return
        icon, style = STATUS_ICONS[self.status]
        header = Text.assemble((f"{icon} ", style), (self.cid, "bold"), ("  " + self.info, "dim"))
        self.query_one(".cell-header", Static).update(header)

    def _render_output(self) -> None:
        if not self.is_mounted:
            return
        out = self.query_one(".cell-output", Static)
        out.display = bool(self.outputs)
        out.update(Group(*(_render(kind, text) for kind, text in self.outputs)))

    @on(TextArea.Changed)
    def _mark_stale(self) -> None:
        if self.ran_source is not None and self.status != "running":
            self.set_status("stale" if self.source != self.ran_source else "ok")


class GPython(App):
    CSS_PATH = "app.tcss"
    TITLE = "gpython"
    BINDINGS = [
        Binding("ctrl+r", "run_cell", "Run", priority=True),
        Binding("ctrl+n", "add_cell", "New cell", priority=True),
        Binding("ctrl+d", "delete_cell", "Delete cell", priority=True),
        Binding("ctrl+up,alt+up", "focus_cell(-1)", "Prev cell", show=False, priority=True),
        Binding("ctrl+down,alt+down", "focus_cell(1)", "Next cell", show=False, priority=True),
        Binding("ctrl+s", "save", "Save", priority=True),
        Binding("ctrl+k", "interrupt", "Interrupt", priority=True),
        Binding("f2", "toggle_vars", "Variables"),
    ]

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path
        self.kernel = Kernel()
        self._run_lock = asyncio.Lock()

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with VerticalScroll(id="cells"):
                for source in notebook.load(self.path):
                    yield Cell(source)
            table = DataTable(id="vars", cursor_type="row", zebra_stripes=True)
            table.add_columns("name", "type", "value")
            yield table
        yield Footer()

    async def on_mount(self) -> None:
        self.sub_title = f"{self.path}  ·  starting kernel…"
        self.cells()[0].editor.focus()
        self.refresh_info()
        self.run_worker(self._start_kernel(), group="kernel")

    async def _start_kernel(self) -> None:
        await self.kernel.start()
        self.sub_title = f"{self.path}  ·  kernel ready"

    async def on_unmount(self) -> None:
        await self.kernel.shutdown()

    # ── helpers ──────────────────────────────────────────────────────────

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

    async def run_cells(self, roots: list[str]) -> None:
        """Run roots and every cell downstream of them, in dependency order."""
        async with self._run_lock:
            graph = self.refresh_info()
            by_id = {c.cid: c for c in self.cells()}
            order = [cid for cid in graph.run_order([r for r in roots if r in by_id])]
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

    # ── actions ──────────────────────────────────────────────────────────

    def action_run_cell(self) -> None:
        if cell := self.current_cell():
            self.run_worker(self.run_cells([cell.cid]), group="run")

    def action_run_all(self) -> None:
        self.run_worker(self.run_cells([c.cid for c in self.cells()]), group="run")

    async def action_add_cell(self) -> None:
        cell = Cell()
        current = self.current_cell()
        container = self.query_one("#cells", VerticalScroll)
        if current:
            await container.mount(cell, after=current)
        else:
            await container.mount(cell)
        cell.editor.focus()
        cell.scroll_visible()

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
            cells[index].editor.focus()
            cells[index].scroll_visible()

    def action_save(self) -> None:
        notebook.save(self.path, [c.source for c in self.cells()])
        self.notify(f"Saved {self.path}")

    async def action_interrupt(self) -> None:
        await self.kernel.interrupt()

    def action_toggle_vars(self) -> None:
        table = self.query_one("#vars")
        table.display = not table.display

    async def action_restart_kernel(self) -> None:
        self.sub_title = f"{self.path}  ·  restarting kernel…"
        await self.kernel.restart()
        for cell in self.cells():
            cell.last_defs = set()
            if cell.ran_source is not None:
                cell.set_status("stale")
        self.query_one("#vars", DataTable).clear()
        self.sub_title = f"{self.path}  ·  kernel ready"

    def get_system_commands(self, screen):
        yield from super().get_system_commands(screen)
        yield SystemCommand("Run all cells", "Run every cell in dependency order", self.action_run_all)
        yield SystemCommand(
            "Restart kernel", "Start a fresh Python process", lambda: self.run_worker(self.action_restart_kernel())
        )
        yield SystemCommand("Save notebook", f"Write cells to {self.path}", self.action_save)
