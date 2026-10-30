"""Markdown cells in the app, and docstring cells that must not echo (plan 04)."""

import asyncio

import pytest

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
    """The markdown the rendered view is showing."""
    return cell.query_one(MarkdownView).shown


def test_markdown_cell_loads_rendered(tmp_path):
    async def test(app, pilot):
        code, md, _ = app.cells()
        assert md.kind == "markdown" and md.has_class("-markdown") and code.kind == "code"
        assert md.rendered and md.editor.display is False
        view = md.query_one(MarkdownView)
        assert view.display is True and "Hello" in view_text(md)
        assert md.editor.language == "markdown"
        assert not code.query(MarkdownView)
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

        await pilot.press("enter")
        md.editor.text = "# Title"
        await pilot.press("shift+enter")
        await pilot.pause()
        assert md.rendered and view_text(md) == "# Title"
        assert app.focused is third.editor and md.exec_count == 0 and md.status == "idle"

        await pilot.press("alt+up")
        assert app.focused is md

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
        app.lazy = False
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
        await pilot.press("escape")
        await pilot.press("shift+enter")
        await app.workers.wait_for_complete()
        assert first.status == "ok" and second.output_text == "2\n"

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
        assert printed.output_text == "hi\n"

    ui(tmp_path, test, cells=cells, kernel=True)


def test_cell_undo_covers_moves_deletes_inserts_and_kind(tmp_path):
    async def test(app, pilot):
        def kinds_and_sources():
            return [(c.kind, c.source) for c in app.cells()]

        original = kinds_and_sources()
        first = app.cells()[0]
        first.focus()
        await pilot.press("J")
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

        await pilot.press("b")
        assert len(app.cells()) == 4
        await pilot.press("escape", "escape", "u")
        await pilot.pause()
        assert kinds_and_sources() == original

        await pilot.press("z")
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


def test_h_folds_a_markdown_cell(tmp_path):
    async def test(app, pilot):
        _, md, _ = app.cells()
        md.focus()
        assert not app.dirty
        await pilot.press("h")
        assert md.folded and md.query_one(MarkdownView).display is False
        assert "folded" in str(md.query_one(".cell-header").render())
        assert not app.dirty
        await pilot.press("h")
        assert not md.folded and md.query_one(MarkdownView).display is True

        await pilot.press("h", "enter")
        assert not md.folded and app.focused is md.editor
        await pilot.press("escape", "escape")
        await pilot.pause()
        assert md.rendered and md.query_one(MarkdownView).display is True

    ui(tmp_path, test)


def test_hidden_marimo_text_cell_shows_its_text_before_running(tmp_path):
    cells = [
        CellSpec("import marimo as mo", hide_code=True),
        CellSpec("mo.vstack([mo.md('## 15. 3Sum\\n\\nGiven nums'), mo.Html('<p>Return <b>triplets</b></p>')])", hide_code=True),
        CellSpec("print(1)"),
    ]

    async def test(app, pilot):
        _, problem, plain = app.cells()
        assert problem.kind == "code" and problem.code_hidden and problem.exec_count == 0
        shown = problem.output_box.query_one(MarkdownView).shown
        assert "3Sum" in shown and "Return **triplets**" in shown
        assert "\\n" not in shown
        assert not problem.outputs_snapshot()
        assert not plain.output_box.has_output

    path = ui(tmp_path, test, cells)
    assert "@app.cell(hide_code=True)" in path.read_text()


def test_markdown_follows_the_theme(tmp_path):
    from rich.color import Color
    from textual.geometry import Region

    def heading_color(view) -> Color | None:
        strip = view.render_lines(Region(0, 0, view.size.width, 1))[0]
        return next(seg.style.color for seg in strip if seg.text.strip() and seg.style and seg.style.color)

    async def test(app, pilot):
        view = app.cells()[1].query_one(MarkdownView)
        app.theme = "nord"
        await pilot.pause()
        nord = heading_color(view)
        assert nord == Color.parse(app.palette.type)
        app.theme = "catppuccin-latte"
        await pilot.pause()
        assert heading_color(view) == Color.parse(app.palette.type) != nord

    ui(tmp_path, test)


PROBLEM = "mo.vstack([\n    mo.md('## [15. 3Sum  (Medium)](https://leetcode.com/problems/3sum/)\\n\\n*Two Pointers*'),\n    mo.Html('<p>Given <code>nums</code>.</p>\\n\\n<pre>\\n<strong>Input:</strong> nums = [0,0,0]\\n</pre>\\n<ul>\\n\\t<li><code>-10<sup>5</sup> &lt;= nums[i]</code></li>\\n</ul>\\n'),\n])"
PROBLEM_SHOWN = "## [15. 3Sum  (Medium)](https://leetcode.com/problems/3sum/)\n\n*Two Pointers*\n\nGiven `nums`.\n\n```\nInput: nums = [0,0,0]\n```\n\n- `-10⁵ <= nums[i]`"
WRAPPED = '''import marimo

__generated_with = "0.25.2"
app = marimo.App(width="medium")


@app.cell(hide_code=True)
def _():
    import marimo as mo

    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
''' + "".join("    " + line + "\n" for line in PROBLEM.split("\n")) + '''    """)
    return


@app.cell
def _():
    x = 1
    return


if __name__ == "__main__":
    app.run()
'''


def ui_text(tmp_path, test, text):
    path = tmp_path / "nb.py"
    path.write_text(text)

    async def main():
        app = GPython(path)
        async with app.run_test(size=(120, 40)) as pilot:
            await app.loaded.wait()
            await pilot.pause()
            await test(app, pilot)

    asyncio.run(main())
    return path


def test_markdown_cell_holding_marimo_display_code_renders_it(tmp_path):
    async def test(app, pilot):
        md = app.cells()[1]
        assert md.kind == "markdown" and md.rendered and md.source == PROBLEM
        view = md.query_one(MarkdownView)
        assert view.text == PROBLEM and view.shown == PROBLEM_SHOWN
        md.focus()
        await pilot.press("enter")
        await pilot.pause()
        assert app.focused is md.editor and md.editor.text == PROBLEM
        await pilot.press("escape", "escape")
        await pilot.pause()
        assert md.rendered and view_text(md) == PROBLEM_SHOWN
        await pilot.press("ctrl+s")
        await pilot.pause()

    path = ui_text(tmp_path, test, WRAPPED)
    assert path.read_text() == WRAPPED


def test_m_on_hidden_marimo_display_cell_keeps_it_rendered(tmp_path):
    hidden = WRAPPED.replace('    mo.md(r"""\n', "").replace('    """)\n    return\n\n\n@app.cell\n', "    return\n\n\n@app.cell\n")
    assert hidden != WRAPPED

    async def test(app, pilot):
        problem = app.cells()[1]
        assert problem.kind == "code" and problem.code_hidden
        problem.focus()
        await pilot.press("m")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert problem.kind == "markdown" and problem.rendered
        assert view_text(problem) == PROBLEM_SHOWN
        await pilot.press("ctrl+s")
        await pilot.pause()

    path = ui_text(tmp_path, test, hidden)
    assert path.read_text() == WRAPPED
