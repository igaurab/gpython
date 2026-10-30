"""marimo's staleness model: a run first brings its stale ancestors up to date."""

import asyncio

import pytest

from gpython import notebook
from gpython.app import GPython
from gpython.notebook import CellSpec
from gpython.vim import VimTextArea

CELLS = [CellSpec("x = 1"), CellSpec("y = 10"), CellSpec("print(x, y)"), CellSpec("z = x + 1\nprint(z)")]


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    VimTextArea.enabled = True
    yield
    VimTextArea.enabled = True
    VimTextArea.line_numbers = True


def ui(tmp_path, test, cells=CELLS, lazy=False):
    path = tmp_path / "nb.py"
    notebook.save(path, cells)

    async def main():
        app = GPython(path)
        async with app.run_test(size=(120, 40)) as pilot:
            await app.loaded.wait()
            await asyncio.wait_for(app.kernel.ready.wait(), 60)
            app.lazy = lazy
            await pilot.pause()
            await test(app, pilot)

    asyncio.run(main())


async def run(app, cell):
    await app.run_cells([cell.cid])
    await app.workers.wait_for_complete()


async def run_all_clean(app):
    app.action_run_all()
    await app.workers.wait_for_complete()
    assert all(c.status == "ok" for c in app.cells())


@pytest.mark.parametrize("lazy", [False, True])
def test_never_run_ancestors_run_first(tmp_path, lazy):
    async def test(app, pilot):
        x, y, show, z = app.cells()
        await run(app, show)
        assert show.output_text == "1 10\n"
        assert x.status == y.status == show.status == "ok"
        assert z.status == ("stale" if lazy else "ok")

    ui(tmp_path, test, lazy=lazy)


@pytest.mark.parametrize("lazy", [False, True])
def test_edited_ancestor_runs_first_and_clean_one_does_not(tmp_path, lazy):
    async def test(app, pilot):
        x, y, show, z = app.cells()
        await run_all_clean(app)
        x_count, y_count = x.exec_count, y.exec_count
        x.editor.text = "x = 2"
        await pilot.pause()
        assert x.status == "stale" and show.status == "ok"
        await run(app, show)
        assert show.output_text == "2 10\n"
        assert x.status == "ok" and x_count < x.exec_count < show.exec_count
        assert y.exec_count == y_count
        if lazy:
            assert z.status == "stale" and z.output_text == "2\n"
        else:
            assert z.status == "ok" and z.output_text == "3\n"

    ui(tmp_path, test, lazy=lazy)


def test_editing_marks_stale_and_undoing_restores_status(tmp_path):
    async def test(app, pilot):
        x, *_ = app.cells()
        await run_all_clean(app)
        x.editor.text = "x = 5"
        await pilot.pause()
        assert x.status == "stale"
        x.editor.text = "x = 1"
        await pilot.pause()
        assert x.status == "ok"

    ui(tmp_path, test)


def test_lazy_input_stale_cell_runs_as_ancestor(tmp_path):
    cells = [CellSpec("x = 1"), CellSpec("w = x * 10"), CellSpec("print(w)")]

    async def test(app, pilot):
        x, w, show = app.cells()
        await run_all_clean(app)
        x.editor.text = "x = 3"
        await pilot.pause()
        await run(app, x)
        assert w.status == "stale" and show.status == "stale"
        w.editor.text = "w = x * 10 "
        w.editor.text = "w = x * 10"
        await pilot.pause()
        assert w.status == "stale"
        await run(app, show)
        assert show.output_text == "30\n" and w.status == "ok" and show.status == "ok"

    ui(tmp_path, test, cells=cells, lazy=True)


def test_failing_ancestor_stops_dependents(tmp_path):
    async def test(app, pilot):
        x, y, show, z = app.cells()
        await run_all_clean(app)
        count = show.exec_count
        x.editor.text = "x = 1 / 0"
        await pilot.pause()
        await run(app, show)
        assert x.status == "error"
        assert show.status == "skipped" and show.exec_count == count
        assert "upstream" in show.output_text

    ui(tmp_path, test)


@pytest.mark.parametrize("lazy", [False, True])
def test_run_stale_runs_every_stale_cell_in_order(tmp_path, lazy):
    async def test(app, pilot):
        x, y, show, z = app.cells()
        await run_all_clean(app)
        x.editor.text = "x = 4"
        y.editor.text = "y = 20"
        await pilot.pause()
        app.action_run_stale()
        await app.workers.wait_for_complete()
        assert all(c.status == "ok" for c in app.cells())
        assert show.output_text == "4 20\n" and z.output_text == "5\n"
        assert x.exec_count < show.exec_count and y.exec_count < show.exec_count

    ui(tmp_path, test, lazy=lazy)


def test_mutation_owner_still_reruns(tmp_path):
    cells = [CellSpec("items = []"), CellSpec("items.append(1)"), CellSpec("print(items)")]

    async def test(app, pilot):
        owner, mutate, show = app.cells()
        await run_all_clean(app)
        owner_count = owner.exec_count
        await run(app, mutate)
        assert owner.exec_count > owner_count
        assert show.output_text == "[1]\n"

    ui(tmp_path, test, cells=cells)


def test_restart_makes_ancestors_rerun(tmp_path):
    async def test(app, pilot):
        x, y, show, z = app.cells()
        await run_all_clean(app)
        await app.action_restart_kernel()
        assert x.status == "stale"
        await run(app, show)
        assert show.output_text == "1 10\n" and show.status == "ok"

    ui(tmp_path, test)
