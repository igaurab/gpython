"""The Cell widget: a header line (its top border's title), an editor and an output area."""

from __future__ import annotations

import ast
import itertools
import time
from dataclasses import dataclass

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.content import Content
from textual.geometry import Region
from textual.timer import Timer
from textual.widgets import Static

from . import notebook
from .graph import Graph
from .markdown import MarkdownView, marimo_markdown
from .outputs import OutputArea
from .vim import CELL_MODE_COLOR, DEBUG_COLOR, MODE_CURSOR_COLORS, VimTextArea, _is_escape

BAR_ROLES = {"idle": "panel", "ok": "panel", "stale": "warning", "queued": "primary", "running": "primary", "error": "error", "skipped": "error"}
CELL_MODE_BAR = CELL_MODE_COLOR
STALE_BAR = "#e8846b"
DEBUG_BAR = DEBUG_COLOR

STATUS_ICONS = {
    "idle": ("○", "dim"),
    "stale": ("◌", "yellow"),
    "queued": ("…", "cyan"),
    "running": ("●", "cyan"),
    "ok": ("✓", "green"),
    "error": ("✗", "red"),
    "skipped": ("⊘", "red"),
}

_CELL_KEYS = {"escape", "enter", "up", "down", "shift+j", "shift+k", "shift+o", "question_mark"}

_ids = itertools.count(1)


def ends_with_string(source: str) -> bool:
    """True when the last top-level statement is a bare string (a docstring-like note).

    IPython would echo such a string as the cell's result; gpython hides that echo.
    """
    try:
        body = ast.parse(source).body
    except (SyntaxError, ValueError):
        return False
    last = body[-1] if body else None
    return isinstance(last, ast.Expr) and isinstance(last.value, ast.Constant) and isinstance(last.value.value, str)


def outputs_from_ipynb(outputs: list[dict]) -> list[tuple[str, dict]]:
    """nbformat output dicts -> the (kind, content) messages the kernel would have sent."""
    messages: list[tuple[str, dict]] = []
    for out in outputs:
        kind = out.get("output_type")
        if kind == "stream":
            messages.append(("stream", {"name": out.get("name", "stdout"), "text": out.get("text", "")}))
        elif kind in ("execute_result", "display_data"):
            messages.append((kind, {"data": dict(out.get("data", {})), "metadata": dict(out.get("metadata", {}))}))
        elif kind == "error":
            messages.append(("error", {"ename": out.get("ename", ""), "evalue": out.get("evalue", ""), "traceback": list(out.get("traceback", []))}))
    return messages


def outputs_to_ipynb(snapshot: list[tuple[str, object]], execution_count: int | None) -> list[dict]:
    """The reverse of outputs_from_ipynb, from Cell.outputs_snapshot()."""
    outputs: list[dict] = []
    for kind, content in snapshot:
        if kind == "stream":
            outputs.append({"output_type": "stream", "name": content["name"], "text": content["text"]})
        elif kind == "_error":
            outputs.append({"output_type": "stream", "name": "stderr", "text": str(content)})
        elif kind == "execute_result":
            outputs.append({"output_type": kind, "data": content.get("data", {}), "metadata": content.get("metadata", {}), "execution_count": execution_count})
        elif kind == "display_data":
            outputs.append({"output_type": kind, "data": content.get("data", {}), "metadata": content.get("metadata", {})})
        elif kind == "error":
            outputs.append({"output_type": "error", "ename": content.get("ename", ""), "evalue": content.get("evalue", ""), "traceback": list(content.get("traceback", []))})
    return outputs


@dataclass
class CellSnapshot:
    """Everything needed to bring a deleted (or copied) cell back."""

    index: int
    source: str
    kind: str
    ran: bool
    outputs: list[tuple[str, object]]
    spec: notebook.CellSpec | None = None


def percentile(ordered: list[float], p: float) -> float:
    """The p-th percentile (0..100) of sorted values, interpolating between neighbours."""
    position = (len(ordered) - 1) * p / 100
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def time_stats(durations: list[float]) -> dict[str, float]:
    """mean, median, std, variance, min, p50, p90, p99 and max of several run times, in seconds."""
    import statistics

    ordered = sorted(durations)
    return {
        "mean": statistics.mean(ordered),
        "median": statistics.median(ordered),
        "std": statistics.stdev(ordered),
        "variance": statistics.variance(ordered),
        "min": ordered[0],
        "p50": percentile(ordered, 50),
        "p90": percentile(ordered, 90),
        "p99": percentile(ordered, 99),
        "max": ordered[-1],
    }


