"""One notebook window: a column of cells, a variables panel and a status line.

A NotebookPane owns everything that belongs to one open file: the path, the kernel,
the cells, the undo stack and the dirty flag. The app (app.py) hosts panes in tabs and
splits (windows.py) and forwards its key bindings to the active pane.
"""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, Static

from . import config, envs, notebook
from .cell import Cell, CellSnapshot, ends_with_string, outputs_to_ipynb
from .graph import Graph, build
from .kernel import Kernel
from .vim import VimTextArea

INITIAL_CELLS = 12  # cells composed before the first frame; the rest mount right after
MOUNT_BATCH = 10
MAX_UNDO = 50


class NotebookPane(Vertical):
    """A window showing one notebook (vim: a window showing a buffer)."""

    def __init__(self, path: Path | None = None) -> None:
        super().__init__(classes="pane")
        self.path = path
        self.markers = True
        self.dirty = False
        self.exec_counter = 0  # Jupyter's In [n]
        self.kernel_state = "starting kernel…"
        self.kernel_label = "Python …"  # filled in once the kernel reports its version
        remembered = config.load().get(f"env:{path.resolve()}") if path is not None else None
        env, self._env_hint = envs.default_for(path, remembered)
        self.kernel = Kernel(file=path, env=env)
        self._kernel_started = False
        self._shutting_down = False
        self._booted = asyncio.Event()  # set when the kernel boot finished (well or badly)
        self._run_lock = asyncio.Lock()
        self.loaded = asyncio.Event()  # set once every cell of the file is mounted
        self._specs: list[notebook.CellSpec] = []
        self._pending_sources: list[notebook.CellSpec] = []  # cells not mounted yet (progressive mount)
        self._cells_box: VerticalScroll | None = None
        self._vars: DataTable | None = None
        self._status: Static | None = None
        self._status_key: tuple | None = None
        self.last_focus = None  # the cell or editor that had focus when this pane was last active
        # Cell-level undo (u / z in cell mode), newest last: ("delete", snapshot),
        # ("move", cid, from_index, to_index), ("insert", cid), ("kind", cid, old_kind).
        self._undo: list[tuple] = []
        self._cmd_pending = ""  # cell mode: first key of gg / dd / yy
        self._cmd_count = ""
        self._kind_before_y: str | None = None  # `y` makes a cell code at once; `yy` (copy) undoes that
        self.load_error: str | None = None
        self._read_file()

    def _read_file(self) -> None:
        specs = [notebook.CellSpec("")]
        if self.path is not None and self.path.exists():
            try:
                specs, self.markers = notebook.load_cells(self.path)
            except Exception as error:  # a file that is not a notebook: show it as one cell
                self.load_error = f"Could not parse {self.path}: {error}"
                specs = [notebook.CellSpec(self.path.read_text(errors="replace"))]
            specs = specs or [notebook.CellSpec("")]
        self._specs = specs

    @property
    def name(self) -> str:
        return self.path.name if self.path else "untitled"

    @property
    def title(self) -> str:
        return (str(self.path) if self.path else "untitled") + (" •" if self.dirty else "")

    @property
    def is_blank(self) -> bool:
        """An untitled, unchanged notebook with one empty cell: safe to replace with a file."""
        cells = self.cells()
        return self.path is None and not self.dirty and len(cells) == 1 and not cells[0].source.strip()

    # ── compose / mount ──────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        specs, self._pending_sources = self._specs[:INITIAL_CELLS], self._specs[INITIAL_CELLS:]
        with Horizontal(classes="pane-body"):
            with VerticalScroll(id="cells") as self._cells_box:
                for n, spec in enumerate(specs, 1):
                    cell = Cell(spec.source, kind=spec.kind, spec=spec)
                    cell.set_label(n)
                    yield cell
            self._vars = DataTable(id="vars", cursor_type="row", zebra_stripes=True)
            self._vars.add_columns("name", "type", "value")
            self._vars.display = False  # f2 / :vars shows it
            yield self._vars
        self._status = Static(classes="pane-status")
        yield self._status

    def on_mount(self) -> None:
        if (boot := self.start_kernel()) is not None:
            self.run_worker(boot, group="kernel")
        self.render_status()
        if self.load_error:
            self.notify(self.load_error, severity="warning")
        if self._env_hint is not None:
            self.notify(f"Found {self._env_hint.name} without ipykernel: f6 to run the kernel there (ipykernel is installed for you)", timeout=8)
        self.call_after_refresh(lambda: self.run_worker(self._mount_rest(), group="mount"))

    async def _mount_rest(self) -> None:
        """Mount the cells left out of compose(), a batch at a time, after the first frame."""
        while self._pending_sources:
            batch = [Cell(spec.source, kind=spec.kind, spec=spec) for spec in self._pending_sources[:MOUNT_BATCH]]
            del self._pending_sources[:MOUNT_BATCH]
            first = len(self._cells_box.children) + 1
            for n, cell in enumerate(batch, first):
                cell.set_label(n)
            await self._cells_box.mount_all(batch)
            await asyncio.sleep(0)  # let keys and repaints through between batches
        self.relabel()
        self.refresh_info()
        self.loaded.set()

    def start_kernel(self):
        """The coroutine that boots the kernel, once; None if it was already started."""
        if self._kernel_started:
            return None
        self._kernel_started = True
        return self._boot_kernel()

    async def _boot_kernel(self) -> None:
        try:
            await self.kernel.start()
            self.kernel_label = await self._kernel_version()
            self.set_kernel_state("kernel ready")
        except Exception as error:  # never let a kernel problem take the UI down
            if not self._shutting_down and self.is_attached:
                self.set_kernel_state("no kernel")
                self.notify(f"Kernel could not start: {error}", severity="error", timeout=10)
        finally:
            self._booted.set()

    async def switch_env(self, env: envs.Env) -> None:
        """Run this notebook's kernel in another environment; remembered per notebook file."""
        self.set_kernel_state(f"starting kernel in {env.name}…")
        try:
            await self.kernel.switch(env)
        except Exception as error:
            self.notify(f"Could not start a kernel in {env.python}: {error}", severity="error", timeout=10)
            if not self.kernel.ready.is_set():  # the fallback to the old env failed too
                self.set_kernel_state("kernel failed to start (f6 picks another env)")
                return
        else:
            if self.path is not None:
                config.save({f"env:{self.path.resolve()}": str(env.python)})
        self.kernel_label = await self._kernel_version()
        self._kernel_reset()
        self.set_kernel_state("kernel ready")

    async def _kernel_version(self) -> str:
        """'Python 3.13.7' from the kernel itself (it may be a different interpreter than ours)."""
        out: list[str] = []
        code = "print(__import__('platform').python_version())"
        await self.kernel._execute(code, lambda kind, c: out.append(c.get("text", "")) if kind == "stream" else None, store_history=False)
        version = "".join(out).strip()
        label = f"Python {version}" if version else "Python"
        return label if self.kernel.env.kind in ("system", "uv-python") else f"{label} · {self.kernel.env.name}"

    def set_kernel_state(self, state: str) -> None:
        self.kernel_state = state
        self.render_status()
        self.app.update_status()

    async def close(self) -> None:
        """Remove the window; its kernel shuts down in the background."""
        self._shutting_down = True
        self.app.run_worker(self._shutdown_kernel(), group="shutdown")
        await self.remove()

    async def _shutdown_kernel(self) -> None:
        """Stop the kernel once its boot (if any) has finished: stopping it mid-start races."""
        if self._kernel_started:
            await self._booted.wait()
        await self.kernel.shutdown()

    async def on_unmount(self) -> None:
        if not self._shutting_down:
            self._shutting_down = True
            await self._shutdown_kernel()

    def focus_pane(self) -> None:
        """Give focus back to the cell (or editor) that had it last."""
        target = self.last_focus
        if target is None or not target.is_attached or not target.display:
            cells = self.cells()
            if not cells:
                return
            target = cells[0] if cells[0].editor_hidden else cells[0].editor
        target.focus(scroll_visible=False)

    def _on_descendant_focus(self, event: events.DescendantFocus) -> None:
        widget = event.widget
        if isinstance(widget, (Cell, VimTextArea)):
            self.last_focus = widget
        self.app.pane_focused(self)

    # ── status line (vim: the per-window status line) ────────────────────

    def set_active(self, active: bool) -> None:
        self.set_class(active, "-active")
        self._status_key = None
        self.render_status()

    def show_status(self, show: bool) -> None:
        """The status line is shown only when a tab has several windows (vim's laststatus=1)."""
        self.set_class(show, "-multi")

    def render_status(self) -> None:
        if self._status is None:
            return
        key = (self.title, self.kernel_label, self.kernel_state, self.has_class("-active"))
        if key == self._status_key:
            return
        self._status_key = key
        active = self.has_class("-active")
        self._status.update(
            Text.assemble(
                (f" {self.title} ", "bold" if active else ""),
                (f" {self.kernel_label} · {self.kernel_state}", "" if active else "dim"),
            ),
            layout=False,
        )

    def mark_dirty(self, dirty: bool = True) -> None:
        if dirty != self.dirty:
            self.dirty = dirty
            self.render_status()
            self.app.update_status()

    # ── cells ────────────────────────────────────────────────────────────

    def cells(self) -> list[Cell]:
        """Cells in document order. They are the direct children of #cells, so no DOM search."""
        box = self._cells_box
        if box is None:
            return list(self.query(Cell))
        return [node for node in box.children if isinstance(node, Cell)]

    def current_cell(self) -> Cell | None:
        """The cell holding the focus, or the one that held it last while this pane was active."""
        node = self.app.focused
        while node is not None and not isinstance(node, Cell):
            node = node.parent
        if node is not None and node.pane is self:
            return node
        node = self.last_focus
        while node is not None and not isinstance(node, Cell):
            node = node.parent
        if node is not None and node.is_attached:
            return node
        cells = self.cells()
        return cells[0] if cells else None

    def relabel(self) -> None:
        """Headers show display positions; renumber after any insert, delete or move."""
        for n, cell in enumerate(self.cells(), 1):
            cell.set_label(n)

    def graph(self) -> Graph:
        """The dependency graph of the code cells (markdown cells never run)."""
        return build({c.cid: c.source for c in self.cells() if c.kind == "code"})

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
            by_id = {c.cid: c for c in self.cells() if c.kind == "code"}
            roots = graph.expand_roots([r for r in roots if r in by_id])
            if downstream is None:
                downstream = not self.app.lazy
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
                cell.echo_suppressed = ends_with_string(source)
                await self.kernel.delete(cell.last_defs | graph.defs[cid])
                # Deferred clear: the old output stays until the first new output arrives,
                # so the cell does not collapse and grow again (the page would jump).
                cell.add_output("clear_output", {"wait": True})
                self.exec_counter += 1
                cell.begin_run(self.exec_counter)
                ok = await self.kernel.execute(source, cell.add_output)
                if cell.output_box.clear_pending:  # no output at all: clear the old one now
                    cell.clear_output()
                cell.ran_source = source
                cell.last_defs = graph.defs[cid]
                cell.end_run("ok" if ok and cell.source == source else "error" if not ok else "stale")
                if not ok:
                    failed.add(cid)
                self._keep_focus_visible(cell)

            await self.refresh_vars(graph)

    def _keep_focus_visible(self, ran: Cell) -> None:
        """New output above the focused cell pushes it down; keep the user's cell on screen."""
        current = self.current_cell()
        if current is not None and current is not ran and self.app.focused is not None and current.pane is self:
            self.call_after_refresh(current.scroll_visible, animate=False, immediate=True)

    async def refresh_vars(self, graph: Graph) -> None:
        names = set().union(*graph.defs.values()) if graph.defs else set()
        rows = await self.kernel.variables(names)
        table = self._vars
        if table is None:
            return
        table.clear()
        for row in rows:
            table.add_row(*row)

    def write_file(self) -> None:
        cells = [self._spec_of(c) for c in self.cells()] + self._pending_sources
        notebook.save(self.path, cells, self.markers)
        self.mark_dirty(False)
        self.notify(f"Saved {self.path}")

    def _spec_of(self, cell: Cell) -> notebook.CellSpec:
        """The CellSpec to save for `cell`: its loaded spec (format extras) with the current state."""
        outputs = outputs_to_ipynb(cell.outputs_snapshot(), cell.exec_count or None) if cell.kind == "code" else []
        count = (cell.exec_count or None) if cell.kind == "code" else None
        if cell.spec is not None:
            return dataclasses.replace(cell.spec, source=cell.source, kind=cell.kind, hide_code=cell.code_hidden, outputs=outputs, execution_count=count)
        return notebook.CellSpec(cell.source, cell.kind, hide_code=cell.code_hidden, outputs=outputs, execution_count=count)

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
        if mode == "cell" or cell.editor_hidden:  # rendered markdown / hidden code: select it; enter edits
            cell.focus(scroll_visible=False)
        else:
            editor = cell.editor
            editor.focus(scroll_visible=False)  # Textual's own scroll is animated
            if mode in ("normal", "insert") and VimTextArea.mode != mode:
                editor.set_mode(mode)
            if row is not None:
                editor.move_cursor((editor.document.line_count - 1 if row < 0 else row, 0))
        self._scroll_to_cell(cell)
        # A cell mounted in this same frame has no region yet: scroll again after layout.
        self.call_after_refresh(cell.scroll_visible, animate=False, immediate=True)

    def _scroll_to_cell(self, cell: Cell) -> None:
        """Scroll `cell` into view now (Textual's own focus scroll is animated).

        Measured and rejected: forcing a layout pass instead of a scroll (to avoid the
        compositor rebuilding its full map afterwards) made moves ~20 % slower.
        """
        cell.scroll_visible(animate=False, immediate=True)

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
        return CellSnapshot(index, cell.source, cell.kind, cell.ran_source is not None, cell.outputs_snapshot(), cell.spec)

    def _push_undo(self, action: tuple) -> None:
        self._undo.append(action)
        del self._undo[:-MAX_UNDO]

    async def _insert(
        self, anchor: Cell | None, above: bool = False, snapshot: CellSnapshot | None = None, record: bool = True
    ) -> Cell:
        """Mount a new (or restored) cell next to `anchor`; renumber; mark dirty.

        `record` puts an ("insert", cid) entry on the undo stack (undo removes the cell).
        """
        cell = Cell(snapshot.source, snapshot.kind, snapshot.spec) if snapshot else Cell()
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
        if record:
            self._push_undo(("insert", cell.cid))
        self.relabel()
        self.refresh_info()
        self.mark_dirty()
        return cell

    # ── actions (the app's bindings call these on the active pane) ───────

    async def run_cell(self, advance: str = "stay") -> None:
        """Run the current cell. advance: "stay", "next" (Jupyter shift+enter) or "insert" (alt+enter)."""
        cell = self.current_cell()
        if cell is None:
            return
        mode = self.app.ui_mode
        if cell.kind == "markdown":  # "running" markdown renders it; the kernel is not involved
            if self.app.focused is not cell:
                cell.focus(scroll_visible=False)
            await cell.show_rendered()
        else:
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

    def run_all(self) -> None:
        self.run_worker(self.run_cells([c.cid for c in self.cells()], downstream=True), group="run")

    def run_stale(self) -> None:
        stale = [c.cid for c in self.cells() if c.kind == "code" and c.status in ("stale", "idle") and c.source.strip()]
        if stale:
            self.run_worker(self.run_cells(stale, downstream=True), group="run")

    def run_relative(self, below: bool) -> None:
        cell = self.current_cell()
        cells = self.cells()
        if cell is None or cell not in cells:
            return
        index = cells.index(cell)
        subset = cells[index:] if below else cells[:index]
        if subset:
            self.run_worker(self.run_cells([c.cid for c in subset], downstream=False), group="run")

    async def add_cell(self, above: bool = False) -> None:
        cell = await self._insert(self.current_cell(), above)
        self.select(cell, "insert")

    async def delete_cell(self, count: int = 1) -> None:
        """Delete `count` cells from the current one (dependents re-run); u in cell mode undoes."""
        cell = self.current_cell()
        if cell is None:
            return
        cells = self.cells()
        index = cells.index(cell)
        await self._remove_cells(cells[index : index + max(1, count)], self.app.ui_mode)

    async def _remove_cells(self, victims: list[Cell], mode: str, record: bool = True) -> None:
        """Remove cells (their variables leave the kernel, dependents re-run); select the next one."""
        cells = self.cells()
        index = cells.index(victims[0])
        ids = [c.cid for c in victims]
        graph = self.graph()
        downstream = graph.descendants([i for i in ids if i in graph.children]) - set(ids)
        defs = set().union(*(c.last_defs for c in victims))
        if record:
            for offset in range(len(victims) - 1, -1, -1):  # undo restores the first one first
                self._push_undo(("delete", self._snapshot(victims[offset], index + offset)))
        await self._cells_box.remove_children(victims)
        if not self.cells():
            await self._cells_box.mount(Cell())
        remaining = self.cells()
        self.relabel()
        self.mark_dirty()
        self.select(remaining[min(index, len(remaining) - 1)], "cell" if mode == "cell" else "keep")

        # Reactivity: the deleted cells' variables vanish, so dependents re-run.
        await self.kernel.delete(defs)
        if downstream:
            self.run_worker(self.run_cells(sorted(downstream)), group="run")
        else:
            self.refresh_info()
            self.run_worker(self.refresh_vars(self.graph()), group="run")

    async def undo(self) -> None:
        """Undo the newest cell-level action: delete, move, insert or kind change."""
        mode = self.app.ui_mode
        keep = "cell" if mode == "cell" else "keep"
        while self._undo:
            action = self._undo.pop()
            if action[0] == "delete":
                snapshot = action[1]
                cells = self.cells()
                if snapshot.index < len(cells):
                    cell = await self._insert(cells[snapshot.index], above=True, snapshot=snapshot, record=False)
                else:
                    cell = await self._insert(cells[-1] if cells else None, snapshot=snapshot, record=False)
                self.select(cell, keep)
                return
            cell = next((c for c in self.cells() if c.cid == action[1]), None)
            if cell is None:  # the cell is gone (e.g. deleted later): skip this entry
                continue
            if action[0] == "move":
                self._move_to(cell, action[2])
                self.select(cell, keep)
            elif action[0] == "insert":
                await self._remove_cells([cell], mode, record=False)
            elif action[0] == "kind":
                await self.set_cell_kind(cell, action[2], record=False)
                self.select(cell, keep)
            return
        self.notify("Nothing to undo")

    def _move_to(self, cell: Cell, index: int) -> None:
        """Put `cell` at display position `index` (0-based); renumber; mark dirty."""
        others = [c for c in self.cells() if c is not cell]
        if not others:
            return
        if index < len(others):
            self._cells_box.move_child(cell, before=others[index])
        else:
            self._cells_box.move_child(cell, after=others[-1])
        self.relabel()
        self.refresh_info()
        self.mark_dirty()
        cell.scroll_visible(animate=False)

    def copy_cell(self) -> None:
        if cell := self.current_cell():
            self.app._cell_clipboard = self._snapshot(cell, self.cells().index(cell))

    async def cut_cell(self) -> None:
        self.copy_cell()
        await self.delete_cell()

    async def paste_cell(self, above: bool = False) -> None:
        clip = self.app._cell_clipboard
        if clip is None:
            self.notify("No cell copied (yy or x in cell mode)")
            return
        mode = self.app.ui_mode
        # A copy is a new cell: it does not inherit file-format extras (e.g. a marimo cell name).
        clip = dataclasses.replace(clip, spec=None)
        cell = await self._insert(self.current_cell(), above, snapshot=clip)
        self.select(cell, "cell" if mode == "cell" else "keep")

    def move_cell(self, step: int) -> None:
        cell = self.current_cell()
        cells = self.cells()
        if cell is None or cell not in cells:
            return
        start = cells.index(cell)
        target = start + step
        if not 0 <= target < len(cells):
            return
        self._move_to(cell, target)
        self._push_undo(("move", cell.cid, start, target))

    def focus_cell(self, step: int) -> None:
        cells = self.cells()
        current = self.current_cell()
        index = cells.index(current) + step if current in cells else 0
        if abs(step) > 1:  # J/K with a count stop at the first/last cell
            index = max(0, min(len(cells) - 1, index))
        if 0 <= index < len(cells) and cells[index] is not current:
            if isinstance(self.app.focused, Cell) or cells[index].editor_hidden:
                self.select(cells[index], "cell")
                return
            editor = cells[index].editor
            editor.focus(scroll_visible=False)  # Textual's own scroll is animated
            # Land on the nearest line, like moving through one long buffer.
            editor.move_cursor((0, 0) if step > 0 else (editor.document.line_count - 1, 0))
            self._scroll_to_cell(cells[index])

    def focus_edge(self, last: bool) -> None:
        mode = self.app.ui_mode
        if last:
            self._after_loaded(lambda: self.goto_cell(len(self.cells()), mode, row=-1))
        else:
            self.goto_cell(1, mode)

    async def set_cell_kind(self, cell: Cell, kind: str, record: bool = True) -> None:
        """Switch a cell between code and markdown; selection stays on the cell.

        code → markdown works like deleting the code: its variables leave the kernel
        and the cells that used them re-run (and now fail, as in marimo).
        """
        if kind == cell.kind or kind not in ("code", "markdown"):
            return
        if record:
            self._push_undo(("kind", cell.cid, cell.kind))
        self.mark_dirty()
        if kind == "code":
            await cell.set_kind("code")
            self.refresh_info()
            return
        graph = self.graph()
        downstream = graph.descendants([cell.cid]) - {cell.cid}
        defs = set(cell.last_defs)
        await cell.set_kind("markdown")
        self.refresh_info()
        if not defs:  # never ran: nothing in the kernel came from it
            return
        await self.kernel.delete(defs)
        if downstream:
            self.run_worker(self.run_cells(sorted(downstream)), group="run")
        else:
            self.run_worker(self.refresh_vars(self.graph()), group="run")

    def save(self, then=None) -> None:
        """Save; ask for a name when untitled. `then` runs after a successful save."""
        if self.path is None:
            from .app import Prompt  # noqa: PLC0415

            self.app.push_screen(Prompt("Save as: ", placeholder="notebook.py"), lambda name: self.saved_as(name, then))
        else:
            self.write_file()
            if then is not None:
                then()

    async def saved_as(self, name: str | None, then=None) -> None:
        if not name:
            return
        self.path = Path(name).expanduser()
        self.write_file()
        self.app.update_status()
        if self.kernel.ready.is_set():
            await self.kernel.set_file(self.path)
        if then is not None:
            result = then()
            if asyncio.iscoroutine(result):
                await result

    async def interrupt(self) -> None:
        await self.kernel.interrupt()

    def toggle_vars(self) -> None:
        if self._vars is not None:
            self._vars.display = not self._vars.display

    async def restart_kernel(self) -> None:
        self.set_kernel_state("restarting kernel…")
        await self.kernel.restart()
        self._kernel_reset()
        self.set_kernel_state("kernel ready")

    def _kernel_reset(self) -> None:
        """A fresh kernel process: every cell that ran is stale, the variables panel is empty."""
        for cell in self.cells():
            cell.last_defs = set()
            if cell.ran_source is not None:
                cell.set_status("stale")
        if self._vars is not None:
            self._vars.clear()

    def clear_outputs(self) -> None:
        for cell in self.cells():
            cell.clear_output()

    def on_click(self, event) -> None:
        """Clicking the empty space below the last cell focuses the nearest cell."""
        if event.widget is self._cells_box and (cells := self.cells()):
            if cells[-1].editor_hidden:
                cells[-1].focus()
            else:
                cells[-1].editor.focus()

    def jump(self, target: str, mode: str) -> None:
        """'3' focuses the third cell by display position; 'text' the next cell containing text."""

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

    # ── cell mode ────────────────────────────────────────────────────────

    async def cell_command(self, cell: Cell, key: str) -> bool:
        """One key in cell mode (the cell itself has focus). Returns False if unknown."""
        app = self.app
        if key.isdigit() and (key != "0" or self._cmd_count):
            self._cmd_count += key
            return True
        had_count = bool(self._cmd_count)
        n = int(self._cmd_count or 1)
        pending, self._cmd_pending, self._cmd_count = self._cmd_pending, "", ""
        if pending == "g":
            if key == "g":
                self.goto_cell(n, "cell")
            elif key == "t":
                app.tab_switch(n if had_count else None, 1)
            elif key == "T":
                app.tab_switch(None, -n)
            return True
        if pending == "d":
            if key == "d":
                await self.delete_cell(n)
            return True
        if pending == "y":
            kind_before, self._kind_before_y = self._kind_before_y, None
            if key == "y":
                if kind_before == "markdown" and cell.kind == "code":
                    await cell.set_kind("markdown")  # yy copies; undo the `y` that made it code
                    if self._undo and self._undo[-1] == ("kind", cell.cid, "markdown"):
                        self._undo.pop()
                self.copy_cell()
                return True
            # `y` alone already made the cell code; this key is a new command.
        match key:
            case "g" | "d" | "y":
                if key == "y":
                    self._kind_before_y = cell.kind
                    await self.set_cell_kind(cell, "code")
                self._cmd_pending = key
                self._cmd_count = str(n) if had_count else ""
            case "j" | "down":
                self.focus_cell(n)
            case "k" | "up":
                self.focus_cell(-n)
            case "G":
                if had_count:
                    self.goto_cell(n, "cell")
                else:
                    self._after_loaded(lambda: self.goto_cell(len(self.cells()), "cell"))
            case "enter" | "i":
                cell.start_editing()  # markdown: show the editor instead of the rendered text
                self.select(cell, "normal" if key == "enter" else "insert")
            case "a" | "b":
                new = await self._insert(cell, above=key == "a")
                self.select(new, "insert")
            case "u" | "z":
                await self.undo()
            case "x":
                await self.cut_cell()
            case "p" | "P":
                await self.paste_cell(above=key == "P")
            case "J" | "shift+j":
                self.move_cell(1)
            case "K" | "shift+k":
                self.move_cell(-1)
            case "m":
                await self.set_cell_kind(cell, "markdown")
            case "h":
                cell.set_code_hidden(not cell.code_hidden)
                self.mark_dirty()
            case "o":
                cell.toggle_collapsed()
            case "O" | "shift+o":
                cell.clear_output()
            case "l":
                app.set_line_numbers(not VimTextArea.line_numbers)
            case "/":
                app._open_search()
            case ":":
                app._open_ex()
            case "?" | "question_mark":
                app.action_help()
            case "escape":
                pass
            case _:
                return False
        return True

    # ── vim glue (messages from the editors in this pane) ────────────────

    @on(VimTextArea.Changed)
    def _edited(self) -> None:
        if not self.dirty:
            self.mark_dirty()

    @on(VimTextArea.EdgeReached)
    def _edge(self, event: VimTextArea.EdgeReached) -> None:
        event.stop()
        self.focus_cell(event.step)

