"""Markdown cells in the app, and docstring cells that must not echo (plan 04)."""

import asyncio

import pytest
from textual.widgets import Markdown

from gpython import notebook
from gpython.app import GPython, ends_with_string
from gpython.markdown import MarkdownView
from gpython.notebook import CellSpec
from gpython.vim import VimTextArea

CELLS = [CellSpec("x = 1"), CellSpec("### Hello\n\nSome *text*.", "markdown"), CellSpec("print(x)")]


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    VimTextArea.enabled = True
    yield
    VimTextArea.enabled = True
    VimTextArea.line_numbers = True


def ui(tmp_path, test, cells=CELLS, kernel=False):
    path = tmp_path / "nb.py"
    notebook.save(path, cells)

    async def main():
        app = GPython(path)
        async with app.run_test(size=(120, 40)) as pilot:
            await app.loaded.wait()
            if kernel:
                await asyncio.wait_for(app.kernel.ready.wait(), 60)
            await pilot.pause()
            await test(app, pilot)

    asyncio.run(main())
    return path


def view_text(cell) -> str:
    """The text the rendered Markdown widget is showing."""
    return cell.query_one(MarkdownView).query_one(Markdown).source


def test_markdown_cell_loads_rendered(tmp_path):
    async def test(app, pilot):
        code, md, _ = app.cells()
        assert md.kind == "markdown" and md.has_class("-markdown") and code.kind == "code"
        assert md.rendered and md.editor.display is False
        view = md.query_one(MarkdownView)
        assert view.display is True and "Hello" in view_text(md)
        assert md.editor.language == "markdown"
        assert not code.query(MarkdownView)  # code cells never build one
        header = str(md.query_one(".cell-header").render())
        assert "¶" in header and "MARKDOWN" in header and "defines" not in header
        assert md.info == ""

    ui(tmp_path, test)


def test_edit_and_render_markdown_with_keys(tmp_path):
    async def test(app, pilot):
        _, md, third = app.cells()
        md.focus()
        await pilot.press("enter")
        assert app.focused is md.editor and app.ui_mode == "normal"
        assert md.editor.display is True and md.query_one(MarkdownView).display is False
        await pilot.press("i", *"Hi ", "escape", "escape")
        await pilot.pause()
        assert md.source.startswith("Hi ### Hello")
        assert app.focused is md and md.rendered
        assert md.editor.display is False and view_text(md).startswith("Hi ")
        assert app.dirty

        # shift+enter on markdown renders it and moves on; the kernel is not touched.
        await pilot.press("enter")
        md.editor.text = "# Title"
        await pilot.press("shift+enter")
        await pilot.pause()
        assert md.rendered and view_text(md) == "# Title"
        assert app.focused is third.editor and md.exec_count == 0 and md.status == "idle"

        # Moving the cursor into a rendered cell selects it (cell mode) instead of a hidden editor.
        await pilot.press("alt+up")
        assert app.focused is md

        # Leaving the editor by focusing another cell renders it too.
        await pilot.press("i")
        assert app.focused is md.editor and app.ui_mode == "insert"
        await pilot.press("x")
        third.editor.focus()
        await pilot.pause()
        await pilot.pause()
        assert md.rendered and view_text(md) == "x# Title"

    ui(tmp_path, test)


def test_m_and_y_switch_kind_and_reactivity(tmp_path):
    cells = [CellSpec("x = 1"), CellSpec("print(x + 1)")]

    async def test(app, pilot):
        first, second = app.cells()
        first.editor.focus()
        await pilot.press("ctrl+e")
        await app.workers.wait_for_complete()
        assert second.output_text == "2\n" and first.last_defs == {"x"}

        await pilot.press("escape", "escape", "m")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert first.kind == "markdown" and first.rendered and app.focused is first
        assert first.status == "idle" and first.output_text == ""
        assert "NameError" in second.output_text and second.status == "error"
        assert first.cid not in app.graph().order
        assert app.dirty

        await pilot.press("y")
        await pilot.pause()
        assert first.kind == "code" and first.editor.display is True and app.focused is first
        assert first.editor.language == "python"
        await pilot.press("escape")  # end the pending `y` (yy would copy)
        await pilot.press("shift+enter")
        await app.workers.wait_for_complete()
        assert first.status == "ok" and second.output_text == "2\n"

        # :md / :code from the editor keep editing; the cell renders when left.
        second.editor.focus()
        await pilot.press(":", *"md", "enter")
        await pilot.pause()
        assert second.kind == "markdown" and not second.rendered and app.focused is second.editor
        await pilot.press("escape")
        await pilot.pause()
        await pilot.pause()
        assert second.rendered and app.focused is second
        await pilot.press(":", *"code", "enter")
        await pilot.pause()
        assert second.kind == "code" and app.focused is second

        # yy on a markdown cell copies it and leaves it markdown.
        await app.set_cell_kind(second, "markdown")
        await pilot.press("y", "y", "p")
        await pilot.pause()
        cells_now = app.cells()
        assert second.kind == "markdown" and cells_now[2].kind == "markdown"
        assert cells_now[2].source == second.source and cells_now[2].rendered

    ui(tmp_path, test, cells=cells, kernel=True)


