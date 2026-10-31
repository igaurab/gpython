"""The output area under each cell: streamed text, results, errors and images.

Stream output (stdout/stderr) arrives in many small chunks. Re-rendering the
whole text for every chunk is O(n^2), so chunks are collected in a list and
drawn at most 30 times a second by one timer (`_flush`).

Results and displays pick the richest MIME type we can show (`MIME_PRIORITY`):
images, markdown, JSON, HTML (as plain text) and LaTeX (as source). Images are kitty
graphics placements (`images.py`) where the terminal has them, textual_image's widget elsewhere.
"""

from __future__ import annotations

import base64
import io
import json
from html.parser import HTMLParser

from rich.json import JSON
from rich.text import Text
from textual.containers import Vertical
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Static
from textual_image.widget import Image

from . import images
from .dom import forget_removed
from .markdown import MarkdownView

MAX_STREAM_CHARS = 50_000
FLUSH_INTERVAL = 1 / 30
_KEEP_RAW = 4 * MAX_STREAM_CHARS

MIME_PRIORITY = (
    "image/png",
    "text/markdown",
    "application/json",
    "text/html",
    "text/latex",
    "text/plain",
)


def _render(kind: str, text: str) -> Text:
    if kind == "error":
        return Text(text, style="bold red")
    if kind == "note":
        return Text(text, style="dim italic")
    if kind == "hint":
        return Text(text, style="italic")
    return Text.from_ansi(text, style="red" if kind == "stderr" else "")


