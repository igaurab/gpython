"""OutputArea: stream batching and rich MIME outputs, driven in a tiny app."""

from __future__ import annotations

import functools

import asyncio

from textual.app import App, ComposeResult
from textual.widgets import Markdown, Static

from gpython import outputs
from gpython.markdown import MarkdownView
from gpython.outputs import MAX_STREAM_CHARS, OutputArea, collapse_carriage_returns, html_to_text


def sync(fn):
    """Run an async test with asyncio.run (no pytest async plugin is installed)."""

    @functools.wraps(fn)
    def wrapper():
        asyncio.run(fn())

    return wrapper


class OutApp(App):
    def compose(self) -> ComposeResult:
        yield OutputArea()


async def settle(pilot) -> None:
    await asyncio.sleep(outputs.FLUSH_INTERVAL * 2)
    await pilot.pause()
    await pilot.pause()


def stream(name: str, text: str) -> tuple[str, dict]:
    return "stream", {"name": name, "text": text}


def rendered(widget: Static) -> str:
    return str(widget.render())


@sync
async def test_stream_chunks_accumulate_before_and_after_flush():
    app = OutApp()
    async with app.run_test() as pilot:
        out = app.query_one(OutputArea)
        for i in range(5):
            out.add(*stream("stdout", f"line {i}\n"))
        expected = "".join(f"line {i}\n" for i in range(5))
        assert out.text == expected
        assert out._flush_timer is not None
        await settle(pilot)
        assert out._flush_timer is None
        assert out.text == expected
        block = out._blocks[0]
        assert "line 4" in rendered(block)
        assert len(out._blocks) == 1


@sync
async def test_carriage_returns_collapse():
    assert collapse_carriage_returns("a\rb\rc\nx\r\n") == "c\nx\n"
    app = OutApp()
    async with app.run_test() as pilot:
        out = app.query_one(OutputArea)
        for pct in (10, 50, 100):
            out.add(*stream("stdout", f"\rprogress {pct}%"))
        out.add(*stream("stdout", "\ndone\n"))
        await settle(pilot)
        shown = rendered(out._blocks[0])
        assert "progress 100%" in shown and "progress 10%" not in shown
        assert "done" in shown


@sync
async def test_trim_keeps_tail_and_notes_hidden():
    app = OutApp()
    async with app.run_test() as pilot:
        out = app.query_one(OutputArea)
        line = "x" * 99 + "\n"
        count = MAX_STREAM_CHARS // len(line) + 50
        for i in range(count - 1):
            out.add(*stream("stdout", line))
        out.add(*stream("stdout", "THE END\n"))
        await settle(pilot)
        shown = rendered(out._blocks[0])
        assert shown.rstrip().endswith("THE END")
        assert "earlier characters hidden" in shown
        assert len(shown) <= MAX_STREAM_CHARS + 100


@sync
async def test_clear_cancels_pending_flush():
    app = OutApp()
    async with app.run_test() as pilot:
        out = app.query_one(OutputArea)
        out.add(*stream("stdout", "hello\n"))
        assert out._flush_timer is not None
        out.clear()
        assert out._flush_timer is None
        assert out.text == "" and not out.has_output
        await settle(pilot)
        assert out.text == "" and not out.has_output
        assert not out.query(Static)


@sync
async def test_stdout_stderr_stdout_three_blocks():
    app = OutApp()
    async with app.run_test() as pilot:
        out = app.query_one(OutputArea)
        out.add(*stream("stdout", "a\n"))
        out.add(*stream("stderr", "b\n"))
        out.add(*stream("stdout", "c\n"))
        assert len(out._blocks) == 3
        assert out.text == "a\nb\nc\n"
        await settle(pilot)
        assert [rendered(b).strip() for b in out._blocks] == ["a", "b", "c"]


