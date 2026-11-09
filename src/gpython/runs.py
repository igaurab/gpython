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

    def item(self, cid: str) -> tuple[str, str, list[str]]:
        cell = self.by_id[cid]
        self.sources[cid] = cell.source
        cell.echo_suppressed = ends_with_string(cell.source)
        return cid, cell.source, sorted(cell.last_defs | self.graph.defs[cid])

    def event(self, cid: str, event: str, payload: dict) -> None:
        if event == "begin":
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
