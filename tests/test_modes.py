"""Cell mode and edit mode are one global choice: passing through a rendered markdown or
hidden-code cell, leaving for the explorer, or running a markdown cell never flips it."""

import asyncio

import pytest

from gpython import notebook
from gpython.app import GPython
from gpython.notebook import CellSpec
from gpython.vim import VimTextArea

CELLS = [CellSpec("a = 1"), CellSpec("## Notes", "markdown"), CellSpec("b = 2"), CellSpec("c = 3")]


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    VimTextArea.enabled = True
    yield
    VimTextArea.enabled = True
    VimTextArea.line_numbers = True


def ui(tmp_path, test, cells=CELLS):
    path = tmp_path / "nb.py"
    notebook.save(path, cells)

    async def main():
        app = GPython(path)
        async with app.run_test(size=(120, 40)) as pilot:
            await app.loaded.wait()
            await pilot.pause()
            await test(app, pilot)

    asyncio.run(main())


def test_edit_mode_survives_a_rendered_markdown_cell(tmp_path):
    async def test(app, pilot):
        first, md, third, _ = app.cells()
        assert md.editor_hidden and app.focused is first.editor and not app.cell_mode
        await pilot.press("j")
        await pilot.pause()
        assert app.focused is md and app.ui_mode == "cell" and not app.cell_mode
        await pilot.press("j")
        await pilot.pause()
        assert app.focused is third.editor and app.ui_mode == "normal"
        await pilot.press("k")
        await pilot.pause()
        assert app.focused is md
        await pilot.press("k")
        await pilot.pause()
        assert app.focused is first.editor and app.ui_mode == "normal"

    ui(tmp_path, test)


def test_cell_mode_survives_a_rendered_markdown_cell(tmp_path):
    async def test(app, pilot):
        first, md, third, _ = app.cells()
        await pilot.press("escape")
        await pilot.pause()
        assert app.focused is first and app.cell_mode
        await pilot.press("j", "j")
        await pilot.pause()
        assert app.focused is third and app.ui_mode == "cell"
        await pilot.press("G")
        await pilot.pause()
        assert app.ui_mode == "cell"

    ui(tmp_path, test)


def test_hiding_code_while_editing_keeps_edit_mode(tmp_path):
    async def test(app, pilot):
        first, md, third, last = app.cells()
        await pilot.press("escape", "escape", "enter")
        await pilot.press("j", "j")
        await pilot.pause()
        assert app.focused is third.editor
        await pilot.press("escape", "h")
        await pilot.pause()
        assert third.editor_hidden and app.focused is third and app.cell_mode
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("escape")
        assert app.focused is third.editor or app.focused is third

    ui(tmp_path, test)


def test_explorer_round_trip_keeps_the_mode(tmp_path):
    async def test(app, pilot):
        first, md, third, _ = app.cells()
        await pilot.press("escape")
        await pilot.pause()
        assert app.focused is first and app.cell_mode
        await pilot.press("ctrl+b")
        await pilot.pause()
        assert app.ui_mode == "files"
        await pilot.press("ctrl+b")
        await pilot.pause()
        assert app.focused is first and app.ui_mode == "cell"
        await pilot.press("enter")
        await pilot.pause()
        assert app.focused is first.editor and not app.cell_mode
        await pilot.press("ctrl+b")
        await pilot.pause()
        await pilot.press("ctrl+b")
        await pilot.pause()
        assert app.focused is first.editor and app.ui_mode == "normal"

    ui(tmp_path, test)


def test_rendering_a_markdown_cell_from_its_editor_keeps_edit_mode(tmp_path):
    async def test(app, pilot):
        first, md, third, _ = app.cells()
        await pilot.press("j")
        await pilot.pause()
        assert app.focused is md
        await pilot.press("enter")
        await pilot.pause()
        assert app.focused is md.editor and not md.editor_hidden
        await pilot.press("shift+enter")
        await pilot.pause()
        assert md.editor_hidden and app.focused is third.editor and app.ui_mode == "normal"
        await pilot.press("k")
        await pilot.pause()
        assert app.focused is md and not app.cell_mode
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("ctrl+enter")
        await pilot.pause()
        assert md.editor_hidden and app.focused is md and not app.cell_mode
        await pilot.press("j")
        await pilot.pause()
        assert app.focused is third.editor

    ui(tmp_path, test)
