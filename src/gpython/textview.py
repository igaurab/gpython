"""A scrollable text page over the notebook: K's help, :g's matching lines, :digraphs.
j / k / ctrl+d / ctrl+u / g / G scroll, q or escape closes."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label, Static


class TextView(ModalScreen[None]):
    DEFAULT_CSS = """
    TextView {
        align: center middle;
    }
    TextView > Vertical {
        width: 90%;
        height: 80%;
        border: round $accent;
        background: $surface;
        padding: 0 1;
    }
    TextView Label {
        text-style: bold;
        width: 100%;
    }
    """

    BINDINGS = [
        Binding("escape,q", "dismiss(None)", show=False),
        Binding("j,down", "scroll(1)", show=False),
        Binding("k,up", "scroll(-1)", show=False),
        Binding("ctrl+d,space", "page(1)", show=False),
        Binding("ctrl+u", "page(-1)", show=False),
        Binding("g", "edge(False)", show=False),
        Binding("G,shift+g", "edge(True)", show=False),
    ]

    def __init__(self, title: str, text: str, ansi: bool = False) -> None:
        super().__init__()
        self.title_text = title
        self.body = Text.from_ansi(text) if ansi else Text(text)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self.title_text + "   (q closes)")
            with VerticalScroll():
                yield Static(self.body)

    def _scroller(self) -> VerticalScroll:
        return self.query_one(VerticalScroll)

    def action_scroll(self, rows: int) -> None:
        self._scroller().scroll_relative(y=rows, animate=False)

    def action_page(self, direction: int) -> None:
        box = self._scroller()
        box.scroll_relative(y=direction * max(1, box.size.height // 2), animate=False)

    def action_edge(self, end: bool) -> None:
        box = self._scroller()
        if end:
            box.scroll_end(animate=False)
        else:
            box.scroll_home(animate=False)