def time_summary(durations: list[float]) -> str:
    """What `r t` prints under a cell: one run's time, or a small table of statistics for several runs."""
    if len(durations) == 1:
        return f"1 run: {format_duration(durations[0])}"
    stats = time_stats(durations)
    cells = {name: format_duration(value) for name, value in stats.items()}
    cells["variance"] = f"{stats['variance']:.3g}s²" if stats["std"] >= 1 else f"{stats['variance'] * 1e6:.3g}ms²"
    headers = ["mean", "median", "std", "var", "p50", "p90", "p99", "min", "max"]
    values = [cells[name] for name in ("mean", "median", "std", "variance", "p50", "p90", "p99", "min", "max")]
    return f"{len(durations)} runs\n" + boxed_table(headers, values)


def boxed_table(headers: list[str], values: list[str]) -> str:
    """A one-row table with box-drawing borders: a header line, a rule and the values."""
    widths = [max(len(h), len(v)) for h, v in zip(headers, values)]

    def row(items: list[str]) -> str:
        return "│ " + " │ ".join(f"{item:<{w}}" for item, w in zip(items, widths)) + " │"

    def rule(left: str, mid: str, right: str) -> str:
        return left + mid.join("─" * (w + 2) for w in widths) + right

    return "\n".join([rule("┌", "┬", "┐"), row(headers), rule("├", "┼", "┤"), row(values), rule("└", "┴", "┘")])


def time_brief(durations: list[float]) -> str:
    """The toast after `r t`: runs, mean, median and p90 on one line."""
    if len(durations) == 1:
        return time_summary(durations)
    stats = time_stats(durations)
    return f"{len(durations)} runs: mean {format_duration(stats['mean'])} · median {format_duration(stats['median'])} · p90 {format_duration(stats['p90'])}"


def format_duration(seconds: float) -> str:
    """A run time as people read it: 12ms, 0.42s, 1m 05s."""
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.2f}s"
    minutes, rest = divmod(seconds, 60)
    return f"{int(minutes)}m {rest:02.0f}s"


def _state_field(name: str) -> property:
    """A widget attribute that lives on its CellState, so call sites can read and write either."""
    return property(lambda self: getattr(self.state, name), lambda self, value: setattr(self.state, name, value))


def _log_text(log: list[tuple]) -> str:
    """The text of an outputs log, as the output area would show it: streams, results, tracebacks."""
    parts: list[str] = []
    for kind, content, stream_parts in log:
        if kind == "stream":
            parts.append("".join(stream_parts))
        elif kind == "_error":
            parts.append(str(content))
        elif kind == "error":
            parts.append("\n".join(content.get("traceback", [])))
        elif kind in ("execute_result", "display_data"):
            data = content.get("data", {})
            text = data.get("text/plain") or data.get("text/markdown") or ""
            parts.append(text if isinstance(text, str) else "".join(text))
    return "".join(parts)


