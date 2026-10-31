"""Rendered markdown: the "rendered" view of a markdown cell, and markdown outputs.

`MarkdownView` draws its text with Rich's markdown renderer as one widget (Textual's
`Markdown` widget builds a widget per block, which made notebooks with many markdown
cells slow to load and to move through). It keeps the source text, shows a dim
placeholder when the text is blank, and asks the app to switch to editing when it is
double-clicked. Colours come from the app theme: see `markdown_theme`. `![alt](src)` draws
the picture itself (`mdimages.py`): as a kitty placement over blank rows where the terminal
has the protocol, as colour blocks elsewhere.
"""

from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING

from textual import events
from textual.message import Message
from textual.widget import Widget

from . import images, mdimages

if TYPE_CHECKING:
    from rich.console import Console, ConsoleOptions, RenderableType, RenderResult
    from rich.segment import Segment
    from rich.theme import Theme

    from .themes import Palette

PLACEHOLDER = "*empty markdown cell – press enter to edit*"


def html_to_markdown(text):
    """Rough markdown for marimo's `mo.Html`. Self-contained: kernel.py sends its source to the kernel.

    `<pre>` becomes a fenced block (kept verbatim), `<code>`/`<strong>`/`<em>`/`<a>`/`<h1>`..`<h6>`
    their markdown forms, list items `- ` (`1. ` inside `<ol>`), numeric `<sup>` superscript
    digits; other tags are dropped and lines are unindented so indented HTML is not read as code.
    """
    import html, re

    flags = re.S | re.I
    blocks = []

    def keep(block):
        blocks.append(block)
        return "\n\n\x00%d\x00\n\n" % (len(blocks) - 1)

    def pre(m):
        body = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).replace("\xa0", " ").strip("\n")
        return keep("```\n" + body + "\n```")

    def sup(m):
        inner = m.group(1).strip()
        digits = str.maketrans("0123456789+-", "\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079\u207a\u207b")
        return inner.translate(digits) if re.fullmatch(r"[0-9+-]+", inner) else "^" + inner

    def wrap(mark):
        return lambda m: mark + m.group(3).strip() + mark if m.group(3).strip() else ""

    t = re.sub(r"<pre[^>]*>(.*?)</pre>", pre, text, flags=flags)
    t = re.sub(r"<code[^>]*>(.*?)</code>", lambda m: "`" + m.group(1) + "`", t, flags=flags)
    t = re.sub(r"<(strong|b)(\s[^>]*)?>(.*?)</\1>", wrap("**"), t, flags=flags)
    t = re.sub(r"<(em|i)(\s[^>]*)?>(.*?)</\1>", wrap("*"), t, flags=flags)
    t = re.sub(r"<sup>(.*?)</sup>", sup, t, flags=flags)
    t = re.sub(r"<a\s[^>]*href=[\"']([^\"']*)[\"'][^>]*>(.*?)</a>", r"[\2](\1)", t, flags=flags)
    t = re.sub(r"<h([1-6])[^>]*>(.*?)</h\1>", lambda m: "\n\n" + "#" * int(m.group(1)) + " " + m.group(2).strip() + "\n\n", t, flags=flags)
    t = re.sub(r"<ol[^>]*>(.*?)</ol>", lambda m: "\n\n" + re.sub(r"\s*<li[^>]*>", "\n1. ", m.group(1), flags=re.I) + "\n\n", t, flags=flags)
    t = re.sub(r"\s*<li[^>]*>", "\n- ", t, flags=re.I)
    t = re.sub(r"<br\s*/?>|</?(p|div|ul|ol)(\s[^>]*)?>", "\n\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t).replace("\xa0", " ")
    t = "\n".join(line.strip() for line in t.split("\n"))
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return re.sub("\x00(\\d+)\x00", lambda m: blocks[int(m.group(1))], t)


def marimo_markdown(source: str) -> str | None:
    """What a display-only marimo cell shows, without running it.

    The cell must be one expression built from `mo.md`, `mo.Html`, `mo.vstack`
    and `mo.hstack` over string literals (how hide_code cells like a problem
    statement are written). Anything else is None: it needs the kernel.
    """
    import ast
    import inspect

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
            return inspect.cleandoc(arg.value) if name == "md" else html_to_markdown(arg.value)
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


@cache
def _markdown_class() -> type:
    """Rich's Markdown with h1–h3 drawn big (bigtext) where the terminal can, else as Rich draws them."""
    from rich.markdown import Heading, ImageItem, Markdown, Paragraph
    from rich.text import Text

    from . import bigtext, mdimages

    class BigHeading(Heading):
        def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
            big = bigtext.heading(self.text.plain, int(self.tag[1:]), options.max_width) if bigtext.supported() else None
            if big is None:
                yield from super().__rich_console__(console, options)
            else:
                yield big

    class Picture(ImageItem):
        """`![alt](src)`: the picture, "⌛ alt" while it loads, "🌆 alt (why)" when it cannot."""

        @classmethod
        def create(cls, markdown: Markdown, token) -> Picture:
            item = cls(str(token.attrs.get("src", "")), markdown.hyperlinks)
            item.markdown = markdown
            return item

        def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
            view = self.markdown.view
            if view is None:
                yield from super().__rich_console__(console, options)
                return
            title = self.text.plain or self.destination.strip("/").rsplit("/", 1)[-1]
            found = mdimages.picture(self.destination, view)
            if found is None:
                yield Text(f"⌛ {title}", style="markdown.link")
            elif found == mdimages.NOT_A_PICTURE:
                yield Text.assemble((title, "markdown.link"), " (", (self.destination, "markdown.link_url"), ")")
            elif isinstance(found, str):
                yield Text.assemble("🌆 ", title, " ", (f"({found})", "dim"))
            else:
                yield from mdimages.render(found, self.markdown.pictures, options.max_width)

    class TextParagraph(Paragraph):
        """A paragraph that was only a picture leaves no blank line of its own."""

        def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
            if self.text.plain.strip():
                yield from super().__rich_console__(console, options)

    class BigMarkdown(Markdown):
        """Also shows links in headings as their text alone: Rich adds " (url)" after each link.

        `view` is the MarkdownView drawing this (None outside one: pictures stay Rich's "🌆 name");
        `pictures` lists (picture, cols, rows) for each block of blank rows the last render left.
        """

        elements = {**Markdown.elements, "heading_open": BigHeading, "image": Picture, "paragraph_open": TextParagraph}

        def __init__(self, *args, view: MarkdownView | None = None, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.view = view
            self.pictures: list[tuple[mdimages.Picture, int, int]] = []
            if view is not None:
                mdimages.promote_links(self.parsed)
            in_heading = False
            for token in self.parsed:
                if token.type == "heading_open":
                    in_heading = True
                elif token.type == "heading_close":
                    in_heading = False
                elif in_heading and token.children:
                    token.children = [child for child in token.children if child.type not in ("link_open", "link_close")]

        def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
            self.pictures = []
            yield from super().__rich_console__(console, options)

        def sources(self) -> list[str]:
            """The `src` of every image, in order."""
            return [str(t.attrs.get("src", "")) for t in self._flatten_tokens(self.parsed) if t.type == "image"]

    return BigMarkdown


class _LinesByWidth:
    """A renderable that lays `renderable` out once per width.

    Textual measures a height:auto widget's height by rendering it, then renders it again
    to paint: Rich markdown is the slow part of loading a notebook full of it.
    """

    def __init__(self, renderable: RenderableType, view: MarkdownView) -> None:
        self.renderable = renderable
        self.view = view
        self._lines: dict[int, tuple[list[list[Segment]], list]] = {}

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        from rich.segment import Segment

        cached = self._lines.get(options.max_width)
        if cached is None:
            lines = console.render_lines(self.renderable, options, pad=False)
            blocks = self.renderable.pictures
            placed = [(y, x, *blocks[tag]) for y, x, tag in mdimages.find_tagged(lines)] if blocks else []
            cached = self._lines[options.max_width] = (lines, placed)
        lines, placed = cached
        self.view.place_pictures(placed)
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

    def __init__(self, text: str = "", *, placeholder: str | None = PLACEHOLDER, marimo: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self._text = text
        self.placeholder = placeholder
        self.marimo = marimo
        self._display: str | None = None
        self._parsed: tuple[tuple[str, bool], _LinesByWidth] | None = None
        self._placements: list[images.InlinePlacement] = []
        self.set_class(not text.strip(), "-empty")

    @property
    def text(self) -> str:
        return self._text

    @property
    def shown(self) -> str:
        """The markdown on screen: the text, or the placeholder when it is blank.

        With `marimo=True`, text that is a marimo display expression (`mo.vstack([mo.md(...),
        mo.Html(...)])`) shows what marimo would draw for it; `text` stays the source.
        """
        if not self._text.strip() and self.placeholder:
            return self.placeholder
        if self._display is None:
            self._display = self._text
            if self.marimo and "mo." in self._text:
                self._display = marimo_markdown(self._text) or self._text
        return self._display

    def render(self) -> RenderableType:
        dark = getattr(getattr(self.app, "palette", None), "dark", True)
        key = (self.shown, dark)
        if self._parsed is None or self._parsed[0] != key:
            markdown = _markdown_class()(key[0], code_theme="ansi_dark" if dark else "ansi_light", hyperlinks=False, view=self)
            self._parsed = key, _LinesByWidth(markdown, self)
        return self._parsed[1]

    @property
    def pictures(self) -> list[bytes]:
        """The PNG of every `![alt](src)` that has loaded, in order."""
        if self._parsed is None:
            return []
        app = self.app if self.is_attached else None
        found = (mdimages.cached(src, app) for src in self._parsed[1].renderable.sources())
        return [picture.png for picture in found if picture is not None]

    def pictures_changed(self) -> None:
        """A picture arrived (or failed): lay the text out again around it."""
        self._parsed = None
        self.refresh(layout=True)

    def place_pictures(self, blocks: list[tuple[int, int, mdimages.Picture, int, int]]) -> None:
        """Keep one kitty placement per block of blank rows (`line`, `x`, picture, cols, rows)."""
        spare = self._placements
        self._placements = []
        for line, x, picture, cols, rows in blocks:
            same = next((p for p in spare if p.png is picture.png and (p.cols, p.rows) == (cols, rows)), None)
            if same is None:
                same = images.InlinePlacement(self, picture.png, picture.pixels, line, x, cols, rows, picture.frames)
            else:
                spare.remove(same)
                same.line, same.x = line, x
            self._placements.append(same)
        self._release(spare)

    def _release(self, placements: list[images.InlinePlacement]) -> None:
        data = "".join(p.release() for p in placements)
        if data:
            images._write(self.app, data)

    def on_unmount(self) -> None:
        self._release(self._placements)
        self._placements = []

    async def update(self, text: str) -> None:
        """Replace the text and re-render it."""
        self._text = text
        self._display = None
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
