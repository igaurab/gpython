"""Lazy is the default run mode: a run never runs the cells below, it marks them stale.

The user runs those with r N or f4. f3 / :set lazy|nolazy switch to reactive and the
choice is saved in the config.
"""

import asyncio

from gpython import config, notebook
from gpython.app import GPython
from gpython.notebook import CellSpec

CELLS = [CellSpec("x = 1"), CellSpec("print(x * 10)")]


def ui(tmp_path, test, cells=CELLS, kernel=True):
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


async def settle(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


def status_bar(app) -> str:
    return str(app.query_one("#status").render())


def test_lazy_by_default_a_run_leaves_the_dependent_stale(tmp_path):
    async def test(app, pilot):
        assert app.lazy and "lazy" in status_bar(app)
        x, show = app.cells()
        x.focus()
        await pilot.press("ctrl+enter")
        await settle(app, pilot)
        assert x.status == "ok"
        assert show.status == "stale" and show.output_text == "" and show.exec_count == 0

    ui(tmp_path, test)


def test_r_number_then_runs_the_dependent_with_the_latest_code(tmp_path):
    async def test(app, pilot):
        x, show = app.cells()
        x.focus()
        await pilot.press("ctrl+enter")
        await settle(app, pilot)
        await pilot.press("r", "2")
        await settle(app, pilot)
        assert show.status == "ok" and show.output_text == "10\n"
        assert x.exec_count == 1 and show.exec_count == 2

    ui(tmp_path, test)


def test_editing_the_ancestor_then_r_number_runs_the_ancestor_first(tmp_path):
    async def test(app, pilot):
        x, show = app.cells()
        await pilot.press("f5")
        await settle(app, pilot)
        assert show.output_text == "10\n"
        x.editor.text = "x = 2"
        await pilot.pause()
        assert x.status == "stale"
        show.focus()
        await pilot.press("r", "2")
        await settle(app, pilot)
        assert show.output_text == "20\n"
        assert x.status == show.status == "ok"
        assert 2 < x.exec_count < show.exec_count

    ui(tmp_path, test)


def test_shift_enter_and_run_by_number_in_normal_mode_keep_dependents_stale(tmp_path):
    async def test(app, pilot):
        x, show = app.cells()
        x.editor.focus()
        await pilot.press("shift+enter")
        await settle(app, pilot)
        assert x.status == "ok" and show.status == "stale"
        x.editor.focus()
        await pilot.press("space", "r", "1")
        await settle(app, pilot)
        assert x.exec_count == 2 and show.status == "stale" and show.exec_count == 0

    ui(tmp_path, test)


def test_f3_switches_to_reactive_and_saves_it(tmp_path):
    async def test(app, pilot):
        x, show = app.cells()
        await pilot.press("f3")
        await pilot.pause()
        assert not app.lazy and "reactive" in status_bar(app)
        assert config.load()["run_mode"] == "reactive"
        x.focus()
        await pilot.press("ctrl+enter")
        await settle(app, pilot)
        assert show.status == "ok" and show.output_text == "10\n"
        await pilot.press("f3")
        await pilot.pause()
        assert app.lazy and "lazy" in status_bar(app)
        assert config.load()["run_mode"] == "lazy"

    ui(tmp_path, test)


def test_set_lazy_nolazy_and_lazy_command(tmp_path):
    async def test(app, pilot):
        app._ex_set("nolazy")
        assert not app.lazy and config.load()["run_mode"] == "reactive"
        app._ex_set("lazy")
        assert app.lazy and config.load()["run_mode"] == "lazy"
        app.action_toggle_lazy()
        assert not app.lazy and config.load()["run_mode"] == "reactive"

    ui(tmp_path, test, kernel=False)


def test_run_mode_persists_across_restarts(tmp_path):
    async def first(app, pilot):
        assert app.lazy
        await pilot.press("f3")
        await pilot.pause()
        assert not app.lazy

    async def second(app, pilot):
        assert not app.lazy and "reactive" in status_bar(app)
        app._ex_set("lazy")

    async def third(app, pilot):
        assert app.lazy and "lazy" in status_bar(app)

    ui(tmp_path, first, kernel=False)
    assert config.load()["run_mode"] == "reactive"
    ui(tmp_path, second, kernel=False)
    assert config.load()["run_mode"] == "lazy"
    ui(tmp_path, third, kernel=False)


def test_deleting_a_cell_marks_its_readers_stale_when_lazy(tmp_path):
    async def test(app, pilot):
        x, show = app.cells()
        await pilot.press("f5")
        await settle(app, pilot)
        assert show.output_text == "10\n"
        x.focus()
        await pilot.press("ctrl+d")
        await settle(app, pilot)
        assert [c.source for c in app.cells()] == ["print(x * 10)"]
        assert show.status == "stale" and show.output_text == "10\n"

    ui(tmp_path, test)


def test_restarts_read_an_unset_mode_as_lazy(tmp_path):
    config.save({"theme": "nord"})

    async def test(app, pilot):
        assert app.lazy

    ui(tmp_path, test, kernel=False)
