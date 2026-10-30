"""Go to a cell by its number: g N in cell mode; g g, g t and g T keep working."""

import asyncio

import pytest

from gpython import notebook, runnumber
from gpython.app import GPython
from gpython.notebook import CellSpec
from gpython.vim import VimTextArea


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    VimTextArea.enabled = True
    yield
    VimTextArea.enabled = True
    VimTextArea.line_numbers = True


def many_cells(n):
    return [CellSpec(f"v{i} = {i}") for i in range(1, n + 1)]


def ui(tmp_path, test, cells=None):
    path = tmp_path / "nb.py"
    notebook.save(path, cells or many_cells(12))

    async def main():
        app = GPython(path)
        async with app.run_test(size=(140, 40)) as pilot:
            await app.loaded.wait()
            await pilot.pause()
            await test(app, pilot)

    asyncio.run(main())


def status(app) -> str:
    return str(app.query_one("#status").render())


def test_g_number_selects_that_cell_in_cell_mode(tmp_path):
    async def test(app, pilot):
        cells = app.cells()
        cells[3].focus()
        await pilot.press("g")
        assert app._number_sequence is not None and "go to cell: c_" in status(app)
        await pilot.press("1")
        await pilot.pause()
        assert app._number_sequence is not None and "go to cell: c1_" in status(app)
        await pilot.press("enter")
        await pilot.pause()
        assert app.focused is cells[0] and app._number_sequence is None and "CELL" in status(app)
        await pilot.press("g", "6")
        await pilot.pause()
        assert app.focused is cells[5] and app._number_sequence is None

    ui(tmp_path, test)


def test_single_digit_goes_at_once_when_unambiguous(tmp_path):
    async def test(app, pilot):
        cells = app.cells()
        cells[0].focus()
        await pilot.press("g", "3")
        await pilot.pause()
        assert app.focused is cells[2] and app._number_sequence is None

    ui(tmp_path, test, cells=many_cells(4))


def test_two_digits_go_once_unambiguous(tmp_path):
    async def test(app, pilot):
        cells = app.cells()
        cells[0].focus()
        await pilot.press("g", "1", "2")
        await pilot.pause()
        assert app.focused is cells[11] and app._number_sequence is None

    ui(tmp_path, test)


def test_g_number_goes_after_a_pause(tmp_path, monkeypatch):
    monkeypatch.setattr(runnumber, "PAUSE", 0.05)

    async def test(app, pilot):
        cells = app.cells()
        cells[0].focus()
        await pilot.press("g", "1")
        await pilot.pause()
        assert app.focused is cells[0]
        await asyncio.sleep(0.2)
        await pilot.pause()
        assert app.focused is cells[0] and app._number_sequence is None
        cells[4].focus()
        await pilot.press("g", "1")
        await asyncio.sleep(0.2)
        await pilot.pause()
        assert app.focused is cells[0]

    ui(tmp_path, test)


def test_gg_and_counted_gg_still_work(tmp_path):
    async def test(app, pilot):
        cells = app.cells()
        cells[5].focus()
        await pilot.press("g", "g")
        await pilot.pause()
        assert app.focused is cells[0] and app._number_sequence is None
        await pilot.press("4", "g", "g")
        await pilot.pause()
        assert app.focused is cells[3]

    ui(tmp_path, test)


def test_gt_switches_tabs(tmp_path):
    async def test(app, pilot):
        app.cells()[0].focus()
        await pilot.press(":")
        await pilot.pause()
        for ch in "tabnew":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause()
        assert len(app.workspace.tabs) == 2 and app.workspace.index == 1
        await pilot.press("g", "T")
        await pilot.pause()
        assert app.workspace.index == 0 and app._number_sequence is None
        await pilot.press("g", "t")
        await pilot.pause()
        assert app.workspace.index == 1
        await pilot.press("1", "g", "t")
        await pilot.pause()
        assert app.workspace.index == 0

    ui(tmp_path, test, cells=many_cells(3))


def test_unknown_cell_number_warns_and_stays(tmp_path):
    async def test(app, pilot):
        cells = app.cells()
        cells[1].focus()
        await pilot.press("g", "9")
        await pilot.pause()
        assert app.focused is cells[1] and app._number_sequence is None

    ui(tmp_path, test, cells=many_cells(4))


def test_escape_cancels(tmp_path):
    async def test(app, pilot):
        cells = app.cells()
        cells[1].focus()
        await pilot.press("g", "1", "escape")
        await pilot.pause()
        assert app.focused is cells[1] and app._number_sequence is None and "go to cell" not in status(app)

    ui(tmp_path, test)


def test_g_in_insert_mode_types_g(tmp_path):
    async def test(app, pilot):
        cell = app.cells()[0]
        cell.focus()
        await pilot.press("i", "g", "6")
        await pilot.pause()
        assert app._number_sequence is None and cell.source.startswith("g6")

    ui(tmp_path, test, cells=many_cells(8))