def test_markdown_cells_are_not_in_the_graph(tmp_path):
    async def test(app, pilot):
        code, md, last = app.cells()
        graph = app.graph()
        assert md.cid not in graph.order and code.cid in graph.order and last.cid in graph.order
        app.refresh_info()
        assert md.info == "" and "uses x" in last.info

    ui(tmp_path, test)


def test_save_roundtrip_keeps_kinds_and_undo_clipboard(tmp_path):
    async def test(app, pilot):
        _, md, _ = app.cells()
        md.focus()
        await pilot.press("d", "d")
        assert [c.kind for c in app.cells()] == ["code", "code"]
        await pilot.press("z")
        await pilot.pause()
        restored = app.cells()[1]
        assert restored.kind == "markdown" and restored.rendered and "Hello" in view_text(restored)
        await pilot.press("ctrl+s")

    path = ui(tmp_path, test)
    specs, markers = notebook.load_cells(path)
    assert markers and [s.kind for s in specs] == ["code", "markdown", "code"]
    assert specs == CELLS


def test_ends_with_string():
    assert ends_with_string('"""doc"""')
    assert ends_with_string('x = 1\n"""note"""')
    assert not ends_with_string('"""doc"""\nx')
    assert not ends_with_string("x = 1")
    assert not ends_with_string("%timeit 1")
    assert not ends_with_string("")


def test_docstring_cells_do_not_echo(tmp_path):
    cells = [CellSpec('"""doc"""'), CellSpec('x = 1\n"""note"""'), CellSpec('"""doc"""\nx'), CellSpec('print("hi")\n"""n"""')]

    async def test(app, pilot):
        app.action_run_all()
        await app.workers.wait_for_complete()
        doc, note, value, printed = app.cells()
        assert all(c.status == "ok" for c in app.cells())
        assert doc.output_text == "" and note.output_text == ""
        assert value.output_text == "1"
        assert printed.output_text == "hi\n"  # streams still show

    ui(tmp_path, test, cells=cells, kernel=True)


def test_cell_undo_covers_moves_deletes_inserts_and_kind(tmp_path):
    async def test(app, pilot):
        def kinds_and_sources():
            return [(c.kind, c.source) for c in app.cells()]

        original = kinds_and_sources()
        first = app.cells()[0]
        first.focus()
        await pilot.press("J")  # move down
        assert app.cells()[1] is first
        await pilot.press("u")
        assert kinds_and_sources() == original and app.focused is first
        assert [c.label for c in app.cells()] == ["c1", "c2", "c3"]

        await pilot.press("alt+shift+down", "alt+shift+down", "u")
        assert app.cells().index(first) == 1
        await pilot.press("u")
        assert kinds_and_sources() == original and app.focused is first

        await pilot.press("d", "d")
        assert len(app.cells()) == 2
        await pilot.press("u")
        await pilot.pause()
        assert kinds_and_sources() == original and app.focused is app.cells()[0]

        third = app.cells()[2]
        third.focus()
        await pilot.press("m")
        await pilot.pause()
        assert third.kind == "markdown"
        await pilot.press("u")
        await pilot.pause()
        assert third.kind == "code" and app.focused is third

        await pilot.press("b")  # insert below; undo removes it
        assert len(app.cells()) == 4
        await pilot.press("escape", "escape", "u")
        await pilot.pause()
        assert kinds_and_sources() == original

        await pilot.press("z")  # alias; the stack is now empty
        assert kinds_and_sources() == original

    ui(tmp_path, test)


def test_save_keeps_the_loaded_spec_fields(tmp_path):
    async def test(app, pilot):
        cell = app.cells()[0]
        assert cell.spec == CELLS[0]
        cell.editor.text = "x = 2"
        await pilot.press("ctrl+s")

    path = ui(tmp_path, test)
    specs, _ = notebook.load_cells(path)
    assert specs[0] == CellSpec("x = 2") and specs[1] == CELLS[1]
