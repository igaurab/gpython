"""The Cell widget: a header line, an editor and an output area."""

from __future__ import annotations

import ast
import itertools
import time
from dataclasses import dataclass

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.timer import Timer
from textual.widgets import Static

from . import notebook
from .graph import Graph
from .markdown import MarkdownView, marimo_markdown
from .outputs import OutputArea
from .vim import VimTextArea, _is_escape

BAR_ROLES = {"idle": "panel", "ok": "panel", "stale": "warning", "queued": "primary", "running": "primary", "error": "error", "skipped": "error"}

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


class Cell(Vertical):
    """One cell: a header line, an editor and an output area.

    The cell itself can take focus: that is "cell mode" (Jupyter's command mode),
    where keys act on whole cells (see keymap.py and NotebookPane.cell_command).

    `kind` is "code" or "markdown". A markdown cell never runs; it has two views:
    rendered (a MarkdownView, the editor hidden) and editing (the editor shown).
    """

    can_focus = True

    def __init__(self, source: str = "", kind: str = "code", spec: notebook.CellSpec | None = None) -> None:
        self.cid = f"c{next(_ids)}"
        super().__init__(id=self.cid)
        self.initial_source = source
        self.kind = kind
        self.spec = spec
        self.editing = kind == "code"
        self.code_hidden = bool(spec is not None and spec.hide_code and kind == "code")
        self.folded = False
        self.echo_suppressed = False
        if kind == "markdown":
            self.add_class("-markdown")
        self.label = self.cid
        self.ran_source: str | None = None
        self.last_defs: set[str] = set()
        self.status = "idle"
        self.info = ""
        self.exec_count = 0
        self.duration: float | None = None
        self._run_start = 0.0
        self._ticker: Timer | None = None
        self._log: list[tuple] = []
        self._log_clear_pending = False
        self._header: Static | None = None
        self._editor: VimTextArea | None = None
        self._outputs: OutputArea | None = None
        self._view: MarkdownView | None = None
        self._note: Static | None = None
        self._header_key: tuple | None = None
        self._pane = None
        self._bar_color: str | None = None
        self.selected = False
        self.focused_within = False

    def compose(self) -> ComposeResult:
        self._header = Static(classes="cell-header")
        markdown = self.kind == "markdown"
        self._editor = VimTextArea(self.initial_source, language="markdown" if markdown else "python")
        self._outputs = OutputArea()
        yield self._header
        yield self._editor
        if markdown:
            self._editor.display = False
            self._view = MarkdownView(self.initial_source)
            yield self._view
        yield self._outputs

    def on_mount(self) -> None:
        if self.code_hidden and self._editor is not None:
            self._editor.display = False
        if self.spec is not None and self.spec.outputs and self.kind == "code":
            self.replay(outputs_from_ipynb(self.spec.outputs))
            if self.spec.execution_count:
                self.exec_count = self.spec.execution_count
        elif self.code_hidden and (preview := marimo_markdown(self.source)) is not None:
            self.output_box.add("display_data", {"data": {"text/markdown": preview}})
        self._render_header()
        self.paint_bar()

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
        return self._outputs or self.query_one(OutputArea)

    @property
    def source(self) -> str:
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
        self.remove_class(f"-{self.status}", update=False)
        self.add_class(f"-{status}", update=False)
        self.status = status
        self._render_header()
        self.paint_bar()

    def paint_bar(self) -> None:
        """Colour the left bar from the status and focus: selection beats status."""
        colors = getattr(self.app, "bar_colors", None)
        if colors is None:
            return
        role = "accent" if (self.selected or self.focused_within) else BAR_ROLES.get(self.status, "panel")
        color = colors[role]
        if color != self._bar_color:
            self._bar_color = color
            self.styles.outline_left = ("thick", color)

    def set_info(self, graph: Graph) -> None:
        if self.kind == "markdown":
            if self.info:
                self.info = ""
                self._render_header()
            return
        parts = []
        if graph.defs.get(self.cid):
            parts.append("defines " + ", ".join(sorted(graph.defs[self.cid])))
        if graph.refs.get(self.cid):
            parts.append("uses " + ", ".join(sorted(graph.refs[self.cid])))
        if graph.mutates.get(self.cid):
            parts.append("mutates " + ", ".join(sorted(graph.mutates[self.cid])))
        self.info = "  ·  ".join(parts)
        self._render_header()


    @property
    def rendered(self) -> bool:
        """A markdown cell showing its rendered view (the editor is hidden)."""
        return self.kind == "markdown" and not self.editing

    @property
    def editor_hidden(self) -> bool:
        """No editor on screen: rendered markdown or a code cell with hidden code (select the cell instead)."""
        return self.rendered or (self.code_hidden and self.kind == "code")

    def set_code_hidden(self, hidden: bool) -> None:
        """marimo's hide_code: show only the header and the output; `h` in cell mode toggles."""
        if self.kind != "code" or hidden == self.code_hidden:
            return
        self.code_hidden = hidden
        if self._editor is not None:
            self._editor.display = not hidden
        self._header_key = None
        self._render_header()

    def set_folded(self, folded: bool) -> None:
        """Markdown cells: show only the header line (`h` in cell mode toggles)."""
        if self.kind != "markdown" or folded == self.folded:
            return
        self.folded = folded
        if self._view is not None:
            self._view.display = not folded and not self.editing
        self._render_header()

    def toggle_hidden(self) -> bool:
        """`h`: hide/show a code cell's code, or fold/unfold a markdown cell. True when the file changes."""
        if self.kind == "markdown":
            self.set_folded(not self.folded)
            return False
        self.set_code_hidden(not self.code_hidden)
        return True

    async def set_kind(self, kind: str) -> None:
        """Switch the widgets to `kind`. No kernel work: NotebookPane.set_cell_kind does that."""
        if kind == self.kind:
            return
        self.kind = kind
        self.folded = False
        self.set_class(kind == "markdown", "-markdown")
        self.ran_source = None
        self.last_defs = set()
        self.exec_count = 0
        self.duration = None
        self.info = ""
        self.clear_output()
        self.set_status("idle")
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
            self._view = MarkdownView(self.source)
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
        if kind == "execute_result" and self.echo_suppressed:
            return
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
                if len(parts) > 2000:
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


    def _render_header(self) -> None:
        if self._header is None:
            return
        elapsed = int(time.perf_counter() - self._run_start) if self.status == "running" else 0
        key = (self.kind, self.status, self.info, self.label, self.exec_count, self.duration, elapsed, self.selected, self.code_hidden, self.folded)
        if key == self._header_key:
            return
        self._header_key = key
        if self.kind == "markdown":
            palette = getattr(self.app, "palette", None)
            dim = palette.comment if palette is not None else "dim"
            self._header.update(self._selected_style(Text.assemble(("¶ ", dim), (self.label, "bold"), ("  MARKDOWN", dim), ("  · folded (h shows)" if self.folded else "", "dim italic"))), layout=False)
            return
        icon, style = STATUS_ICONS[self.status]
        palette = getattr(self.app, "palette", None)
        if palette is not None:
            theme = self.app.current_theme
            role = {"ok": "success", "stale": "warning", "error": "error", "skipped": "error"}.get(self.status, "primary")
            style = palette.comment if self.status == "idle" else (getattr(theme, role, None) or style)
            style = style.removeprefix("ansi_")
        parts: list[tuple[str, str]] = [(f"{icon} ", style), (self.label, "bold")]
        if self.status == "running":
            parts.append((f"  running… {elapsed}s", "cyan"))
        else:
            if self.exec_count:
                parts.append((f" [{self.exec_count}]", "dim"))
            if self.duration is not None and self.duration >= 0.1:
                parts.append((f" {self.duration:.2f}s", "dim"))
        parts.append(("  " + self.info, "dim"))
        if self.code_hidden:
            parts.append(("  · code hidden (h shows)", "dim italic"))
        self._header.update(self._selected_style(Text.assemble(*parts)), layout=False)

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
        self._render_header()
        self.paint_bar()
        self.app.update_status()

    def on_blur(self) -> None:
        self.selected = False
        self.remove_class("-selected", update=False)
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
    def _leave_editor(self, event: VimTextArea.LeaveEditor) -> None:
        event.stop()
        self.focus(scroll_visible=False)

    def on_click(self, event) -> None:
        """Clicking the header (or rendered markdown) selects the cell; anywhere else focuses its editor."""
        if event.widget is self._header or self.editor_hidden:
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
