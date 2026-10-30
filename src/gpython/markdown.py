"""Rendered markdown: the "rendered" view of a markdown cell, and markdown outputs.

`MarkdownView` is a thin wrapper around Textual's `Markdown` widget. It keeps the
source text, shows a dim placeholder when the text is blank, and asks the app to
switch to editing when it is double-clicked.
"""

from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Markdown

PLACEHOLDER = "*empty markdown cell – press enter to edit*"


class MarkdownView(Widget):
    """Renders `text` as markdown. Use `await view.update(text)` to change it."""

    DEFAULT_CSS = """
    MarkdownView {
        height: auto;
        padding: 0 1;
    }
    MarkdownView > Markdown {
        height: auto;
        margin: 0;
        padding: 0;
    }
    MarkdownView.-empty > Markdown {
        color: $text-muted;
    }
    """

    class EditRequested(Message):
        """Posted on double click: the user wants to edit this markdown."""

        def __init__(self, view: MarkdownView) -> None:
            super().__init__()
            self.view = view

        @property
        def control(self) -> MarkdownView:
            return self.view

    def __init__(self, text: str = "", *, placeholder: str | None = PLACEHOLDER, **kwargs) -> None:
        super().__init__(**kwargs)
        self._text = text
        self.placeholder = placeholder
        self.set_class(not text.strip(), "-empty")

    @property
    def text(self) -> str:
        return self._text

    @property
    def markdown(self) -> Markdown:
        return self.query_one(Markdown)

    def _shown(self) -> str:
        if not self._text.strip() and self.placeholder:
            return self.placeholder
        return self._text

    def compose(self) -> ComposeResult:
        yield Markdown(self._shown())

    async def update(self, text: str) -> None:
        """Replace the text and re-render it."""
        self._text = text
        self.set_class(not text.strip(), "-empty")
        if self.is_mounted:
            await self.markdown.update(self._shown())

    set_text = update

    def on_click(self, event: events.Click) -> None:
        if event.chain == 2:
            event.stop()
            self.post_message(self.EditRequested(self))
