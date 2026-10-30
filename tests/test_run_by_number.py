"""Run a cell by its number: r N in cell mode, space r N in vim normal mode, :run N."""

import asyncio

import pytest

from gpython import notebook, runnumber
from gpython.app import GPython
from gpython.notebook import CellSpec
from gpython.vim import VimTextArea

CELLS = [CellSpec("a = 1"), CellSpec("## Notes", "markdown"), CellSpec("print(a + 2)"), CellSpec("b = 4")]


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
        async with app.run_test(size=(140, 40)) as pilot:
            await app.loaded.wait()
            if kernel:
                await asyncio.wait_for(app.kernel.ready.wait(), 60)
            await pilot.pause()
            await test(app, pilot)

    asyncio.run(main())


def status(app) -> str:
    return str(app.query_one("#status").render())


async def settle(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


def many_cells(n):
    return [CellSpec(f"v{i} = {i}\nprint('cell', {i})") for i in range(1, n + 1)]


def test_r_number_runs_that_cell_and_keeps_the_selection(tmp_path):
    async def test(app, pilot):
        first, _, show, last = app.cells()
        last.focus()
        await pilot.press("r")
        assert app._run_number is not None
        await pilot.press("3")
        await settle(app, pilot)
        assert show.output_text == "3\n" and show.exec_count > 0
        assert first.status == "ok"
        assert app.focused is last and last.exec_count == 0
        assert app._run_number is None and "CELL" in status(app)

    ui(tmp_path, test, kernel=True)


def test_status_bar_while_pending(tmp_path):
    async def test(app, pilot):
        app.cells()[0].focus()
        await pilot.press("r")
        await pilot.pause()
        text = status(app)
        assert "run cell: c_" in text and "press a cell number to run that cell" in text

    ui(tmp_path, test, cells=many_cells(12))


def test_digits_show_while_waiting(tmp_path):
    async def test(app, pilot):
        app.cells()[0].focus()
        await pilot.press("r", "1")
        await pilot.pause()
        assert "run cell: c1_" in status(app)
        await pilot.press("escape")

    ui(tmp_path, test, cells=many_cells(12))


def test_no_digit_times_out(tmp_path, monkeypatch):
    monkeypatch.setattr(runnumber, "TIMEOUT", 0.2)

    async def test(app, pilot):
        cell = app.cells()[0]
        cell.focus()
        await pilot.press("r")
        assert app._run_number is not None
        await asyncio.sleep(0.4)
        await pilot.pause()
        assert app._run_number is None
        assert "run cell" not in status(app) and "CELL" in status(app)
        await pilot.press("j")
        assert app.focused is app.cells()[1]

    ui(tmp_path, test)


def test_escape_and_other_keys_cancel(tmp_path):
    async def test(app, pilot):
        cells = app.cells()
        cells[0].focus()
        await pilot.press("r", "escape")
        assert app._run_number is None and "run cell" not in status(app)
        await pilot.press("r", "x")
        assert app._run_number is None and len(app.cells()) == len(cells)
        assert all(c.exec_count == 0 for c in app.cells())

    ui(tmp_path, test)


def test_multi_digit_waits_until_unambiguous(tmp_path, monkeypatch):
    monkeypatch.setattr(runnumber, "PAUSE", 30)

    async def test(app, pilot):
        cells = app.cells()
        cells[0].focus()
        await pilot.press("r", "1")
        await pilot.pause()
        assert app._run_number is not None
        await pilot.press("2")
        await settle(app, pilot)
        assert app._run_number is None
        assert cells[11].output_text == "cell 12\n"
        assert cells[0].exec_count == 0 and app.focused is cells[0]

    ui(tmp_path, test, cells=many_cells(13), kernel=True)


def test_single_digit_runs_at_once_when_unambiguous(tmp_path, monkeypatch):
    monkeypatch.setattr(runnumber, "PAUSE", 30)

    async def test(app, pilot):
        cells = app.cells()
        cells[0].focus()
        await pilot.press("r", "2")
        await settle(app, pilot)
        assert app._run_number is None and cells[1].output_text == "cell 2\n"

    ui(tmp_path, test, cells=many_cells(12), kernel=True)


def test_r_1_enter_runs_c1(tmp_path, monkeypatch):
    monkeypatch.setattr(runnumber, "PAUSE", 30)

    async def test(app, pilot):
        cells = app.cells()
        cells[2].focus()
        await pilot.press("r", "1", "enter")
        await settle(app, pilot)
        assert cells[0].output_text == "cell 1\n" and cells[9].exec_count == 0
        assert app.focused is cells[2]

    ui(tmp_path, test, cells=many_cells(12), kernel=True)


def test_r_1_runs_after_a_pause(tmp_path, monkeypatch):
    monkeypatch.setattr(runnumber, "PAUSE", 0.2)

    async def test(app, pilot):
        cells = app.cells()
        cells[2].focus()
        await pilot.press("r", "1")
        await asyncio.sleep(0.4)
        await settle(app, pilot)
        assert app._run_number is None and cells[0].output_text == "cell 1\n"

    ui(tmp_path, test, cells=many_cells(12), kernel=True)


def test_unknown_cell_number_warns_and_cancels(tmp_path):
    async def test(app, pilot):
        app.cells()[0].focus()
        await pilot.press("r", "9")
        await pilot.pause()
        assert app._run_number is None
        assert any("No cell c9" in n.message for n in app._notifications)
        assert all(c.exec_count == 0 for c in app.cells())

    ui(tmp_path, test)


def test_markdown_target_is_rendered(tmp_path):
    async def test(app, pilot):
        _, md, _, last = app.cells()
        md.start_editing()
        last.focus()
        await pilot.pause()
        assert not md.rendered
        await pilot.press("r", "2")
        await settle(app, pilot)
        assert md.rendered and app.focused is last

    ui(tmp_path, test)


def test_space_r_from_vim_normal_mode(tmp_path):
    async def test(app, pilot):
        first, _, show, last = app.cells()
        app.select(last, "normal")
        await pilot.pause()
        await pilot.press("space", "r")
        await pilot.pause()
        assert "run cell: c_" in status(app)
        await pilot.press("3")
        await settle(app, pilot)
        assert show.output_text == "3\n"
        assert app.focused is last.editor and VimTextArea.mode == "normal"
        assert last.source == "b = 4"

    ui(tmp_path, test, kernel=True)


def test_insert_mode_types_r_and_space(tmp_path):
    async def test(app, pilot):
        last = app.cells()[-1]
        app.select(last, "insert")
        last.editor.move_cursor((0, 5))
        await pilot.press("space", "r", "3")
        await pilot.pause()
        assert app._run_number is None
        assert last.source == "b = 4 r3"

    ui(tmp_path, test)


def test_ex_run_number(tmp_path):
    async def test(app, pilot):
        cells = app.cells()
        cells[3].focus()
        await pilot.press(":", *"run 3", "enter")
        await settle(app, pilot)
        assert cells[2].output_text == "3\n" and app.focused is cells[3]

    ui(tmp_path, test, kernel=True)
