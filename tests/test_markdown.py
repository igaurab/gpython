"""MarkdownView on its own."""

from __future__ import annotations

import asyncio
import functools

from textual.app import App, ComposeResult
from textual.widgets import Markdown

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
    return " ".join(str(block._content) for block in view.markdown.children)


@sync
async def test_renders_heading():
    app = MdApp("### Hello")
    async with app.run_test() as pilot:
        await pilot.pause()
        view = app.query_one(MarkdownView)
        assert view.markdown.source == "### Hello"
        assert "Hello" in headings_text(view)
        assert not view.has_class("-empty")


@sync
async def test_blank_shows_placeholder():
    app = MdApp("   \n")
    async with app.run_test() as pilot:
        await pilot.pause()
        view = app.query_one(MarkdownView)
        assert view.markdown.source == PLACEHOLDER
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
        assert view.markdown.source == "# Title\n\nbody"
        assert "Title" in headings_text(view)
        await view.update("")
        await pilot.pause()
        assert view.text == "" and view.markdown.source == PLACEHOLDER


@sync
async def test_double_click_requests_edit():
    app = MdApp("# Hi")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click(MarkdownView, times=2)
        await pilot.pause()
        assert app.requests == [app.query_one(MarkdownView)]
