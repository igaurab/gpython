"""Rendered markdown: the "rendered" view of a markdown cell, and markdown outputs.

`MarkdownView` draws its text with Rich's markdown renderer as one widget (Textual's
`Markdown` widget builds a widget per block, which made notebooks with many markdown
cells slow to load and to move through). It keeps the source text, shows a dim
placeholder when the text is blank, and asks the app to switch to editing when it is
double-clicked. Colours come from the app theme: see `markdown_theme`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual import events
from textual.message import Message
from textual.widget import Widget

if TYPE_CHECKING:
    from rich.console import Console, ConsoleOptions, RenderableType, RenderResult
    from rich.segment import Segment
    from rich.theme import Theme

    from .themes import Palette

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


def markdown_theme(palette: Palette) -> Theme:
    """Rich's `markdown.*` styles in the colours of `palette` (Rich's defaults are fixed colours)."""
    from rich.style import Style
    from rich.theme import Theme

    p = palette

    def c(color: str, **kw) -> Style:
        return Style(color=None if color == "default" else color, **kw)

    return Theme({
        "markdown.h1": c(p.keyword, bold=True, underline=True),
        "markdown.h1.border": c(p.comment),
        "markdown.h2": c(p.function, bold=True),
        "markdown.h3": c(p.type, bold=True),
        "markdown.h4": c(p.type, italic=True),
        "markdown.h5": Style(italic=True),
        "markdown.h6": c(p.comment),
        "markdown.code": c(p.string),
        "markdown.code_block": c(p.fg),
        "markdown.block_quote": c(p.comment, italic=True),
        "markdown.list": c(p.keyword),
        "markdown.item.bullet": c(p.keyword, bold=True),
        "markdown.item.number": c(p.keyword),
        "markdown.hr": c(p.comment),
        "markdown.link": c(p.function),
        "markdown.link_url": c(p.function, underline=True),
        "markdown.table.border": c(p.comment),
        "markdown.table.header": c(p.type, bold=True),
    })


class _LinesByWidth:
    """A renderable that lays `renderable` out once per width.

    Textual measures a height:auto widget's height by rendering it, then renders it again
    to paint: Rich markdown is the slow part of loading a notebook full of it.
    """

    def __init__(self, renderable: RenderableType) -> None:
        self.renderable = renderable
        self._lines: dict[int, list[list[Segment]]] = {}

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        from rich.segment import Segment

        lines = self._lines.get(options.max_width)
        if lines is None:
            lines = self._lines[options.max_width] = console.render_lines(self.renderable, options, pad=False)
        new_line = Segment.line()
        for line in lines:
            yield from line
            yield new_line


class MarkdownView(Widget):
    """Renders `text` as markdown. Use `await view.update(text)` to change it."""

    DEFAULT_CSS = """
    MarkdownView {
        height: auto;
        padding: 0 1;
    }
    MarkdownView.-empty {
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
        self._parsed: tuple[tuple[str, bool], _LinesByWidth] | None = None
        self.set_class(not text.strip(), "-empty")

    @property
    def text(self) -> str:
        return self._text

    @property
    def shown(self) -> str:
        """The markdown on screen: the text, or the placeholder when it is blank."""
        if not self._text.strip() and self.placeholder:
            return self.placeholder
        return self._text

    def render(self) -> RenderableType:
        dark = getattr(getattr(self.app, "palette", None), "dark", True)
        key = (self.shown, dark)
        if self._parsed is None or self._parsed[0] != key:
            from rich.markdown import Markdown

            markdown = Markdown(key[0], code_theme="ansi_dark" if dark else "ansi_light", hyperlinks=False)
            self._parsed = key, _LinesByWidth(markdown)
        return self._parsed[1]

    async def update(self, text: str) -> None:
        """Replace the text and re-render it."""
        self._text = text
        self.set_class(not text.strip(), "-empty")
        self._parsed = None
        self.refresh(layout=True)

    set_text = update

    def restyle(self) -> None:
        """Draw again in the current theme's colours (the cached layout has the old ones)."""
        self._parsed = None
        self.refresh()

    def on_click(self, event: events.Click) -> None:
        if event.chain == 2:
            event.stop()
            self.post_message(self.EditRequested(self))