@sync
async def test_execute_result_after_stream_keeps_order():
    app = OutApp()
    async with app.run_test() as pilot:
        out = app.query_one(OutputArea)
        out.add(*stream("stdout", "printed\n"))
        out.add("execute_result", {"data": {"text/plain": "42"}})
        assert out.text == "printed\n42"
        await settle(pilot)
        assert out.text == "printed\n42"
        assert [rendered(b).strip() for b in out._blocks] == ["printed", "42"]
        assert list(out.children) == out._blocks


@sync
async def test_markdown_display_mounts_markdown_view():
    app = OutApp()
    async with app.run_test() as pilot:
        out = app.query_one(OutputArea)
        out.add("display_data", {"data": {"text/markdown": "## Title here", "text/plain": "<Markdown>"}})
        await pilot.pause()
        view = out.query_one(MarkdownView)
        assert view.text == "## Title here"
        assert "Title here" in out.text
        assert out.query_one(Markdown).source == "## Title here"


@sync
async def test_json_renders_and_in_text():
    app = OutApp()
    async with app.run_test() as pilot:
        out = app.query_one(OutputArea)
        out.add("display_data", {"data": {"application/json": {"a": [1, 2], "b": "hi"}, "text/plain": "x"}})
        await pilot.pause()
        assert '"a"' in out.text and '"hi"' in out.text
        assert '"b": "hi"' in rendered(out._blocks[0])


@sync
async def test_html_prefers_plain():
    app = OutApp()
    async with app.run_test() as pilot:
        out = app.query_one(OutputArea)
        out.add("execute_result", {"data": {"text/html": "<table><tr><td>1</td></tr></table>", "text/plain": "   a\n0  1"}})
        await pilot.pause()
        assert out.text == "   a\n0  1"
        assert "html rendered as text" not in rendered(out._blocks[0])


@sync
async def test_html_alone_is_stripped():
    assert html_to_text("<p>Hello <b>world</b></p><script>x()</script><p>bye &amp; ok</p>") == "Hello world\n\nbye & ok"
    app = OutApp()
    async with app.run_test() as pilot:
        out = app.query_one(OutputArea)
        out.add("display_data", {"data": {"text/html": "<div><h1>Big</h1><p>small <i>text</i></p></div>"}})
        await pilot.pause()
        assert "<" not in out.text
        assert "Big" in out.text and "small text" in out.text
        assert "[html rendered as text]" in rendered(out._blocks[0])


@sync
async def test_latex_shown_as_source_with_note():
    app = OutApp()
    async with app.run_test() as pilot:
        out = app.query_one(OutputArea)
        out.add("display_data", {"data": {"text/latex": "$x^2$"}})
        await pilot.pause()
        assert out.text == "$x^2$"
        assert "latex" in rendered(out._blocks[0])


@sync
async def test_unknown_mime_note():
    app = OutApp()
    async with app.run_test() as pilot:
        out = app.query_one(OutputArea)
        out.add("display_data", {"data": {"application/x-weird": "?"}})
        await pilot.pause()
        assert "not supported yet" in out.text


@sync
async def test_clear_output_wait_defers_until_next_output():
    app = OutApp()
    async with app.run_test() as pilot:
        out = app.query_one(OutputArea)
        out.add(*stream("stdout", "10%\n"))
        out.add("clear_output", {"wait": True})
        assert out.text == "10%\n" and out.has_output
        await settle(pilot)
        assert out.text == "10%\n"
        out.add(*stream("stdout", "20%\n"))
        assert out.text == "20%\n"
        out.add("clear_output", {"wait": True})
        out.add("display_data", {"data": {"text/plain": "done"}})
        assert out.text == "done"
        await settle(pilot)
        assert len(out.children) == 1


@sync
async def test_clear_output_without_wait_clears_now():
    app = OutApp()
    async with app.run_test() as pilot:
        out = app.query_one(OutputArea)
        out.add("execute_result", {"data": {"text/plain": "1"}})
        out.add("clear_output", {"wait": False})
        assert out.text == "" and not out.has_output
