"""Run-all draws off-screen cells once, after the run (docs/plans/12-run-all-scaling.md)."""

import asyncio

import pytest

from gpython import notebook
from gpython.app import GPython
from gpython.notebook import CellSpec
from gpython.vim import VimTextArea


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    VimTextArea.enabled = True
    yield
    VimTextArea.enabled = True


def run_app(tmp_path, cells, test):
    path = tmp_path / "nb.py"
    notebook.save(path, cells)

    async def main():
        app = GPython(path)
        async with app.run_test(size=(140, 40)) as pilot:
            await app.pane.loaded.wait()
            await asyncio.wait_for(app.kernel.ready.wait(), 60)
            await pilot.pause()
            await test(app, pilot)

    asyncio.run(main())


def record_deferral(app):
    """Wrap the pane's per-cell step to note, per cell, whether its drawing was deferred."""
    seen = {}
    real = app.pane._execute_in_run

    async def spy(cell, cid, *args):
        seen[cid] = cell.defer_drawing
        await real(cell, cid, *args)

    app.pane._execute_in_run = spy
    return seen


def test_run_all_defers_off_screen_cells_and_draws_every_cell_after(tmp_path):
    cells = [CellSpec(f"v{i} = {i}\nprint('cell', {i})") for i in range(40)]

    async def test(app, pilot):
        seen = record_deferral(app)
        await app.pane.run_cells([c.cid for c in app.cells()], downstream=True)
        await pilot.pause()
        shown = app.cells()
        assert not seen[shown[0].cid]
        assert seen[shown[-1].cid]
        assert all(not c.drawing_deferred and not c.defer_drawing for c in shown)
        assert [c.output_text for c in shown] == [f"cell {i}\n" for i in range(40)]
        assert [c.status for c in shown] == ["ok"] * 40
        assert shown[-1].exec_count == 40
        assert shown[-1].outputs_snapshot() == [("stream", {"name": "stdout", "text": "cell 39\n"})]

    run_app(tmp_path, cells, test)


def test_second_run_all_replaces_the_outputs_of_off_screen_cells(tmp_path):
    cells = [CellSpec(f"print('first', {i})") for i in range(30)]

    async def test(app, pilot):
        await app.pane.run_cells([c.cid for c in app.cells()], downstream=True)
        last = app.cells()[-1]
        last.editor.text = "print('second')"
        await app.pane.run_cells([c.cid for c in app.cells()], downstream=True)
        await pilot.pause()
        assert last.output_text == "second\n"

    run_app(tmp_path, cells, test)


def test_hint_under_an_off_screen_failure_survives_the_deferred_draw(tmp_path):
    cells = [CellSpec(f"v{i} = {i}") for i in range(30)] + [CellSpec("import gpython_no_such_module_xyz")]

    async def test(app, pilot):
        await app.pane.run_cells([c.cid for c in app.cells()], downstream=True)
        await pilot.pause()
        failed = app.cells()[-1]
        assert failed.status == "error"
        assert "ModuleNotFoundError" in failed.output_text
        assert "gpython_no_such_module_xyz is not installed" in failed.output_text

    run_app(tmp_path, cells, test)


def test_running_one_cell_never_defers_its_drawing(tmp_path):
    cells = [CellSpec(f"v{i} = {i}") for i in range(30)] + [CellSpec("print('alone')")]

    async def test(app, pilot):
        seen = record_deferral(app)
        last = app.cells()[-1]
        await app.pane.run_cells([last.cid], downstream=False)
        await pilot.pause()
        assert seen == {last.cid: False}
        assert last.output_text == "alone\n"

    run_app(tmp_path, cells, test)
