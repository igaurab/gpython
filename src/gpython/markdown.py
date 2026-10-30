"""Rendered markdown: the "rendered" view of a markdown cell, and markdown outputs.

`MarkdownView` is a thin wrapper around Textual's `Markdown` widget. It keeps the
source text, shows a dim placeholder when the text is blank, and asks the app to
switch to editing when it is double-clicked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual import events
from textual.app import ComposeResult
from textual.message import Message
from textual.widget import Widget

if TYPE_CHECKING:
    from textual.widgets import Markdown

PLACEHOLDER = "*empty markdown cell – press enter to edit*"


def html_to_markdown(text):
    """Rough markdown for marimo's `mo.Html`. Self-contained: kernel.py sends its source to the kernel."""
    import html, re

    t = re.sub(r"<pre[^>]*>(.*?)</pre>", lambda m: "\n```\n" + re.sub(r"<[^>]+>", "", m.group(1)).strip("\n") + "\n```\n", text, flags=re.S | re.I)
    t = re.sub(r"<code[^>]*>(.*?)</code>", r"`\1`", t, flags=re.S | re.I)
    t = re.sub(r"<(strong|b)(\s[^>]*)?>(.*?)</\1>", r"**\3**", t, flags=re.S | re.I)
    t = re.sub(r"<(em|i)(\s[^>]*)?>(.*?)</\1>", r"*\3*", t, flags=re.S | re.I)
    t = re.sub(r"<sup>(.*?)</sup>", r"^\1", t, flags=re.S | re.I)
    t = re.sub(r"\s*<li[^>]*>", "\n- ", t, flags=re.I)
    t = re.sub(r"<br\s*/?>|</?p[^>]*>", "\n\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t).replace("\xa0", " ")
    t = re.sub(r"[ \t]+\n", "\n", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def marimo_markdown(source: str) -> str | None:
    """What a display-only marimo cell shows, without running it.

    The cell must be one expression built from `mo.md`, `mo.Html`, `mo.vstack`
    and `mo.hstack` over string literals (how hide_code cells like a problem
    statement are written). Anything else is None: it needs the kernel.
    """
    import ast

    try:
        body = ast.parse(source).body
    except SyntaxError:
        return None
    if len(body) != 1 or not isinstance(body[0], ast.Expr):
        return None

    def md(node: ast.expr) -> str:
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "mo"
            and len(node.args) == 1
        ):
            raise ValueError
        name, arg = node.func.attr, node.args[0]
        if name in ("md", "Html") and not node.keywords and isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value if name == "md" else html_to_markdown(arg.value)
        if name in ("vstack", "hstack") and isinstance(arg, (ast.List, ast.Tuple)):
            return ("\n\n" if name == "vstack" else "  ").join(md(item) for item in arg.elts)
        raise ValueError

    try:
        return md(body[0].value)
    except ValueError:
        return None


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
        from textual.widgets import Markdown

        return self.query_one(Markdown)

    def _shown(self) -> str:
        if not self._text.strip() and self.placeholder:
            return self.placeholder
        return self._text

    def compose(self) -> ComposeResult:
        from textual.widgets import Markdown

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
