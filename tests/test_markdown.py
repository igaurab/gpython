"""MarkdownView on its own."""

from __future__ import annotations

import asyncio
import functools

from textual.app import App, ComposeResult
from textual.geometry import Region

from gpython.markdown import PLACEHOLDER, MarkdownView


def sync(fn):
    """Run an async test with asyncio.run (no pytest async plugin is installed)."""

    @functools.wraps(fn)
    def wrapper():
        asyncio.run(fn())

    return wrapper


class MdApp(App):
    def __init__(self, text: str = "") -> None:
        super().__init__()
        self.text = text
        self.requests: list[MarkdownView] = []

    def compose(self) -> ComposeResult:
        yield MarkdownView(self.text)

    def on_markdown_view_edit_requested(self, message: MarkdownView.EditRequested) -> None:
        self.requests.append(message.view)


def headings_text(view: MarkdownView) -> str:
    """The text the view draws on screen."""
    return " ".join(strip.text for strip in view.render_lines(Region(0, 0, view.size.width, view.size.height)))


@sync
async def test_renders_heading():
    app = MdApp("### Hello")
    async with app.run_test() as pilot:
        await pilot.pause()
        view = app.query_one(MarkdownView)
        assert view.shown == "### Hello"
        assert "Hello" in headings_text(view)
        assert not view.has_class("-empty")


@sync
async def test_blank_shows_placeholder():
    app = MdApp("   \n")
    async with app.run_test() as pilot:
        await pilot.pause()
        view = app.query_one(MarkdownView)
        assert view.shown == PLACEHOLDER
        assert "press enter to edit" in headings_text(view)
        assert view.has_class("-empty")
        assert view.text == "   \n"


@sync
async def test_update_roundtrips_text():
    app = MdApp("")
    async with app.run_test() as pilot:
        view = app.query_one(MarkdownView)
        await view.update("# Title\n\nbody")
        await pilot.pause()
        assert view.text == "# Title\n\nbody"
        assert view.shown == "# Title\n\nbody"
        assert "Title" in headings_text(view)
        await view.update("")
        await pilot.pause()
        assert view.text == "" and view.shown == PLACEHOLDER


@sync
async def test_double_click_requests_edit():
    app = MdApp("# Hi")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click(MarkdownView, times=2)
        await pilot.pause()
        assert app.requests == [app.query_one(MarkdownView)]


def test_marimo_markdown_reads_display_only_cells():
    from gpython.markdown import marimo_markdown

    source = "mo.vstack([ mo.md('## 15. 3Sum\\n\\n*Two Pointers*'), mo.Html('<p>Given <code>nums</code></p><pre>Input: [1]\\nOutput: []</pre>'), ])"
    assert marimo_markdown(source) == "## 15. 3Sum\n\n*Two Pointers*\n\nGiven `nums`\n\n```\nInput: [1]\nOutput: []\n```"
    assert marimo_markdown("mo.hstack([mo.md('a'), mo.md('b')])") == "a  b"
    for code in ("mo.md(f'{x}')", "mo.md(text)", "x = mo.md('a')", "mo.md('a')\nprint(1)", "mo.ui.slider(1, 2)", "mo.md("):
        assert marimo_markdown(code) is None


def test_html_to_markdown_handles_leetcode_html():
    from gpython.markdown import html_to_markdown

    source = (
        '<p>Given <code>nums</code>, return <strong> all </strong> <em>triplets</em>.</p>\n\n<p>&nbsp;</p>\n'
        '<p><strong class="example">Example 1:</strong></p>\n\n<pre>\n<strong>Input:</strong> nums = [-1,0]\n'
        "<strong>Output:</strong> []\n    indented &lt;kept&gt;\n</pre>\n\n<ul>\n\t<li><code>3 &lt;= n</code></li>\n"
        "\t<li><code>-10<sup>5</sup> &lt;= x</code> and 2<sup>n</sup></li>\n</ul>\n"
        '<ol><li>first</li><li>second</li></ol>\n<div>\n    <p>See <a href="https://x.org/a">docs</a></p>\n</div><h3>Note</h3>'
    )
    assert html_to_markdown(source) == (
        "Given `nums`, return **all** *triplets*.\n\n**Example 1:**\n\n"
        "```\nInput: nums = [-1,0]\nOutput: []\n    indented <kept>\n```\n\n"
        "- `3 <= n`\n- `-10⁵ <= x` and 2^n\n\n1. first\n1. second\n\nSee [docs](https://x.org/a)\n\n### Note"
    )


def test_marimo_markdown_dedents_md_like_marimo():
    from gpython.markdown import marimo_markdown

    source = 'mo.vstack([\n    mo.md("""\n        ## Title\n\n        body\n        """),\n])'
    assert marimo_markdown(source) == "## Title\n\nbody"


@sync
async def test_marimo_view_shows_display_code_rendered():
    source = "mo.vstack([\n    mo.md('## T\\n\\nbody'),\n    mo.Html('<p>x</p>'),\n])"

    class MarimoApp(MdApp):
        def compose(self) -> ComposeResult:
            yield MarkdownView(self.text, marimo=True, id="marimo")
            yield MarkdownView(self.text, id="plain")

    app = MarimoApp(source)
    async with app.run_test() as pilot:
        await pilot.pause()
        view, plain = app.query_one("#marimo", MarkdownView), app.query_one("#plain", MarkdownView)
        assert view.text == source and view.shown == "## T\n\nbody\n\nx"
        assert "mo.vstack" not in headings_text(view) and "body" in headings_text(view)
        assert plain.shown == source
        await view.update("mo.md(f'{x}')")
        assert view.shown == "mo.md(f'{x}')"