def collapse_carriage_returns(text: str) -> str:
    """Per line keep only the text after the last '\\r' (how a terminal shows progress bars)."""
    if "\r" not in text:
        return text
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if "\r" in line:
            line = line.rstrip("\r")
            lines[i] = line.rsplit("\r", 1)[-1]
    return "\n".join(lines)


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "head", "title"}
    _BREAK = {"br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "pre"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs) -> None:
        if tag in self._SKIP:
            self._skip += 1
        elif tag in self._BREAK:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append("\t")

    def handle_endtag(self, tag) -> None:
        if tag in self._SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in self._BREAK:
            self.parts.append("\n")

    def handle_data(self, data) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Strip tags from `html`, keeping a rough line structure."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    lines = [" ".join(line.split()) for line in "".join(parser.parts).split("\n")]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def _as_str(value) -> str:
    return "".join(value) if isinstance(value, list) else str(value)


def _tail(text: str, limit: int) -> tuple[str, int]:
    """Last `limit` characters cut at a line boundary, and how many were dropped."""
    if len(text) <= limit:
        return text, 0
    tail = text[-limit:]
    newline = tail.find("\n")
    if 0 <= newline < len(tail) - 1:
        tail = tail[newline + 1 :]
    return tail, len(text) - len(tail)


class OutputArea(Vertical):
    """A vertical list of output blocks. `text` is all text output, flushed or not."""

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("classes", "cell-output")
        super().__init__(**kwargs)
        self._blocks: list[Widget] = []
        self._stream_kind: str | None = None
        self._stream_parts: list[str] = []
        self._stream_hidden = 0
        self._stream_widget: Static | None = None
        self._flush_timer: Timer | None = None
        self._clear_pending = False


    @property
    def text(self) -> str:
        parts = []
        for block in self._blocks:
            if block is self._stream_widget:
                parts.append("".join(self._stream_parts))
            else:
                parts.append(getattr(block, "gp_text", ""))
        return "".join(parts)

    @property
    def clear_pending(self) -> bool:
        """True after clear_output(wait=True) until the next output arrives."""
        return self._clear_pending

    @property
    def has_output(self) -> bool:
        return bool(self._blocks)

    def clear(self) -> None:
        self._clear_pending = False
        self._cancel_timer()
        self._close_stream(flush=False)
        self._blocks.clear()
        self.remove_children()
        self.call_after_refresh(forget_removed, self)
        self.display = False

    def show_error(self, message: str) -> None:
        self.clear()
        self._add_text("error", message)

    def add_hint(self, text: str) -> None:
        """A line under the output about what to do next; not one of the kernel's outputs."""
        self._add_text("hint", text)

    def add(self, kind: str, content: dict) -> None:
        """Add one output, with the same (kind, content) as `Kernel.execute` callbacks."""
        if kind == "clear_output":
            if content.get("wait"):
                self._clear_pending = True
            else:
                self.clear()
            return
        if self._clear_pending:
            self.clear()
        if kind == "stream":
            self._add_stream(content["name"], content["text"])
            return
        self._close_stream()
        if kind in ("execute_result", "display_data"):
            self._add_data(content.get("data", {}))
        elif kind == "error":
            self._add_text("traceback", "\n".join(content["traceback"]))


    def _add_data(self, data: dict) -> None:
        mime = next((m for m in MIME_PRIORITY if m in data), None)
        if mime is None:
            self._add_text("note", f"[{', '.join(data)} output not supported yet]")
        elif mime == "image/png":
            self._add_image(data["image/png"])
        elif mime == "text/markdown":
            self._add_markdown(_as_str(data[mime]))
        elif mime == "application/json":
            self._add_json(data[mime])
        elif mime == "text/html":
            if "text/plain" in data:
                self._add_text("result", _as_str(data["text/plain"]))
            else:
                self._add_noted(html_to_text(_as_str(data[mime])), "[html rendered as text]")
        elif mime == "text/latex":
            self._add_noted(_as_str(data[mime]), "[latex shown as source]")
        else:
            self._add_text("result", _as_str(data["text/plain"]))

    def _add_markdown(self, text: str) -> None:
        widget = MarkdownView(text, placeholder=None, classes="output-markdown")
        widget.gp_text = text
        self._mount_block(widget)

    def _add_json(self, payload) -> None:
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                pass
        try:
            source = json.dumps(payload, indent=2, default=str)
        except (TypeError, ValueError):
            self._add_text("result", repr(payload))
            return
        widget = Static(JSON(source))
        widget.gp_text = source
        self._mount_block(widget)

    def _add_noted(self, text: str, note: str) -> None:
        """Plain text with a dim note after it (the note is not part of `text`)."""
        rendered = Text.assemble(Text(text), "\n", Text(note, style="dim italic"))
        widget = Static(rendered)
        widget.gp_text = text
        self._mount_block(widget)


    def _mount_block(self, widget: Widget) -> None:
        self.display = True
        self._blocks.append(widget)
        self.mount(widget)

    def _add_text(self, kind: str, text: str) -> None:
        self._close_stream()
        widget = Static(_render(kind, text))
        widget.gp_text = text
        self._mount_block(widget)

    def _add_image(self, b64: str) -> None:
        png = base64.b64decode(b64)
        if images.supported():
            self._mount_block(images.KittyImage(png, classes="output-image"))
            return
        from PIL import Image as PILImage

        self._mount_block(Image(PILImage.open(io.BytesIO(png)), classes="output-image"))


    def _add_stream(self, name: str, text: str) -> None:
        if self._stream_kind != name:
            self._close_stream()
            self._stream_kind = name
            self._stream_parts = []
            self._stream_hidden = 0
            self._stream_widget = Static()
            self._mount_block(self._stream_widget)
        self._stream_parts.append(text)
        if self._flush_timer is None:
            self._flush_timer = self.set_timer(FLUSH_INTERVAL, self._flush)

    def _close_stream(self, flush: bool = True) -> None:
        """Draw the open stream block now and stop appending to it."""
        if self._stream_widget is not None:
            if flush:
                self._cancel_timer()
                self._flush()
            self._stream_widget.gp_text = "".join(self._stream_parts)
        self._stream_kind = None
        self._stream_parts = []
        self._stream_widget = None

    def _cancel_timer(self) -> None:
        if self._flush_timer is not None:
            self._flush_timer.stop()
            self._flush_timer = None

    def _flush(self) -> None:
        self._flush_timer = None
        widget = self._stream_widget
        if widget is None:
            return
        raw = "".join(self._stream_parts)
        if len(raw) > _KEEP_RAW:
            raw, dropped = _tail(raw, _KEEP_RAW // 2)
            self._stream_hidden += dropped
        self._stream_parts = [raw]
        if not widget.is_attached:
            return
        shown, hidden = _tail(collapse_carriage_returns(raw), MAX_STREAM_CHARS)
        hidden += self._stream_hidden
        rendered = _render(self._stream_kind or "stdout", shown)
        if hidden:
            note = Text(f"… ({hidden} earlier characters hidden)\n", style="dim italic")
            rendered = Text.assemble(note, rendered)
        widget.update(rendered)