class CellState:
    """One cell of the notebook, on screen or not.

    The pane keeps one of these per cell from the moment the file is read. A `Cell` widget
    is its view while the cell is near the viewport (`widget`); over the mounted-cells budget
    (pane.MOUNT_BUDGET) cells far from it have no widget at all, which is what keeps a keystroke
    and a cell move at the 60-cell cost in a 1000-cell notebook. Every change to the data goes
    through here and is shown on the widget when there is one, so a run reaches cells that
    are not mounted and the result appears when they are.

    While mounted the source, cursor and undo history live in the editor; `sleep` copies them
    back before the widget goes.
    """

    def __init__(self, source: str = "", kind: str = "code", spec: notebook.CellSpec | None = None) -> None:
        self.cid = f"c{next(_ids)}"
        self.kind = kind
        self.spec = spec
        self.label = self.cid
        self._source = source
        self.cursor: tuple[int, int] = (0, 0)
        self.history = None
        self.widget: Cell | None = None
        self.editing = kind == "code"
        self.code_hidden = bool(spec is not None and spec.hide_code and kind == "code")
        self.folded = False
        self.collapsed = False
        self.echo_suppressed = False
        self.ran_source: str | None = None
        self.last_defs: set[str] = set()
        self.missing: str | None = None
        self.status = "idle"
        self.run_status = "idle"
        self.inputs_stale = False
        self.info = ""
        self.exec_count = 0
        self.duration: float | None = None
        self.outputs_version = 0
        self._run_start = 0.0
        self._log: list[tuple] = []
        self._log_clear_pending = False
        if spec is not None and kind == "code":
            if spec.execution_count:
                self.exec_count = spec.execution_count
            self.duration = spec.duration
            if spec.outputs:
                self.replay(outputs_from_ipynb(spec.outputs))

    @property
    def source(self) -> str:
        widget = self.widget
        if widget is not None and widget._editor is not None:
            return widget._editor.text
        return self._source

    @property
    def is_mounted(self) -> bool:
        return self.widget is not None

    def sleep(self) -> None:
        """Take the editor's text, cursor and undo history back; the widget is about to go."""
        widget = self.widget
        if widget is None:
            return
        editor = widget._editor
        if editor is not None:
            self._source = editor.text
            self.cursor = editor.cursor_location
            self.history = editor.history
        self.widget = None

    @property
    def rendered(self) -> bool:
        """A markdown cell showing its rendered view (the editor is hidden)."""
        return self.kind == "markdown" and not self.editing

    @property
    def editor_hidden(self) -> bool:
        """No editor on screen: rendered markdown or a code cell with hidden code (select the cell instead)."""
        return self.rendered or (self.code_hidden and self.kind == "code")

    @property
    def output_text(self) -> str:
        return _log_text(self._log)

    @property
    def images(self) -> list[bytes]:
        """The PNG bytes of the output images, decoded from the log."""
        import base64

        found = []
        for kind, content, _ in self._log:
            if kind in ("execute_result", "display_data") and "image/png" in content.get("data", {}):
                found.append(base64.b64decode(content["data"]["image/png"]))
        return found

    @property
    def output_clear_pending(self) -> bool:
        return self._log_clear_pending

    @property
    def last_error(self) -> dict | None:
        """The content of the last error the current outputs hold, if any."""
        for kind, content, _ in reversed(self._log):
            if kind == "error":
                return content
        return None

    def set_label(self, n: int) -> None:
        """The 1-based display position (c1, c2, ...) shown in the header."""
        label = f"c{n}"
        if label != self.label:
            self.label = label
            if self.widget is not None:
                self.widget._render_header()

    def set_status(self, status: str) -> None:
        if status == self.status:
            return
        old, self.status = self.status, status
        if self.widget is not None:
            self.widget._show_status(old, status)

    def set_info(self, graph: Graph) -> None:
        if self.kind == "markdown":
            info = ""
        else:
            parts = []
            if graph.defs.get(self.cid):
                parts.append("defines " + ", ".join(sorted(graph.defs[self.cid])))
            if graph.refs.get(self.cid):
                parts.append("uses " + ", ".join(sorted(graph.refs[self.cid])))
            if graph.mutates.get(self.cid):
                parts.append("mutates " + ", ".join(sorted(graph.mutates[self.cid])))
            info = "  ·  ".join(parts)
        if info != self.info:
            self.info = info
            if self.widget is not None:
                self.widget._render_header()

    def begin_run(self, count: int) -> None:
        """Mark the cell running as In [count]; a mounted header counts seconds while it runs."""
        self.exec_count = count
        self.inputs_stale = False
        self.duration = None
        self._run_start = time.perf_counter()
        self.set_status("running")
        if self.widget is not None:
            self.widget._start_ticker()

    def end_run(self, status: str, duration: float | None = None) -> None:
        """The run is over; `duration` is the kernel's own timing when it has one."""
        self.duration = time.perf_counter() - self._run_start if duration is None else duration
        self.run_status = status
        if self.widget is not None:
            self.widget._stop_ticker()
        self.set_status(status)
        if self.widget is not None:
            self.widget._render_header()

    def mark_inputs_stale(self) -> None:
        """Something it depends on changed in the kernel (an ancestor re-ran, a restart): stale until it runs."""
        self.inputs_stale = True
        self.set_status("stale")

    def clear_output(self) -> None:
        self.outputs_version += 1
        self._log.clear()
        self._log_clear_pending = False
        if self.widget is not None:
            self.widget._clear_shown()

    def show_error(self, message: str) -> None:
        self.outputs_version += 1
        self._log[:] = [("_error", message, None)]
        self._log_clear_pending = False
        if self.widget is not None:
            self.widget._show_error(message)

    def show_hint(self, text: str) -> None:
        """A line under the outputs (an install offer, say); it is not saved with them."""
        if self.widget is not None:
            self.widget._show_hint(text)

    def add_output(self, kind: str, content: dict) -> None:
        if kind == "execute_result" and self.echo_suppressed:
            return
        self.outputs_version += 1
        log = self._log
        if kind == "clear_output" and content.get("wait"):
            self._log_clear_pending = True
            if self.widget is not None:
                self.widget._show_output(kind, content)
            return
        if self._log_clear_pending:
            log.clear()
            self._log_clear_pending = False
        if kind == "stream":
            if log and log[-1][0] == "stream" and log[-1][1] == content["name"]:
                parts = log[-1][2]
                parts.append(content["text"])
                if len(parts) > 2000:
                    parts[:] = ["".join(parts)[-200_000:]]
            else:
                log.append(("stream", content["name"], [content["text"]]))
        elif kind == "clear_output":
            log.clear()
        else:
            log.append((kind, content, None))
        if self.widget is not None:
            self.widget._show_output(kind, content)

    def outputs_snapshot(self) -> list[tuple[str, object]]:
        out: list[tuple[str, object]] = []
        for kind, content, parts in self._log:
            if kind == "stream":
                out.append(("stream", {"name": content, "text": "".join(parts)}))
            else:
                out.append((kind, content))
        return out

    def restore_run(self, spec: notebook.CellSpec) -> None:
        """Take a stored run (outputs, run number, run time) on a cell that has not run since it loaded."""
        if self.kind != "code" or self.exec_count or self._log:
            return
        if spec.execution_count:
            self.exec_count = spec.execution_count
        self.duration = spec.duration
        if spec.outputs:
            self.replay(outputs_from_ipynb(spec.outputs))
        if self.widget is not None:
            self.widget._render_header()

    def replay(self, outputs: list[tuple[str, object]]) -> None:
        for kind, content in outputs:
            if kind == "_error":
                self.show_error(content)
            else:
                self.add_output(kind, content)

    def set_code_hidden(self, hidden: bool) -> None:
        """marimo's hide_code: show only the header and the output; `h` in cell mode toggles."""
        if self.kind != "code" or hidden == self.code_hidden:
            return
        self.code_hidden = hidden
        if self.widget is not None:
            self.widget._show_code_hidden()

    def set_folded(self, folded: bool) -> None:
        """Markdown cells: show only the header line (`h` in cell mode toggles)."""
        if self.kind != "markdown" or folded == self.folded:
            return
        self.folded = folded
        if self.widget is not None:
            self.widget._show_folded()

    def toggle_hidden(self) -> bool:
        """`h`: hide/show a code cell's code, or fold/unfold a markdown cell. True when the file changes."""
        if self.kind == "markdown":
            self.set_folded(not self.folded)
            return False
        self.set_code_hidden(not self.code_hidden)
        return True

    def toggle_collapsed(self) -> None:
        self.collapsed = not self.collapsed
        if self.widget is not None:
            self.widget._show_collapsed()

    def reset_kind(self, kind: str) -> None:
        """The data side of a kind change: a cell that becomes markdown (or code again) never ran."""
        self.kind = kind
        self.folded = False
        self.editing = kind == "code"
        self.ran_source = None
        self.inputs_stale = False
        self.last_defs = set()
        self.exec_count = 0
        self.duration = None
        self.info = ""
        self.clear_output()
        self.set_status("idle")


