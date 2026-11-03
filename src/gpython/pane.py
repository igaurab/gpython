"""One notebook window: a column of cells, a variables panel and a status line.

A NotebookPane owns everything that belongs to one open file: the path, the kernel,
the cells, the undo stack and the dirty flag. The app (app.py) hosts panes in tabs and
splits (windows.py) and forwards its key bindings to the active pane.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from pathlib import Path

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.geometry import Region
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Static

from . import config, envpicker, envs, notebook, packages
from .cell import Cell, CellSnapshot, ends_with_string, outputs_to_ipynb, time_brief, time_summary
from .dom import forget_removed
from .graph import Graph, build
from .kernel import Kernel
from .outline import Outline, Value, Values
from .vim import VimTextArea, search_regex

MIN_INITIAL_CELLS = 4
MOUNT_BATCH = 10
MAX_UNDO = 50
DEBUGGABLE_ERRORS = {
    "IndexError", "KeyError", "ZeroDivisionError", "AssertionError", "ValueError", "TypeError",
    "AttributeError", "RecursionError", "StopIteration", "OverflowError", "UnboundLocalError",
}


def needs_run(cell: Cell) -> bool:
    """A non-empty code cell that never ran, was edited since it ran, or whose inputs changed (marimo's "stale")."""
    return bool(cell.source.strip()) and (cell.inputs_stale or cell.ran_source != cell.source)


class NotebookPane(Vertical):
    """A window showing one notebook (vim: a window showing a buffer)."""

    def __init__(self, path: Path | None = None) -> None:
        super().__init__(classes="pane")
        self.path = path
        self.markers = False
        self.dirty = False
        self.exec_counter = 0
        self.kernel_state = "starting kernel…"
        self.kernel_label = "Python …"
        remembered = config.load().get(f"env:{path.resolve()}") if path is not None else None
        env, self._env_hint = envs.default_for(path, remembered)
        self.kernel = Kernel(file=path, env=env)
        self._kernel_started = False
        self._shutting_down = False
        self._booted = asyncio.Event()
        self._run_lock = asyncio.Lock()
        self.tried: set[str] = set()
        self.loaded = asyncio.Event()
        self._specs: list[notebook.CellSpec] = []
        self._pending_sources: list[notebook.CellSpec] = []
        self._outputs_timer = None
        self._outputs_to_store: tuple[Path, list[notebook.CellSpec], str] | None = None
        self._run_state_json: dict[str, tuple[tuple, str]] = {}
        self._cells_box: VerticalScroll | None = None
        self._outline: Outline | None = None
        self._outline_timer: Timer | None = None
        self._values: dict[str, Value] = {}
        self._var_rows: dict[str, list] = {}
        self._side = None
        self._variables = None
        self.debug = None
        self._debug_line: tuple | None = None
        self._debug_keep_view = False
        self._timeline_wanted = False
        self._input_screen = None
        self._status: Static | None = None
        self.zoomed = False
        self._status_key: tuple | None = None
        self.last_focus = None
        self._undo: list[tuple] = []
        self._cmd_pending = ""
        self._cmd_count = ""
        self._kind_before_y: str | None = None
        self.search_pattern = ""
        self.search_highlight = False
        self._markdown_warned: tuple[str, float] | None = None
        self.load_error: str | None = None
        self._read_file()

    def _read_file(self) -> None:
        specs = [notebook.CellSpec("")]
        if self.path is not None and self.path.exists():
            try:
                specs, self.markers = notebook.load_cells(self.path)
            except Exception as error:
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


    def _initial_count(self) -> int:
        """How many cells to compose before the first frame: enough to fill the screen.

        Every cell costs ~1 ms of CSS and layout before the first paint, so only the
        ones that can be visible are composed now; the rest mount after the first frame.
        """
        height = self.app.size.height or 45
        rows = 0
        for n, spec in enumerate(self._specs, 1):
            rows += spec.source.count("\n") + 3
            if rows >= height and n >= MIN_INITIAL_CELLS:
                return n
        return len(self._specs)

    def compose(self) -> ComposeResult:
        initial = self._initial_count()
        specs, self._pending_sources = self._specs[:initial], self._specs[initial:]
        with Horizontal(classes="pane-body"):
            with VerticalScroll(id="cells") as self._cells_box:
                for n, spec in enumerate(specs, 1):
                    cell = Cell(spec.source, kind=spec.kind, spec=spec)
                    cell.set_label(n)
                    yield cell
        self._status = Static(classes="pane-status")
        yield self._status

    def on_mount(self) -> None:
        self.render_status()
        if self.load_error:
            self.notify(self.load_error, severity="warning")
        if self._env_hint is not None:
            self.notify(f"Found {self._env_hint.name} without ipykernel: f6 to run the kernel there (ipykernel is installed for you)", timeout=8)
        self.app.after_first_frame(self._after_first_frame)

    def _after_first_frame(self) -> None:
        self.run_worker(self._restore_outputs_then_mount_rest(), group="mount")
        if (boot := self.start_kernel()) is not None:
            self.run_worker(boot, group="kernel")

    async def _restore_outputs_then_mount_rest(self) -> None:
        if self.path is not None and not self.markers:
            await self._restore_outputs()
        await self._mount_rest()

    async def _restore_outputs(self) -> None:
        """A plain script's stored outputs are read after the first frame (sidecar.py), then shown."""
        cells = self.cells()
        specs = [self._light_spec(c) for c in cells] + self._pending_sources
        try:
            states = await asyncio.to_thread(notebook.load_outputs, self.path, specs)
        except Exception:
            return
        if states is None or self.dirty or self.cells() != cells:
            return
        mounted = len(cells)
        for cell, state in zip(cells, states):
            cell.restore_run(notebook.with_run_state(self._light_spec(cell), state))
        self._pending_sources = [notebook.with_run_state(spec, state) for spec, state in zip(self._pending_sources, states[mounted:])]

    async def _mount_rest(self) -> None:
        """Mount the cells left out of compose(), a batch at a time, after the first frame."""
        while self._pending_sources:
            batch = [Cell(spec.source, kind=spec.kind, spec=spec) for spec in self._pending_sources[:MOUNT_BATCH]]
            del self._pending_sources[:MOUNT_BATCH]
            first = len(self._cells_box.children) + 1
            for n, cell in enumerate(batch, first):
                cell.set_label(n)
            with self.app.batch_update():
                await self._cells_box.mount_all(batch)
            await asyncio.sleep(0)
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
        except Exception as error:
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
            if not self.kernel.ready.is_set():
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
        self.store_outputs_now()
        if not self._shutting_down:
            self._shutting_down = True
            await self._shutdown_kernel()

    def focus_pane(self) -> None:
        """Give focus back to the cell that had it last, in the global mode (cell or editor)."""
        target = self.last_focus
        cell = target if isinstance(target, Cell) else getattr(target, "parent", None)
        if not isinstance(cell, Cell) or not cell.is_attached:
            cells = self.cells()
            if not cells:
                return
            cell = cells[0]
        self.select(cell, "auto")

    def _on_descendant_focus(self, event: events.DescendantFocus) -> None:
        widget = event.widget
        if isinstance(widget, (Cell, VimTextArea)):
            self.last_focus = widget
            if self.outline_visible:
                cell, row = self._cursor_position(widget)
                self._outline.follow(cell.cid if cell else None, row)
        self.app.pane_focused(self)
        if self.debug is not None:
            self.app.update_status()


    def set_active(self, active: bool) -> None:
        self.set_class(active, "-active")
        self._status_key = None
        self.render_status()

    def show_status(self, show: bool) -> None:
        """The status line is shown only when a tab has several windows (vim's laststatus=1)."""
        self.set_class(show, "-multi")

    def set_zoomed(self, zoomed: bool) -> None:
        """ctrl+w z: this window fills its tab; the status line says so."""
        if zoomed != self.zoomed:
            self.zoomed = zoomed
            self.render_status()

    def render_status(self) -> None:
        if self._status is None:
            return
        key = (self.title, self.kernel_label, self.kernel_state, self.has_class("-active"), self.zoomed)
        if key == self._status_key:
            return
        self._status_key = key
        active = self.has_class("-active")
        self._status.update(
            Text.assemble(
                (f" {self.title} ", "bold" if active else ""),
                (f" {self.kernel_label} · {self.kernel_state}", "" if active else "dim"),
                ("   ⤢ zoomed · ctrl+w z restores the splits" if self.zoomed else "", "bold"),
            ),
            layout=False,
        )

    def mark_dirty(self, dirty: bool = True) -> None:
        if dirty != self.dirty:
            self.dirty = dirty
            self.render_status()
            self.app.update_status()


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
        self._refresh_outline(graph)
        return graph

    @property
    def outline_visible(self) -> bool:
        return self._outline is not None and self._outline.display

    @property
    def variables(self) -> dict[str, Value]:
        """name → (type, repr) for the names the cells define, as of the last run."""
        return self._values

    def _owners(self, cells: list[Cell]) -> dict[str, Cell]:
        """name → the cell that bound it last (the highest run number among the cells defining it)."""
        owners: dict[str, Cell] = {}
        for cell in cells:
            for name in cell.last_defs:
                if name in self._values and (name not in owners or cell.exec_count > owners[name].exec_count):
                    owners[name] = cell
        return owners

    def _cell_values(self, cells: list[Cell]) -> Values:
        """The kernel's values keyed by (cell, name): a name belongs to the cell that bound it
        last, so a second cell defining the same name does not show the first one's value."""
        return {(cell.cid, name): self._values[name] for name, cell in self._owners(cells).items()}

    def _cursor_position(self, widget: Widget | None = None) -> tuple[Cell | None, int | None]:
        """The cell under the cursor and the editor's row, None when the cell itself has the focus."""
        widget = self.app.focused if widget is None else widget
        if isinstance(widget, VimTextArea) and isinstance(widget.parent, Cell):
            return widget.parent, widget.cursor_location[0]
        if isinstance(widget, Cell):
            return widget, None
        return self.current_cell(), None

    def _side_column(self):
        """The right column that holds the outline and the variables panel, built on first use."""
        if self._side is None:
            from .variables import SideColumn

            self._side = SideColumn()
            self._cells_box.parent.mount(self._side)
        return self._side

    def toggle_outline(self) -> None:
        """ctrl+o, :outline: show or hide the outline panel; it is built the first time."""
        if self._outline is None:
            self._outline = Outline()
            side = self._side_column()
            if self._variables is not None:
                side.mount(self._outline, before=self._variables)
            else:
                side.mount(self._outline)
            side.display = True
            self.call_after_refresh(self._refresh_outline)
            return
        self._outline.display = not self._outline.display
        self._side.sync()
        if self._outline.display:
            self._refresh_outline()

    @property
    def variables_visible(self) -> bool:
        return self._variables is not None and self._variables.display

    def _variables_panel(self):
        if self._variables is None:
            from .variables import VariablesPanel

            self._variables = VariablesPanel(self)
            self._variables.rows = self._variable_rows()
            side = self._side_column()
            side.mount(self._variables)
            side.display = True
        return self._variables

    def toggle_variables(self) -> None:
        """ctrl+v, :vars: show or hide the variables panel (the debugger while a cell is paused)."""
        if self._variables is None:
            self._variables_panel()
            return
        self._variables.display = not self._variables.display
        self._side.sync()
        if self._variables.display:
            if self.debug is not None:
                self._draw_debug()
            else:
                self._refresh_variables()

    def show_variables(self) -> None:
        panel = self._variables_panel()
        if not panel.display:
            panel.display = True
            self._side.sync()

    def hide_variables(self) -> None:
        if self._variables is not None:
            self._variables.display = False
            self._side.sync()
        self.focus_pane()

    def focus_variables(self) -> None:
        if self.variables_visible:
            self._variables.table.focus()

    def focus_side(self, below: bool | None = None) -> bool:
        """ctrl+w l into the right column: the debugger while paused, else the outline first;
        ctrl+w j / k move between the two when both are open."""
        shown = [w for w in (self._outline, self._variables) if w is not None and w.display]
        if not shown:
            return False
        if below is None:
            target = self._variables if self.debug is not None and self.variables_visible else shown[0]
        else:
            target = shown[-1] if below else shown[0]
        if target is self._outline:
            self.focus_outline()
        else:
            self.focus_variables()
        return True

    def _variable_rows(self) -> list[tuple]:
        owners = self._owners(self.cells())
        rows = []
        for name, (kind, shown, info, category) in sorted(self._var_rows.items()):
            cell = owners.get(name)
            rows.append((name, kind, shown, info, category, cell.label if cell is not None else ""))
        return rows

    def _refresh_variables(self) -> None:
        if self.variables_visible and self.debug is None:
            self._variables.show_vars(self._variable_rows())

    def jump_to_definition(self, name: str) -> None:
        """g in the variables panel: the editor of the cell that bound `name`, on its line."""
        from .outline import symbols

        cell = self._owners(self.cells()).get(name)
        if cell is None:
            self.notify(f"No cell defines {name}", severity="warning")
            return
        line = next((symbol.line for symbol in symbols(cell.source) if symbol.name == name), 0)
        if cell.editor_hidden:
            self.select(cell, "cell")
        else:
            self.select(cell, "normal", row=line)
            self.call_after_refresh(self._reveal_row, cell, line)

    async def delete_variable(self, name: str) -> None:
        """d in the variables panel: remove the name from the kernel; the cells that bound it become stale."""
        if self.debug is not None:
            self.notify("Not while the debugger is paused", severity="warning")
            return
        await self.kernel.delete({name})
        self._values.pop(name, None)
        self._var_rows.pop(name, None)
        for cell in self.cells():
            if name in cell.last_defs:
                cell.mark_inputs_stale()
        self._refresh_variables()
        if self.outline_visible:
            self._outline.set_values(self._cell_values(self.cells()))
        self.notify(f"Deleted {name} from the kernel")

    async def show_value(self, name: str) -> None:
        """enter in the variables panel: the whole value, fetched from the kernel on demand."""
        from .variables import ValueScreen

        if self.kernel._lock.locked():
            self.notify("The kernel is busy; the value shows once the cell finishes", severity="information")
        value = await self.kernel.full_value(name)
        self.app.push_screen(ValueScreen(name, value))

    def focus_outline(self) -> None:
        if self.outline_visible:
            self._outline.tree_view.focus()

    def _refresh_outline(self, graph: Graph | None = None) -> None:
        if not self.outline_visible:
            return
        cells = self.cells()
        current, row = self._cursor_position()
        self._outline.rebuild(cells, graph or self.graph(), self._cell_values(cells), current.cid if current else None, row)

    @on(VimTextArea.SelectionChanged)
    def _cursor_moved(self, event: VimTextArea.SelectionChanged) -> None:
        """The outline's cursor tracks the symbol under the editor's cursor, like tagbar."""
        if self.outline_visible and event.text_area is self.app.focused:
            cell, row = self._cursor_position(event.text_area)
            if cell is not None and row is not None:
                self._outline.follow(cell.cid, row)

    @on(Outline.Jump)
    def _outline_jump(self, event: Outline.Jump) -> None:
        event.stop()
        cell = next((c for c in self.cells() if c.cid == event.cid), None)
        if cell is None:
            return
        if event.line is None or cell.editor_hidden:
            self.select(cell, "cell")
        else:
            self.select(cell, "normal", row=event.line)

    @on(Outline.Closed)
    def _outline_closed(self, event: Outline.Closed) -> None:
        event.stop()
        if event.hide:
            self._outline.display = False
        self.focus_pane()

    async def run_cells(
        self, roots: list[str], downstream: bool | None = None, after_install: bool = False, debug: str | None = None,
    ) -> None:
        """Run roots in dependency order, stale ancestors first.

        `downstream` None follows the app's run mode: lazy (the default) only marks the cells
        downstream stale, reactive runs them too. `after_install` marks the re-run that follows
        a package install, so a module that still fails is not offered again at once. `debug`
        names a cell that pauses in the debugger on its first line.
        """
        await self.loaded.wait()
        async with self._run_lock:
            graph = self.refresh_info()
            by_id = {c.cid: c for c in self.cells() if c.kind == "code"}
            roots = [r for r in roots if r in by_id]
            roots = graph.expand_roots(roots + self._stale_ancestors(graph, by_id, roots))
            if downstream is None:
                downstream = not self.app.lazy
            if downstream:
                order = graph.run_order(roots)
            else:
                order = graph.sort(set(roots))
                for cid in graph.descendants(roots) - set(roots):
                    by_id[cid].mark_inputs_stale()
            for cid in order:
                by_id[cid].set_status("queued")

            failed: set[str] = set()
            missing: list[Cell] = []
            for cid in order:
                cell = by_id[cid]
                if not cell.is_mounted:
                    continue
                if cid in graph.errors:
                    cell.show_error(graph.errors[cid])
                    cell.set_status("error")
                    failed.add(cid)
                    continue
                if graph.parents[cid] & failed:
                    cell.show_error("not run: an upstream cell failed")
                    cell.inputs_stale = True
                    cell.set_status("skipped")
                    failed.add(cid)
                    continue

                source = cell.source
                cell.echo_suppressed = ends_with_string(source)
                await self.kernel.delete(cell.last_defs | graph.defs[cid])
                cell.add_output("clear_output", {"wait": True})
                self.exec_counter += 1
                cell.begin_run(self.exec_counter)
                if cid == debug:
                    await self.kernel.arm(cid)
                ok = await self.kernel.execute(
                    source, cell.add_output, cell_id=cid,
                    on_input=lambda prompt, password, cell=cell: self._input_requested(cell, prompt, password),
                )
                self._debug_end()
                if cell.output_clear_pending:
                    cell.clear_output()
                cell.ran_source = source
                cell.last_defs = graph.defs[cid]
                cell.end_run("ok" if ok and cell.source == source else "error" if not ok else "stale")
                self.outputs_changed()
                cell.missing = self._note_missing(cell) if not ok else None
                if not ok and not cell.missing:
                    self._offer_debugger(cell)
                if not ok:
                    failed.add(cid)
                if cell.missing:
                    missing.append(cell)
                self._keep_focus_visible(cell)

            await self.refresh_vars(graph)
        if missing:
            envpicker.install_missing(self, missing, after_install)

    def _note_missing(self, cell: Cell) -> str | None:
        """After a failed run: the module a ModuleNotFoundError names, with a hint under the traceback.

        A standard-library module is pointed out but never offered: pip cannot supply it.
        """
        module = packages.missing_module(cell.last_error)
        if module is None:
            return None
        env = self.kernel.env.name
        if packages.is_stdlib(module):
            cell.show_hint(f"{module} is part of Python's standard library, but the interpreter in {env} was built without it")
            return None
        cell.show_hint(f"{module} is not installed in {env} · :pip {packages.pypi_name(module, cell.source)} installs it")
        return module

    def missing_package(self) -> str:
        """The PyPI name the current cell failed to import, or '' (what :pip offers first)."""
        cell = self.current_cell()
        return packages.pypi_name(cell.missing, cell.source) if cell is not None and cell.missing else ""

    @staticmethod
    def _stale_ancestors(graph: Graph, by_id: dict[str, Cell], roots: list[str]) -> list[str]:
        """Upstream code cells that are stale: never ran, edited since, or their inputs changed.

        Like marimo, a run first brings everything it depends on up to date; clean cells do not re-run.
        """
        upstream = graph.ancestors(roots) - set(roots)
        return [cid for cid in graph.order if cid in upstream and cid in by_id and needs_run(by_id[cid])]

    def _keep_focus_visible(self, ran: Cell) -> None:
        """New output above the focused cell pushes it down; keep the user's cell on screen."""
        current = self.current_cell()
        if current is not None and current is not ran and self.app.focused is not None and current.pane is self:
            self.call_after_refresh(current.scroll_visible, animate=False, immediate=True)

    async def refresh_vars(self, graph: Graph) -> None:
        names = set().union(*graph.defs.values()) if graph.defs else set()
        rows = await self.kernel.variables(names)
        self._values = {row[0]: Value(row[1], row[2]) for row in rows}
        self._var_rows = {row[0]: list(row[1:5]) for row in rows if len(row) >= 5}
        if self.outline_visible:
            self._outline.set_values(self._cell_values(self.cells()))
        self._refresh_variables()

    def _offer_debugger(self, cell: Cell) -> None:
        """Under a traceback raised in the cell's own code: how to open the debugger where it failed."""
        error = cell.last_error or {}
        if error.get("ename") in DEBUGGABLE_ERRORS:
            cell.show_hint(":debug or r d opens the debugger where it failed: the loops, the indexes, the values")

    def _input_requested(self, cell: Cell, prompt: str, password: bool) -> None:
        """The kernel waits for input: the debugger's state (drawn in the panel) or input()."""
        from .kernel_side import MARKER

        if prompt.startswith(MARKER):
            import json

            try:
                payload = json.loads(prompt[len(MARKER):])
            except ValueError:
                payload = {}
            self._debug_stop(payload)
            return
        from .variables import InputPrompt

        screen = InputPrompt(prompt, password, cell.label)
        self._input_screen = screen

        def answered(value: str | None) -> None:
            self._input_screen = None
            if value is None:
                self.run_worker(self.kernel.interrupt(), group="kernel")
            else:
                self.kernel.reply(value)

        self.app.push_screen(screen, answered)

    def _debug_stop(self, payload: dict) -> None:
        """A stop (or the answer to a private command): draw it, highlight the line, say DEBUG."""
        from .debugview import DebugSession

        first = self.debug is None
        if first:
            self.debug = DebugSession()
        keep, self._debug_keep_view = self._debug_keep_view, False
        self.debug.update(payload, keep_view=keep and not first)
        self.show_variables()
        self._draw_debug()
        if first:
            self.focus_variables()
            self.app.update_status()
        reply = payload.get("reply") or {}
        if reply.get("kind") == "full":
            from .variables import ValueScreen

            self.app.push_screen(ValueScreen(reply.get("name", ""), reply.get("value") or {}))
        elif reply.get("kind") == "error":
            self.notify(reply.get("text", ""), severity="warning")
        if self._timeline_wanted:
            self.debug_timeline()

    def _draw_debug(self) -> None:
        session = self.debug
        if session is None:
            return
        self._variables_panel().show_debug(session)
        record = session.record
        self._highlight(record.get("cell"), record.get("line"))

    def _highlight(self, cid: str | None, line: int | None) -> None:
        """Mark the paused line in its cell's editor: one style on one line, no CSS classes."""
        target = (cid, line) if cid and line else None
        if target == self._debug_line:
            return
        if self._debug_line is not None:
            old = self._cell_by_id(self._debug_line[0])
            if old is not None:
                old.editor.set_debug_line(None)
        self._debug_line = target
        if target is None:
            return
        cell = self._cell_by_id(cid)
        if cell is not None:
            cell.editor.set_debug_line(line - 1)
            self.call_after_refresh(self._reveal_row, cell, line - 1)

    def _cell_by_id(self, cid: str | None) -> Cell | None:
        return next((c for c in self.cells() if c.cid == cid), None) if cid else None

    def place_of(self, where: dict) -> str:
        """`c3 line 5 in f()` for a frame in a cell, `file.py:12 in g()` elsewhere."""
        func = where.get("func", "")
        suffix = "" if func in ("<module>", "") else f" in {func}()"
        cell = self._cell_by_id(where.get("cell"))
        if cell is not None:
            return f"{cell.label} line {where.get('line')}{suffix}"
        name = str(where.get("file", "?")).rsplit("/", 1)[-1]
        return f"{name}:{where.get('line')}{suffix}"

    def jump_to_record(self, record: dict) -> None:
        cell = self._cell_by_id(record.get("cell"))
        if cell is None:
            self.notify("This line is not in a cell of this notebook", severity="warning")
            return
        self.select(cell, "normal", row=max(0, (record.get("line") or 1) - 1))

    def debug_command(self, command: str, keep_view: bool = False) -> None:
        """Send one pdb command (or a private _gp_* one) to the paused kernel."""
        if self.debug is None:
            return
        self._debug_keep_view = keep_view
        if not keep_view:
            self.debug.running = True
            self._draw_debug()
        self.kernel.reply(command)

    def debug_travel(self, step: int) -> None:
        """h / l: back and forward through the recorded moments; the program does not run."""
        session = self.debug
        if session is None:
            return
        if not session.total:
            self.notify("Nothing recorded yet: step with n, ], ) or b first", severity="information")
            return
        fetch = session.travel(step)
        if fetch is not None:
            self.debug_command(f"_gp_history {fetch}", keep_view=True)
            return
        self._draw_debug()

    def debug_timeline(self) -> None:
        """T: the recorded iterations as a table; missing records are fetched first."""
        from .debugview import timeline
        from .variables import TimelineScreen

        session = self.debug
        if session is None:
            return
        gap = session.missing()
        if gap is not None:
            self._timeline_wanted = True
            self.debug_command(f"_gp_history {gap}", keep_view=True)
            return
        self._timeline_wanted = False
        roots = timeline(session.records())
        if not roots:
            self.notify("No loop iterations recorded yet: step through a loop first", severity="information")
            return

        def chosen(index: int | None) -> None:
            if index is not None and self.debug is not None:
                live = self.debug.payload.get("index")
                self.debug.view = None if index == live else index
                self._draw_debug()

        self.app.push_screen(TimelineScreen(roots, session.total), chosen)

    def _debug_end(self) -> None:
        """The run is over (finished, failed, quit, interrupted or restarted): back to variables."""
        if self._input_screen is not None:
            screen, self._input_screen = self._input_screen, None
            if screen.is_attached:
                screen.dismiss(None)
        if self.debug is None:
            return
        self.debug = None
        self._timeline_wanted = False
        self._highlight(None, None)
        if self._variables is not None:
            focused = self.app.focused
            inside = focused is not None and self._variables in focused.ancestors_with_self
            self._variables.set_mode("vars")
            self._refresh_variables()
            if inside:
                self.focus_pane()
        self.app.update_status()

    async def debug_cell(self, cell: Cell | None = None) -> None:
        """r d, :debug: post-mortem where the cell's last run failed, else run it paused on its first line."""
        cell = cell or self.current_cell()
        if cell is None or cell.kind != "code":
            return
        if cell.run_status == "error" and cell.last_error is not None and cell.ran_source == cell.source:
            await self.postmortem(cell)
        else:
            await self.run_cells([cell.cid], debug=cell.cid)

    async def postmortem(self, cell: Cell) -> None:
        """Open the debugger at the frame where the cell's last run raised."""
        notes: list[str] = []

        def output(kind: str, content: dict) -> None:
            if kind == "stream":
                notes.append(content.get("text", ""))

        async with self._run_lock:
            await self.kernel.execute(
                "_gp_postmortem()", output, store_history=False,
                on_input=lambda prompt, password: self._input_requested(cell, prompt, password),
            )
            self._debug_end()
        if "".join(notes).strip():
            self.notify("".join(notes).strip(), severity="warning")

    def all_specs(self) -> list[notebook.CellSpec]:
        return [self._spec_of(c) for c in self.cells()] + self._pending_sources

    def copy_text(self, clean: bool = False) -> str:
        """The whole notebook to paste elsewhere, unsaved edits included: the file's own text, but
        a .ipynb becomes a # %% script (markdown cells as comments) instead of its JSON.
        `clean` drops every `# %%` line: code as written, markdown as comments."""
        if clean:
            return notebook.dump_clean(self.all_specs())
        if self.path is not None and self.path.suffix.lower() == ".ipynb":
            return notebook.dump(self.all_specs(), True)
        return notebook.file_text(self.path, self.all_specs(), self.markers or self.path is None)

    def write_file(self) -> None:
        notebook.save(self.path, self.all_specs(), self.markers, outputs=False)
        self.mark_dirty(False)
        self.outputs_changed()
        self.notify(f"Saved {self.path}")

    def _light_spec(self, cell: Cell) -> notebook.CellSpec:
        """The CellSpec of `cell` without its outputs: enough to write the script text."""
        if cell.spec is not None:
            return dataclasses.replace(cell.spec, source=cell.source, kind=cell.kind, hide_code=cell.code_hidden, outputs=[], execution_count=None, duration=None)
        return notebook.CellSpec(cell.source, cell.kind, hide_code=cell.code_hidden)

    def _run_state_of(self, cell: Cell) -> str:
        """The JSON of `cell`'s run state, made again only when its outputs, run number or run time changed."""
        version = (cell.kind, cell.outputs_version, cell.exec_count, cell.duration)
        cached = self._run_state_json.get(cell.cid)
        if cached is not None and cached[0] == version:
            return cached[1]
        import json

        encoded = json.dumps(notebook.run_state(self._spec_of(cell)))
        self._run_state_json[cell.cid] = (version, encoded)
        return encoded

    def _outputs_snapshot(self) -> tuple[list[notebook.CellSpec], str]:
        """The cells (no outputs) and the JSON list of their run states, as remember_outputs takes them."""
        import json

        cells = self.cells()
        states = [self._run_state_of(c) for c in cells] + [json.dumps(notebook.run_state(s)) for s in self._pending_sources]
        live = {c.cid for c in cells}
        for cid in [cid for cid in self._run_state_json if cid not in live]:
            del self._run_state_json[cid]
        return [self._light_spec(c) for c in cells] + self._pending_sources, "[" + ",".join(states) + "]"

    def _spec_of(self, cell: Cell) -> notebook.CellSpec:
        """The CellSpec to save for `cell`: its loaded spec (format extras) with the current state."""
        outputs = outputs_to_ipynb(cell.outputs_snapshot(), cell.exec_count or None) if cell.kind == "code" else []
        count = (cell.exec_count or None) if cell.kind == "code" else None
        duration = cell.duration if count else None
        if cell.spec is not None:
            return dataclasses.replace(cell.spec, source=cell.source, kind=cell.kind, hide_code=cell.code_hidden, outputs=outputs, execution_count=count, duration=duration)
        return notebook.CellSpec(cell.source, cell.kind, hide_code=cell.code_hidden, outputs=outputs, execution_count=count, duration=duration)

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

        The choice between cell mode and the editor is global, `app.cell_mode` (see
        `Cell.focus`); "auto" follows it, so navigation keeps the user's mode. A rendered
        markdown or hidden-code cell is always selected as a cell, but passing through one
        does not drop an editing user into cell mode.
        `mode` "normal"/"insert" also switches the vim mode; "keep" leaves it.
        `row` puts the cursor at the start of that line (-1 = last line).
        """
        if mode == "auto":
            mode = "cell" if self.app.cell_mode else "keep"
        else:
            self.app.cell_mode = mode == "cell"
        if mode == "cell" or cell.editor_hidden:
            cell.focus(scroll_visible=False, keep_mode=True)
        else:
            editor = cell.editor
            editor.focus(scroll_visible=False)
            if mode in ("normal", "insert") and VimTextArea.mode != mode:
                editor.set_mode(mode)
            if row is not None:
                editor.move_cursor((editor.document.line_count - 1 if row < 0 else row, 0))
        self._scroll_to_cell(cell)
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
        self.select(cells[n - 1], mode if mode in ("cell", "auto") else "keep", row)
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


    async def run_cell(self, advance: str = "stay") -> None:
        """Run the current cell. advance: "stay", "next" (Jupyter shift+enter) or "insert" (alt+enter)."""
        cell = self.current_cell()
        if cell is None:
            return
        if cell.kind == "markdown" and self.app.focused is not cell:
            cell.focus(scroll_visible=False, keep_mode=True)
        await self._run_one(cell)
        if advance == "next":
            cells = self.cells()
            if cells[-1] is cell and self._pending_sources:
                await self.loaded.wait()
                cells = self.cells()
            index = cells.index(cell) if cell in cells else len(cells) - 1
            if index + 1 < len(cells):
                self.select(cells[index + 1], "cell" if self.app.cell_mode else "normal", row=0)
                return
            advance = "insert"
        if advance == "insert":
            new = await self._insert(cell)
            self.select(new, "insert")

    async def _run_one(self, cell: Cell) -> None:
        """Run one code cell (stale ancestors first, then dependents), or render a markdown one."""
        if cell.kind == "markdown":
            await cell.show_rendered()
        else:
            self.run_worker(self.run_cells([cell.cid]), group="run")

    def cell_count(self) -> int:
        """Cells in the notebook, counting the ones not mounted yet."""
        return len(self.cells()) + len(self._pending_sources)

    async def run_number(self, n: int) -> bool:
        """Run cell c`n` (display position) without moving the focus or the selection."""
        total = self.cell_count()
        if not 1 <= n <= total:
            self.notify(f"No cell c{n} (there are {total})", severity="warning")
            return False
        await self.loaded.wait()
        cell = self.cells()[n - 1]
        if cell.kind == "markdown" and self.app.focused is cell.editor:
            cell.focus(scroll_visible=False, keep_mode=True)
        await self._run_one(cell)
        return True

    async def time_cell(self, runs: int) -> None:
        """r t [n], :timeit [n]: run the current code cell n times and print a table of its run times under it."""
        cell = self.current_cell()
        if cell is None or cell.kind != "code":
            self.notify("Only a code cell can be timed", severity="warning")
            return
        durations: list[float] = []
        for _ in range(max(1, runs)):
            await self.run_cells([cell.cid], downstream=False)
            if cell.status != "ok" or cell.duration is None:
                break
            durations.append(cell.duration)
        if not durations:
            return
        cell.add_output("stream", {"name": "stdout", "text": f"⏱ {time_summary(durations)}\n"})
        self.notify(f"{cell.label}: {time_brief(durations)}", timeout=8)

    def run_all(self) -> None:
        self.run_worker(self.run_cells([c.cid for c in self.cells()], downstream=True), group="run")

    def run_stale(self) -> None:
        stale = [c.cid for c in self.cells() if c.kind == "code" and needs_run(c)]
        if stale:
            self.run_worker(self.run_cells(stale, downstream=True), group="run")

    def run_relative(self, below: bool, inclusive: bool = False) -> None:
        """Run the cells below (r a, :runbelow) or above (r C, :runabove) the current one; `inclusive` counts it in."""
        cell = self.current_cell()
        cells = self.cells()
        if cell is None or cell not in cells:
            return
        index = cells.index(cell)
        subset = cells[index if inclusive else index + 1 :] if below else cells[: index + 1 if inclusive else index]
        if subset:
            self.run_worker(self.run_cells([c.cid for c in subset], downstream=False), group="run")

    async def add_cell(self, above: bool = False) -> None:
        cell = await self._insert(self.current_cell(), above)
        self.select(cell, "insert")

    async def delete_cell(self, count: int = 1) -> None:
        """Delete `count` cells from the current one; u in cell mode undoes."""
        cell = self.current_cell()
        if cell is None:
            return
        cells = self.cells()
        index = cells.index(cell)
        await self._remove_cells(cells[index : index + max(1, count)])

    async def _remove_cells(self, victims: list[Cell], record: bool = True) -> None:
        """Remove cells; their variables leave the kernel and the cells that used them re-run or go stale; select the next one."""
        cells = self.cells()
        index = cells.index(victims[0])
        ids = [c.cid for c in victims]
        graph = self.graph()
        downstream = graph.descendants([i for i in ids if i in graph.children]) - set(ids)
        defs = set().union(*(c.last_defs for c in victims))
        if record:
            for offset in range(len(victims) - 1, -1, -1):
                self._push_undo(("delete", self._snapshot(victims[offset], index + offset)))
        await self._cells_box.remove_children(victims)
        forget_removed(self._cells_box)
        if not self.cells():
            await self._cells_box.mount(Cell())
        remaining = self.cells()
        self.relabel()
        self.mark_dirty()
        self.select(remaining[min(index, len(remaining) - 1)], "auto")

        await self.kernel.delete(defs)
        self._rerun_or_mark_stale(downstream)

    def _rerun_or_mark_stale(self, downstream: set[str]) -> None:
        """Cells that used variables just removed from the kernel: re-run them when reactive, mark them stale when lazy."""
        if downstream and not self.app.lazy:
            self.run_worker(self.run_cells(sorted(downstream)), group="run")
            return
        graph = self.refresh_info()
        for cell in self.cells():
            if cell.cid in downstream:
                cell.mark_inputs_stale()
        self.run_worker(self.refresh_vars(graph), group="run")

    async def undo(self) -> None:
        """Undo the newest cell-level action: delete, move, insert or kind change."""
        while self._undo:
            action = self._undo.pop()
            if action[0] == "delete":
                snapshot = action[1]
                cells = self.cells()
                if snapshot.index < len(cells):
                    cell = await self._insert(cells[snapshot.index], above=True, snapshot=snapshot, record=False)
                else:
                    cell = await self._insert(cells[-1] if cells else None, snapshot=snapshot, record=False)
                self.select(cell, "auto")
                return
            cell = next((c for c in self.cells() if c.cid == action[1]), None)
            if cell is None:
                continue
            if action[0] == "move":
                self._move_to(cell, action[2])
                self.select(cell, "auto")
            elif action[0] == "insert":
                await self._remove_cells([cell], record=False)
            elif action[0] == "kind":
                await self.set_cell_kind(cell, action[2], record=False)
                self.select(cell, "auto")
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
        """yy / x in cell mode: keep the cell for p / P, and put its text on the system clipboard."""
        if cell := self.current_cell():
            self.app._cell_clipboard = self._snapshot(cell, self.cells().index(cell))
            self.app.copy_to_clipboard(cell.source)

    async def cut_cell(self) -> None:
        self.copy_cell()
        await self.delete_cell()

    async def paste_cell(self, above: bool = False) -> None:
        clip = self.app._cell_clipboard
        if clip is None:
            self.notify("No cell copied (yy or x in cell mode)")
            return
        clip = dataclasses.replace(clip, spec=None)
        cell = await self._insert(self.current_cell(), above, snapshot=clip)
        self.select(cell, "auto")

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
        if abs(step) > 1:
            index = max(0, min(len(cells) - 1, index))
        if 0 <= index < len(cells) and cells[index] is not current:
            self.select(cells[index], "auto", row=0 if step > 0 else -1)

    def focus_edge(self, last: bool) -> None:
        if last:
            self._after_loaded(lambda: self.goto_cell(len(self.cells()), "auto", row=-1))
        else:
            self.goto_cell(1, "auto")

    async def set_cell_kind(self, cell: Cell, kind: str, record: bool = True) -> None:
        """Switch a cell between code and markdown; selection stays on the cell.

        code → markdown works like deleting the code: its variables leave the kernel
        and the cells that used them re-run (and now fail, as in marimo).
        """
        if kind == cell.kind or kind not in ("code", "markdown"):
            return
        if record and kind == "markdown" and not self._confirmed_to_markdown(cell):
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
        if not defs:
            return
        await self.kernel.delete(defs)
        self._rerun_or_mark_stale(downstream)

    MARKDOWN_CONFIRM_SECONDS = 3.0

    def _confirmed_to_markdown(self, cell: Cell) -> bool:
        """False, with a warning, the first time a display-only marimo cell is turned into markdown.

        Such a cell (hidden code like `mo.vstack([mo.md(...), mo.Html(...)])`) already shows as
        text, and as markdown its code would be saved inside `mo.md(...)`. A second request on the
        same cell within MARKDOWN_CONFIRM_SECONDS goes ahead.
        """
        from .markdown import marimo_markdown

        if not cell.code_hidden or marimo_markdown(cell.source) is None:
            return True
        now = time.monotonic()
        warned, self._markdown_warned = self._markdown_warned, None
        if warned is not None and warned[0] == cell.cid and now - warned[1] <= self.MARKDOWN_CONFIRM_SECONDS:
            return True
        self._markdown_warned = (cell.cid, now)
        self.notify("This cell already renders as text; press m again to convert it to markdown anyway", severity="warning")
        return False

    def save(self, then=None) -> None:
        """Save; ask for a name when untitled. `then` runs after a successful save."""
        if self.path is None:
            from .app import Prompt

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

    async def restart_kernel(self) -> None:
        self.set_kernel_state("restarting kernel…")
        await self.kernel.restart()
        self._kernel_reset()
        self.set_kernel_state("kernel ready")

    async def restart_and_run_all(self) -> None:
        await self.restart_kernel()
        self.run_all()

    def _kernel_reset(self) -> None:
        """A fresh kernel process: every cell that ran is stale, the variables panel is empty."""
        self.tried.clear()
        for cell in self.cells():
            cell.last_defs = set()
            if cell.ran_source is not None:
                cell.mark_inputs_stale()
        self._values = {}
        self._var_rows = {}
        if self.outline_visible:
            self._outline.set_values({})
        self._refresh_variables()

    def clear_outputs(self) -> None:
        for cell in self.cells():
            cell.clear_output()
        self.outputs_changed()

    def outputs_changed(self) -> None:
        """A run finished, outputs were cleared or the file was saved: take a snapshot now (cheap,
        only changed cells are encoded) and write it to ~/.gpython from a thread half a second
        later, no :w needed. on_unmount writes a snapshot still waiting, so quitting keeps it."""
        if self.dirty or self.path is None or self.markers:
            return
        specs, states = self._outputs_snapshot()
        self._outputs_to_store = (self.path, specs, states)
        if self._outputs_timer is not None:
            self._outputs_timer.stop()
        self._outputs_timer = self.set_timer(0.5, self._store_outputs)

    async def _store_outputs(self) -> None:
        self._outputs_timer = None
        pending, self._outputs_to_store = self._outputs_to_store, None
        if pending is not None:
            path, specs, states = pending
            await asyncio.to_thread(notebook.remember_outputs, path, specs, False, states)

    def store_outputs_now(self) -> None:
        if self._outputs_timer is not None:
            self._outputs_timer.stop()
            self._outputs_timer = None
        pending, self._outputs_to_store = self._outputs_to_store, None
        if pending is not None:
            path, specs, states = pending
            notebook.remember_outputs(path, specs, False, states)

    def on_click(self, event) -> None:
        """Clicking the empty space below the last cell focuses the nearest cell."""
        if event.widget is self._cells_box and (cells := self.cells()):
            self.select(cells[-1], "cell" if cells[-1].editor_hidden else "keep")

    def jump(self, target: str, mode: str) -> None:
        """'3' focuses the third cell by display position; 'text' searches forward for text."""

        def go() -> None:
            if target.isdigit():
                self.goto_cell(int(target), mode, clamp=False)
            else:
                self.search(target, 1, mode)

        self._after_loaded(go)

    def search(self, pattern: str, step: int, mode: str) -> None:
        """Make `pattern` the search of this pane, highlight it and go `step` matches on."""
        self.search_pattern = pattern
        self.search_next(step, mode)

    def search_next(self, step: int, mode: str) -> None:
        """n / N: the `step`-th match after (or before, when negative) the cursor, wrapping.

        From an editor ("normal"/"insert" mode) the cursor lands on the match; in cell mode
        the next cell holding a match is selected. Hidden code and rendered markdown are
        searched by their source and selected as cells.
        """
        if not self.search_pattern:
            self.notify("No previous search", severity="warning")
            return
        self.search_highlight = True
        regex = search_regex(self.search_pattern)
        self.app.show_search(regex)
        self._after_loaded(lambda: self._go_to_match(regex, step, mode))

    def clear_search_highlight(self) -> bool:
        """:noh / escape in cell mode: stop highlighting (the pattern stays for n). False if it was off."""
        if not self.search_highlight:
            return False
        self.search_highlight = False
        self.app.show_search(None)
        return True

    def _go_to_match(self, regex, step: int, mode: str) -> None:
        cells = self.cells()
        if not cells:
            return
        current = self.current_cell()
        index = cells.index(current) if current in cells else 0
        offset = None
        if mode != "cell" and not cells[index].editor_hidden:
            editor = cells[index].editor
            offset = editor._offset(editor.cursor_location)
        sources = [cell.source for cell in cells]
        position = (index, offset)
        for _ in range(abs(step)):
            found = _find_match(regex, sources, *position, forward=step > 0)
            if found is None:
                self.notify(f"Not found: {self.search_pattern}", severity="warning")
                return
            position = found if offset is not None else (found[0], None)
        index, start = position
        cell = cells[index]
        if start is None or cell.editor_hidden:
            self.select(cell, "auto")
            return
        self.select(cell, "keep")
        row, column = _location_in(sources[index], start)
        cell.editor.move_cursor((row, column))
        self.call_after_refresh(self._reveal_row, cell, row)

    def _reveal_row(self, cell: Cell, row: int) -> None:
        """Scroll so line `row` of a tall cell's editor is on screen, not only the cell's top."""
        box = self._cells_box
        if box is None or not cell.is_attached:
            return
        y = cell.virtual_region.y + cell.editor.virtual_region.y + row
        box.scroll_to_region(Region(0, y, 1, 1), animate=False, immediate=True)

    async def cell_command(self, cell: Cell, key: str) -> bool:
        """One key in cell mode (the cell itself has focus). Returns False if unknown."""
        app = self.app
        if app._number_sequence is not None:
            app._number_sequence.key(key)
            return True
        if key.isdigit() and (key != "0" or self._cmd_count):
            self._cmd_count += key
            return True
        had_count = bool(self._cmd_count)
        n = int(self._cmd_count or 1)
        pending, self._cmd_pending, self._cmd_count = self._cmd_pending, "", ""
        if pending == "d":
            if key == "d":
                await self.delete_cell(n)
            return True
        if pending in (" ", " f"):
            if pending == " " and key == "f":
                self._cmd_pending = " f"
            elif pending == " " and key == "r":
                app.start_run_by_number(self)
            elif pending == " f" and key == "f":
                app.action_find_files()
            return True
        if pending == "y":
            kind_before, self._kind_before_y = self._kind_before_y, None
            if key == "y":
                if kind_before == "markdown" and cell.kind == "code":
                    await cell.set_kind("markdown")
                    if self._undo and self._undo[-1] == ("kind", cell.cid, "markdown"):
                        self._undo.pop()
                self.copy_cell()
                return True
        match key:
            case "g":
                app.start_goto_by_number(self, n if had_count else None)
            case "d" | "y" | " ":
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
                cell.start_editing()
                self.select(cell, "normal" if key == "enter" else "insert")
            case "a" | "b" | "o" | "O" | "shift+o":
                new = await self._insert(cell, above=key in ("a", "O", "shift+o"))
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
            case "r":
                app.start_run_by_number(self)
            case "m":
                await self.set_cell_kind(cell, "markdown")
            case "h":
                if cell.toggle_hidden():
                    self.mark_dirty()
            case "c":
                cell.toggle_collapsed()
            case "C" | "shift+c":
                cell.clear_output()
                self.outputs_changed()
            case "v":
                app.view_image(cell)
            case "l":
                app.set_line_numbers(not VimTextArea.line_numbers)
            case "t":
                app.set_timing(not Cell.show_timing)
            case "/":
                app._open_search()
            case "n" | "N":
                self.search_next(n if key == "n" else -n, "cell")
            case ":":
                app._open_ex()
            case "?" | "question_mark":
                app.action_help()
            case "escape":
                self.clear_search_highlight()
            case _:
                return False
        return True


    @on(VimTextArea.Changed)
    def _edited(self) -> None:
        if not self.dirty:
            self.mark_dirty()
        if self.outline_visible:
            if self._outline_timer is not None:
                self._outline_timer.stop()
            self._outline_timer = self.set_timer(0.4, self._refresh_outline)

    @on(VimTextArea.EdgeReached)
    def _edge(self, event: VimTextArea.EdgeReached) -> None:
        event.stop()
        self.focus_cell(event.step)


def _find_match(regex, sources: list[str], index: int, offset: int | None, forward: bool) -> tuple[int, int] | None:
    """(cell index, start) of the nearest match after or before cell `index`, wrapping around.

    With `offset` (a cursor in that cell) the rest of the cell is searched first;
    without it the search starts in the next cell and ends in this one.
    """
    count = len(sources)
    if offset is not None:
        if forward:
            match = regex.search(sources[index], offset + 1)
            if match is not None:
                return index, match.start()
        else:
            before = [m.start() for m in regex.finditer(sources[index]) if m.start() < offset]
            if before:
                return index, before[-1]
    for k in range(1, count + 1):
        j = (index + k) % count if forward else (index - k) % count
        if forward:
            match = regex.search(sources[j])
            if match is not None:
                return j, match.start()
        else:
            starts = [m.start() for m in regex.finditer(sources[j])]
            if starts:
                return j, starts[-1]
    return None


def _location_in(text: str, offset: int) -> tuple[int, int]:
    """(row, column) of a character offset in `text`."""
    row = text.count("\n", 0, offset)
    return row, offset - (text.rfind("\n", 0, offset) + 1)
