"""One run of cells through the kernel, a batch per request.

`NotebookPane.run_cells` works out the order and sends the cells in batches with
`Kernel.run_batch`; a BatchRun keeps what the pane needs between the kernel's messages:
the source each cell was sent with, which cells began and ended, which failed, and the
cells whose run failed on a missing module.
"""

from __future__ import annotations

from .cell import CellState, ends_with_string
from .graph import Graph


class BatchRun:
    def __init__(self, pane, graph: Graph, by_id: dict[str, CellState], debug: str | None) -> None:
        self.pane = pane
        self.graph = graph
        self.by_id = by_id
        self.debug = debug
        self.sources: dict[str, str] = {}
        self.began: dict[str, float] = {}
        self.ended: set[str] = set()
        self.failed: set[str] = set()
        self.missing: list[CellState] = []
        self.access: dict = {}

    def next_batch(self, pending: list[str]) -> tuple[list[str], list[str]]:
        """Split `pending` into the cells to send now and the ones for later.

        Cells with a syntax error or a failed parent are marked and dropped. The cell to
        debug goes alone: the kernel pauses at the first cell run after it is armed."""
        batch: list[str] = []
        for index, cid in enumerate(pending):
            cell = self.by_id[cid]
            if cid in self.graph.errors:
                cell.show_error(self.graph.errors[cid])
                cell.set_status("error")
                self.failed.add(cid)
                continue
            if self.graph.parents[cid] & self.failed:
                cell.show_error("not run: an upstream cell failed")
                cell.inputs_stale = True
                cell.set_status("skipped")
                self.failed.add(cid)
                continue
            if cid == self.debug and batch:
                return batch, pending[index:]
            batch.append(cid)
            if cid == self.debug:
                return batch, pending[index + 1 :]
        return batch, []

    def items(self, batch: list[str]) -> list[list[tuple[str, str, list[str]]]]:
        """What the kernel runs: groups of (cell id, source, names to drop first), one cell each."""
        return [[self.item(cid)] for cid in batch]

    def groups(self, batch: list[str]) -> tuple[list[list[tuple[str, str, list[str]]]], list[list[str]] | None]:
        """Groups for the kernel, and per group the names to check after it (None: no check).

        Sequential unless the app is concurrent; then concurrency.py splits the batch. The cell to
        debug and cells that raced before run alone."""
        app = self.pane.app
        if not app.concurrent or len(batch) < 2:
            return self.items(batch), None
        from .concurrency import Access, accesses, default_cap, groups, watched

        self.access = accesses({s.cid: s.source for s in self.pane.states if s.kind == "code"})
        for cid in {self.debug, *self.pane.raced} & self.access.keys():
            self.access[cid] = Access(self.access[cid].reads, self.access[cid].writes, "runs alone")
        found = groups(batch, self.access, self.graph.parents, app.concurrency or default_cap())
        self.pane.concurrent_run = any(len(g) > 1 for g in found)
        watches = [watched(g, self.access) if len(g) > 1 else [] for g in found] if app.concurrent_verify else None
        return [[self.item(cid) for cid in group] for group in found], watches

    def race(self, names: list[str], cells: list[str]) -> None:
        """Watched names changed while a group ran: a write the analysis missed. The cells of that
        group that read them are stale and run alone from now on."""
        hit = [cid for cid in cells if cid in self.by_id and self.access.get(cid) and set(names) & self.access[cid].reads]
        for cid in hit:
            self.by_id[cid].mark_inputs_stale()
        self.pane.raced.update(cells)
        labels = ", ".join(self.by_id[cid].label for cid in hit) or "no cell"
        self.pane.app.notify(
            f"{', '.join(names)} changed while cells ran at the same time; {labels} marked stale. These cells now run one by one.",
            title="Concurrent cells", severity="warning",
        )

    def item(self, cid: str) -> tuple[str, str, list[str]]:
        cell = self.by_id[cid]
        self.sources[cid] = cell.source
        cell.echo_suppressed = ends_with_string(cell.source)
        return cid, cell.source, sorted(cell.last_defs | self.graph.defs[cid])

    def event(self, cid: str, event: str, payload: dict) -> None:
        if event == "race":
            self.race(payload.get("names", []), payload.get("cells", []))
        elif event == "begin":
            self.begin(cid, payload["t"])
        else:
            self.finish(cid, payload.get("ok", False), payload.get("t"))

    def output(self, cid: str, kind: str, content: dict) -> None:
        self.by_id[cid].add_output(kind, content)

    def input(self, cid: str, prompt: str, password: bool) -> None:
        self.pane._input_requested(self.by_id[cid], prompt, password)

    def begin(self, cid: str, at: float) -> None:
        cell = self.by_id[cid]
        cell.add_output("clear_output", {"wait": True})
        self.pane.exec_counter += 1
        cell.begin_run(self.pane.exec_counter)
        self.began[cid] = at

    def finish(self, cid: str, ok: bool, at: float | None = None) -> None:
        if cid in self.ended:
            return
        self.ended.add(cid)
        cell, pane, source = self.by_id[cid], self.pane, self.sources[cid]
        pane._debug_end()
        if cell.output_clear_pending:
            cell.clear_output()
        cell.ran_source = source
        cell.last_defs = self.graph.defs[cid]
        duration = at - self.began[cid] if at is not None and cid in self.began else None
        cell.end_run("ok" if ok and cell.source == source else "error" if not ok else "stale", duration)
        cell.missing = pane._note_missing(cell) if not ok else None
        if not ok and not cell.missing:
            pane._offer_debugger(cell)
        if not ok:
            self.failed.add(cid)
        if cell.missing:
            self.missing.append(cell)
        pane._keep_focus_visible(cell)

    def abandon(self, cids: list[str], reason: str) -> None:
        """Cells the kernel never ran: it died, or stopped answering."""
        for cid in cids:
            cell = self.by_id[cid]
            cell.show_error(reason)
            cell.set_status("error")
            self.failed.add(cid)