class Cell(Vertical):
    """One cell on screen: a header line, an editor and an output area, showing a CellState.

    The header is the title of the cell's own top border (`border-top: blank`), not a
    widget: every widget costs ~40 kB and ~1 ms at mount, and one widget fewer per cell
    made 30 cell moves 15 % cheaper (every move re-arranges and repaints them all).

    The cell itself can take focus: that is "cell mode" (Jupyter's command mode),
    where keys act on whole cells (see keymap.py and NotebookPane.cell_command).

    `kind` is "code" or "markdown". A markdown cell never runs; it has two views:
    rendered (a MarkdownView, the editor hidden) and editing (the editor shown).
    The data lives on `state` (see CellState); the attributes below read and write it.
    """

    can_focus = True
    show_timing = False

    kind = _state_field("kind")
    spec = _state_field("spec")
    label = _state_field("label")
    status = _state_field("status")
    run_status = _state_field("run_status")
    ran_source = _state_field("ran_source")
    last_defs = _state_field("last_defs")
    inputs_stale = _state_field("inputs_stale")
    missing = _state_field("missing")
    info = _state_field("info")
    exec_count = _state_field("exec_count")
    duration = _state_field("duration")
    outputs_version = _state_field("outputs_version")
    echo_suppressed = _state_field("echo_suppressed")
    code_hidden = _state_field("code_hidden")
    folded = _state_field("folded")
    editing = _state_field("editing")
    collapsed = _state_field("collapsed")

    def watch_has_focus(self, has_focus: bool) -> None:
        """Textual re-applies the stylesheet to a widget and all its children whenever its focus
        changes, for `:focus` rules. A cell's focus colour is painted through `outline_left` in
        `_paint_state`, no rule depends on `Cell:focus`, and the re-application walked the header,
        editor and output on every move in cell mode."""

    def __init__(self, source: str = "", kind: str = "code", spec: notebook.CellSpec | None = None, *, state: CellState | None = None) -> None:
        self.state = CellState(source, kind, spec) if state is None else state
        self.cid = self.state.cid
        super().__init__(id=self.cid)
        self.state.widget = self
        if self.state.kind == "markdown":
            self.add_class("-markdown", update=False)
        if self.state.status != "idle":
            self.add_class(f"-{self.state.status}", update=False)
        if self.state.collapsed:
            self.add_class("-collapsed", update=False)
        self._ticker: Timer | None = None
        self._editor: VimTextArea | None = None
        self._outputs: OutputArea | None = None
        self._view: MarkdownView | None = None
        self._note: Static | None = None
        self._header_key: tuple | None = None
        self._pane = None
        self._bar_color: str | None = None
        self.selected = False
        self.focused_within = False
        self.debugging = False

    def compose(self) -> ComposeResult:
        state = self.state
        markdown = state.kind == "markdown"
        self._editor = VimTextArea(state.source, language="markdown" if markdown else "python")
        if state.history is not None:
            self._editor.history = state.history
            state.history = None
        yield self._editor
        if markdown:
            self._view = MarkdownView(state.source, marimo=True)
            if state.editing:
                self._view.display = False
            else:
                self._editor.display = False
                self._view.display = not state.folded
            yield self._view

    def on_mount(self) -> None:
        state = self.state
        if state.code_hidden and self._editor is not None:
            self._editor.display = False
        if state._log:
            self._replay_shown(state.outputs_snapshot())
        elif state.code_hidden and (preview := marimo_markdown(self.source)) is not None:
            self.output_box.add("display_data", {"data": {"text/markdown": preview}})
        if state.collapsed:
            self._update_note()
        if state.status == "running":
            self._start_ticker()
        if self._editor is not None and state.cursor != (0, 0):
            self._editor.move_cursor(state.cursor)
        self._render_header()
        self.paint_bar()
        pane = self.pane
        if pane is not None:
            pane._cell_mounted(self)

    @property
    def pane(self):
        """The NotebookPane (window) this cell belongs to."""
        if self._pane is None or not self._pane.is_attached:
            from .pane import NotebookPane

            node = self.parent
            while node is not None and not isinstance(node, NotebookPane):
                node = node.parent
            self._pane = node
        return self._pane

    @property
    def editor(self) -> VimTextArea:
        return self._editor or self.query_one(VimTextArea)

    @property
    def output_box(self) -> OutputArea:
        """The output area, mounted on the first output: most cells never need one, and
        each widget costs about a millisecond of CSS and layout at start-up."""
        if self._outputs is None:
            self._outputs = OutputArea()
            self.mount(self._outputs, after=self._view or self._editor)
        return self._outputs

    @property
    def output_clear_pending(self) -> bool:
        return self.state.output_clear_pending

    @property
    def source(self) -> str:
        return self.state.source

    @property
    def output_text(self) -> str:
        return self._outputs.text if self._outputs is not None else ""

    @property
    def images(self) -> list[bytes]:
        """The cell's pictures: those in its markdown, then its output images."""
        shown = self._view.pictures if self._view is not None else []
        return shown + (self._outputs.images if self._outputs is not None else [])

    @property
    def last_error(self) -> dict | None:
        return self.state.last_error

    @property
    def rendered(self) -> bool:
        return self.state.rendered

    @property
    def editor_hidden(self) -> bool:
        return self.state.editor_hidden

    def set_label(self, n: int) -> None:
        self.state.set_label(n)

    def set_status(self, status: str) -> None:
        self.state.set_status(status)

    def set_info(self, graph: Graph) -> None:
        self.state.set_info(graph)

    def begin_run(self, count: int) -> None:
        self.state.begin_run(count)

    def end_run(self, status: str, duration: float | None = None) -> None:
        self.state.end_run(status, duration)

    def mark_inputs_stale(self) -> None:
        self.state.mark_inputs_stale()

    def clear_output(self) -> None:
        self.state.clear_output()

    def show_error(self, message: str) -> None:
        self.state.show_error(message)

    def show_hint(self, text: str) -> None:
        self.state.show_hint(text)

    def add_output(self, kind: str, content: dict) -> None:
        self.state.add_output(kind, content)

    def outputs_snapshot(self) -> list[tuple[str, object]]:
        return self.state.outputs_snapshot()

    def restore_run(self, spec: notebook.CellSpec) -> None:
        self.state.restore_run(spec)

    def replay(self, outputs: list[tuple[str, object]]) -> None:
        self.state.replay(outputs)

    def set_code_hidden(self, hidden: bool) -> None:
        self.state.set_code_hidden(hidden)

    def set_folded(self, folded: bool) -> None:
        self.state.set_folded(folded)

    def toggle_hidden(self) -> bool:
        return self.state.toggle_hidden()

    def toggle_collapsed(self) -> None:
        self.state.toggle_collapsed()

    def set_debugging(self, paused_here: bool) -> None:
        """The debugger is paused in this cell (or no longer): its bar turns DEBUG_BAR red."""
        if paused_here != self.debugging:
            self.debugging = paused_here
            self.paint_bar()


    def _show_status(self, old: str, new: str) -> None:
        self.remove_class(f"-{old}", update=False)
        self.add_class(f"-{new}", update=False)
        self._render_header()
        self.paint_bar()

    def _start_ticker(self) -> None:
        if self._ticker is None:
            self._ticker = self.set_interval(1, self._render_header)

    def _stop_ticker(self) -> None:
        if self._ticker is not None:
            self._ticker.stop()
            self._ticker = None

    def _clear_shown(self) -> None:
        if self._outputs is not None:
            self._outputs.clear()
        self._update_note()

    def _show_error(self, message: str) -> None:
        self.output_box.show_error(message)
        self._update_note()

    def _show_hint(self, text: str) -> None:
        self.output_box.add_hint(text)
        if self._note is not None:
            self._update_note()

    def _show_output(self, kind: str, content: dict) -> None:
        if kind == "clear_output":
            if self._outputs is not None:
                self._outputs.add(kind, content)
            return
        self.output_box.add(kind, content)
        if self._note is not None:
            self._update_note()

    def _replay_shown(self, outputs: list[tuple[str, object]]) -> None:
        """Paint a state's outputs into a fresh output area (a remount, or stored outputs)."""
        for kind, content in outputs:
            if kind == "_error":
                self.output_box.show_error(content)
            else:
                self.output_box.add(kind, content)
        if self._note is not None:
            self._update_note()

    def _show_code_hidden(self) -> None:
        if self._editor is not None:
            self._editor.display = not self.state.code_hidden
        self._header_key = None
        self._render_header()

    def _show_folded(self) -> None:
        if self._view is not None:
            self._view.display = not self.state.folded and not self.state.editing
        self._render_header()

    def _show_collapsed(self) -> None:
        self.set_class(self.state.collapsed, "-collapsed")
        self._update_note()


    def paint_bar(self) -> None:
        """Colour the left bar from the status and focus: selection beats status; a cell selected
        in cell mode is CELL_MODE_BAR, the blue of the CELL badge; the cell being edited takes
        the colour of the vim mode, like the mode's badge and cursor (MODE_CURSOR_COLORS); a
        stale cell, one that needs a rerun, is STALE_BAR, salmon, apart from the yellow of
        normal mode. The cell the debugger is paused in is DEBUG_BAR, the red of the DEBUG
        badge, above everything else."""
        colors = getattr(self.app, "bar_colors", None)
        if colors is None:
            return
        status = self.state.status
        role = "accent" if (self.selected or self.focused_within) else BAR_ROLES.get(status, "panel")
        if self.debugging:
            color = DEBUG_BAR
        elif self.focused_within:
            editor = self._editor
            mode = VimTextArea.mode if VimTextArea.enabled else "insert"
            if editor is not None and editor.visual:
                mode = "visual"
            color = MODE_CURSOR_COLORS.get(mode, MODE_CURSOR_COLORS["normal"])
        elif self.selected:
            color = CELL_MODE_BAR
        elif status == "stale":
            color = STALE_BAR
        else:
            color = colors[role]
        if color != self._bar_color:
            self._bar_color = color
            self.styles.outline_left = ("thick", color)

    async def set_kind(self, kind: str) -> None:
        """Switch the widgets to `kind`. No kernel work: NotebookPane.set_cell_kind does that."""
        if kind == self.kind:
            return
        self.state.reset_kind(kind)
        self.set_class(kind == "markdown", "-markdown")
        self._header_key = None
        self._render_header()
        if self._editor is None:
            return
        self._editor.language = "markdown" if kind == "markdown" else "python"
        if kind == "code":
            self.start_editing()
        elif self.is_mounted and self.screen.focused is self._editor:
            self.editing = True
        else:
            await self.show_rendered()

    def start_editing(self) -> None:
        """Show the editor (markdown: hide the rendered view; hidden code: show it). The caller focuses it."""
        self.editing = True
        self.code_hidden = False
        self.folded = False
        if self._editor is not None:
            self._editor.display = True
        if self._view is not None:
            self._view.display = False
        self._render_header()

    async def show_rendered(self) -> None:
        """Markdown cells: render the editor's text and show it instead of the editor."""
        if self.kind != "markdown" or self._editor is None:
            return
        self.editing = False
        if self._view is None:
            self._view = MarkdownView(self.source, marimo=True)
            self._view.display = False
            await self.mount(self._view, after=self._editor)
        elif self._view.text != self.source:
            await self._view.update(self.source)
        if not self.editing:
            self._editor.display = False
            self._view.display = not self.folded

    async def _render_if_left(self) -> None:
        if self.kind == "markdown" and self.editing and self.is_mounted and self.screen.focused is not self._editor:
            await self.show_rendered()

    @on(MarkdownView.EditRequested)
    def _edit_requested(self, event: MarkdownView.EditRequested) -> None:
        event.stop()
        self.start_editing()
        self.pane.select(self, "normal")

    def _update_note(self) -> None:
        """'… N more lines' under a collapsed output."""
        text = self.output_text
        hidden = (text.rstrip("\n").count("\n") + 1 - 3) if text else 0
        if self.state.collapsed and hidden > 0:
            if self._note is None:
                self._note = Static(classes="collapsed-note")
                self.mount(self._note, after=self.output_box)
            self._note.update(f"… {hidden} more lines")
        elif self._note is not None:
            self._note.remove()
            self._note = None


    def _render_header(self) -> None:
        state = self.state
        elapsed = int(time.perf_counter() - state._run_start) if state.status == "running" else 0
        key = (state.kind, state.status, state.info, state.label, state.exec_count, state.duration, elapsed, self.selected, state.code_hidden, state.folded, self.show_timing)
        if key == self._header_key:
            return
        self._header_key = key
        if state.kind == "markdown":
            palette = getattr(self.app, "palette", None)
            dim = palette.comment if palette is not None else "dim"
            self._set_header(self._selected_style(Text.assemble(("¶ ", dim), (state.label, "bold"), ("  MARKDOWN", dim), ("  · folded (h shows)" if state.folded else "", "dim italic"))))
            return
        icon, style = STATUS_ICONS[state.status]
        palette = getattr(self.app, "palette", None)
        if palette is not None:
            theme = self.app.current_theme
            role = {"ok": "success", "stale": "warning", "error": "error", "skipped": "error"}.get(state.status, "primary")
            style = palette.comment if state.status == "idle" else (getattr(theme, role, None) or style)
            style = style.removeprefix("ansi_")
        parts: list[tuple[str, str]] = [(f"{icon} ", style), (state.label, "bold")]
        if state.status == "running":
            parts.append((f"  running… {elapsed}s", "cyan"))
        else:
            if state.exec_count:
                parts.append((f" [{state.exec_count}]", "dim"))
            if self.show_timing and state.duration is not None:
                parts.append((f" {format_duration(state.duration)}", "dim"))
        parts.append(("  " + state.info, "dim"))
        if state.code_hidden:
            parts.append(("  · code hidden (h shows)", "dim italic"))
        self._set_header(self._selected_style(Text.assemble(*parts)))

    def refresh_header(self) -> None:
        self._render_header()

    @property
    def header_text(self) -> str:
        """The header line as plain text (what the top border shows)."""
        return self._border_title.plain if self._border_title is not None else ""

    def _set_header(self, text: Text) -> None:
        """Assigning `border_title` repaints the whole cell; only its top row changed.

        Regions given to `refresh` are relative to the content area, one row and one
        column in from the border row that holds the title.
        """
        self._border_title = Content.from_rich_text(Text.assemble(" ", text))
        self.refresh(Region(-1, -1, self.outer_size.width, 1))

    def _selected_style(self, text: Text) -> Text:
        if self.selected:
            text.stylize("reverse")
        return text


    def _on_descendant_focus(self, event: events.DescendantFocus) -> None:
        self.focused_within = True
        self.add_class("-focused", update=False)
        self.paint_bar()

    def _on_descendant_blur(self, event: events.DescendantBlur) -> None:
        self.focused_within = False
        self.remove_class("-focused", update=False)
        self.paint_bar()
        if self.kind == "markdown" and self.editing and event.widget is self._editor:
            self.call_after_refresh(self._render_if_left)

    def on_focus(self) -> None:
        self.selected = True
        self.add_class("-selected", update=False)
        if self._editor is not None and not self.editor_hidden:
            self._editor.show_cell_cursor(True)
        self._render_header()
        self.paint_bar()
        self.app.update_status()

    def on_blur(self) -> None:
        self.selected = False
        self.remove_class("-selected", update=False)
        if self._editor is not None:
            self._editor.show_cell_cursor(False)
        self._render_header()
        self.paint_bar()
        if not isinstance(self.app.focused, Cell):
            self.app.update_status()

    def on_key(self, event: events.Key) -> None:
        if self.screen.focused is not self:
            return
        key = event.character if event.is_printable and event.character else event.key
        if _is_escape(event):
            key = "escape"
        if event.is_printable or key in _CELL_KEYS:
            event.stop()
            event.prevent_default()
            pane = self.pane
            if pane is not None:
                self.app.call_later(pane.cell_command, self, key)

    @on(VimTextArea.LeaveEditor)
    def focus(self, scroll_visible: bool = True, *, keep_mode: bool = False):
        """Selecting a cell puts the whole notebook in cell mode (`app.cell_mode`), the way
        focusing an editor puts it in edit mode; `NotebookPane.select` passes `keep_mode`
        when it only follows the mode the user is already in. Textual's own focus moves (a
        removed cell, a hidden tab) go through `Screen.set_focus` and change nothing."""
        if not keep_mode:
            self.app.cell_mode = True
        return super().focus(scroll_visible)

    def _leave_editor(self, event: VimTextArea.LeaveEditor) -> None:
        event.stop()
        self.focus(scroll_visible=False)

    def on_click(self, event) -> None:
        """Clicking the header (or rendered markdown) selects the cell; anywhere else focuses its editor."""
        if event.widget is self or self.editor_hidden:
            self.focus(scroll_visible=False)
        else:
            self.app.cell_mode = False
            if not isinstance(event.widget, VimTextArea):
                self.editor.focus()

    @on(VimTextArea.Changed)
    def _mark_stale(self) -> None:
        if self.ran_source is None or self.status == "running":
            return
        document = self.editor.document
        same = document.line_count == self.ran_source.count("\n") + 1 and document.text == self.ran_source
        self.set_status(self.run_status if same and not self.inputs_stale else "stale")
