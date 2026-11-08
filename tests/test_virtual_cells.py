"""A notebook over the mounted-cells budget keeps only a window of cells as widgets (docs/plans/12-virtual-cells.md)."""

import asyncio
import gc

from gpython import notebook
from gpython.app import GPython
from gpython.cell import Cell
from gpython.notebook import CellSpec
from gpython.pane import JUMP_WINDOW, MOUNT_BATCH, MOUNT_BUDGET

LONG = 300


def many_cells(n: int) -> list[CellSpec]:
    return [CellSpec(f"v{i} = {i}\nprint({i})" + ("\nneedle = True" if i == 200 else "")) for i in range(n)]


def write(tmp_path, n: int, markers: bool = True):
    path = tmp_path / "nb.py"
    notebook.save(path, many_cells(n), markers)
    return path


async def settle(app, pilot, rounds: int = 4) -> None:
    for _ in range(rounds):
        await pilot.pause()
        await app.workers.wait_for_complete()
    await pilot.pause()


def mounted(pane) -> list[int]:
    return [i for i, state in enumerate(pane.states) if state.widget is not None]


def run(tmp_path, test, n: int = LONG, markers: bool = True, size=(140, 45)):
    path = write(tmp_path, n, markers)

    async def main():
        app = GPython(path)
        async with app.run_test(size=size) as pilot:
            await app.loaded.wait()
            await settle(app, pilot)
            await test(app, pilot)

    asyncio.run(main())


def test_a_long_notebook_mounts_only_the_budget_but_knows_every_cell(tmp_path):
    async def test(app, pilot):
        pane = app.pane
        assert pane.cell_count() == LONG
        assert len(app.cells()) == MOUNT_BUDGET
        assert pane._window == (0, MOUNT_BUDGET)
        assert app.cells()[-1].label == "c100" and pane.states[-1].label == "c300"
        assert [c.state for c in app.cells()] == pane.states[:MOUNT_BUDGET]

    run(tmp_path, test)


def test_g_and_gg_move_the_window_to_the_ends(tmp_path):
    async def test(app, pilot):
        pane = app.pane
        app.cells()[0].focus()
        await pilot.pause()
        await pilot.press("G")
        await settle(app, pilot)
        last = pane.states[-1]
        assert app.focused is last.widget and last.widget.label == "c300"
        assert pane._window[1] == LONG and pane._window[0] >= LONG - MOUNT_BUDGET - MOUNT_BATCH
        assert pane.states[0].widget is None
        assert mounted(pane) == list(range(*pane._window))
        await pilot.press("g", "g")
        await settle(app, pilot)
        assert app.focused is pane.states[0].widget
        assert pane._window[0] == 0 and pane.states[-1].widget is None

    run(tmp_path, test)


def test_colon_number_and_search_reveal_far_cells(tmp_path):
    async def test(app, pilot):
        pane = app.pane
        app.cells()[0].focus()
        await pilot.pause()
        await app._run_ex("250")
        await settle(app, pilot)
        target = pane.states[249]
        assert app.focused in (target.widget, target.widget.editor) and target.widget.label == "c250"
        pane.select(target.widget, "cell")
        await pilot.pause()
        lo, hi = pane._window
        assert lo <= 249 < hi and hi - lo <= MOUNT_BUDGET + MOUNT_BATCH
        assert mounted(pane) == list(range(lo, hi))
        pane.search("needle", 1, "cell")
        await settle(app, pilot)
        found = pane.states[200]
        assert app.focused is found.widget and found.widget.label == "c201"
        pane.search("needle", 1, "normal")
        await settle(app, pilot)
        assert app.focused is found.widget.editor and found.widget.editor.cursor_location == (2, 0)

    run(tmp_path, test)


def test_run_by_number_reaches_an_unmounted_cell_and_its_output_shows_once_mounted(tmp_path):
    async def test(app, pilot):
        pane = app.pane
        await asyncio.wait_for(app.kernel.ready.wait(), 60)
        target = pane.states[249]
        assert target.widget is None
        assert await pane.run_number(250)
        await settle(app, pilot)
        assert target.widget is None and target.status == "ok" and target.output_text == "249\n" and target.exec_count == 1
        assert pane.goto_cell(250, "cell")
        await settle(app, pilot)
        widget = target.widget
        assert widget is not None and app.focused is widget
        assert widget.output_text == "249\n" and widget.status == "ok" and "[1]" in widget.header_text

    run(tmp_path, test)


def test_run_all_right_after_opening_runs_every_cell_mounted_or_not(tmp_path):
    path = write(tmp_path, LONG)

    async def main():
        app = GPython(path)
        async with app.run_test(size=(140, 45)) as pilot:
            assert not app.pane.loaded.is_set()
            app.action_run_all()
            await asyncio.wait_for(app.kernel.ready.wait(), 60)
            pane = app.pane
            for _ in range(300):
                await settle(app, pilot, 1)
                if all(s.status == "ok" for s in pane.states):
                    break
            assert [s.status for s in pane.states] == ["ok"] * LONG
            assert pane.states[-1].widget is None and pane.states[-1].output_text == "299\n"
            assert app.cells()[0].output_text == "0\n"
            pane.goto_cell(LONG, "cell")
            await settle(app, pilot)
            assert pane.states[-1].widget.output_text == "299\n"

    asyncio.run(main())


def test_an_edit_far_away_is_kept_through_unmounting_and_saved(tmp_path):
    async def test(app, pilot):
        pane = app.pane
        pane.goto_cell(250, "normal")
        await settle(app, pilot)
        target = pane.states[249]
        assert app.focused is target.widget.editor
        await pilot.press("i", "x", "escape")
        assert target.source.startswith("xv249")
        pane.goto_cell(1, "cell")
        await settle(app, pilot)
        assert target.widget is None and target.source.startswith("xv249")
        assert pane.dirty
        await app._run_ex("w")
        await settle(app, pilot)
        specs = notebook.load_cells(pane.path)[0]
        assert len(specs) == LONG and specs[249].source.startswith("xv249") and specs[299].source == "v299 = 299\nprint(299)"
        assert pane.copy_text().count("# %%") == LONG
        pane.goto_cell(250, "normal")
        await settle(app, pilot)
        await pilot.press("u")
        assert target.source == "v249 = 249\nprint(249)"

    run(tmp_path, test, markers=True)


def test_mounted_widgets_stay_bounded_while_jumping_around(tmp_path):
    async def test(app, pilot):
        pane = app.pane
        for command in ("300", "1", "150", "300", "75"):
            await app._run_ex(command)
            await settle(app, pilot)
            lo, hi = pane._window
            assert hi - lo <= MOUNT_BUDGET + MOUNT_BATCH
            assert mounted(pane) == list(range(lo, hi))
        for step in range(1, 6):
            await pilot.resize_terminal(140 + step, 45)
            await pilot.pause()
        for _ in range(3):
            gc.collect()
        states = set(pane.states)
        alive = [o for o in gc.get_objects() if isinstance(o, Cell) and o.state in states]
        assert len(alive) <= MOUNT_BUDGET + 2 * MOUNT_BATCH + JUMP_WINDOW

    run(tmp_path, test)


def test_scrolling_with_the_mouse_moves_the_window_too(tmp_path):
    async def test(app, pilot):
        pane = app.pane
        box = pane._cells_box
        app.cells()[0].focus()
        await pilot.pause()
        for _ in range(20):
            box.scroll_end(animate=False, immediate=True)
            await settle(app, pilot, 1)
        lo, hi = pane._window
        assert lo > 0 and hi - lo <= MOUNT_BUDGET + MOUNT_BATCH
        assert mounted(pane) == list(range(lo, hi))

    run(tmp_path, test)


def test_a_notebook_under_the_budget_never_unmounts(tmp_path):
    async def test(app, pilot):
        pane = app.pane
        widgets = [state.widget for state in pane.states]
        assert len(app.cells()) == 60 and None not in widgets
        app.cells()[0].focus()
        await pilot.pause()
        await pilot.press("G")
        await settle(app, pilot)
        await pilot.press("g", "g")
        await settle(app, pilot)
        await app._run_ex("40")
        await settle(app, pilot)
        assert [state.widget for state in pane.states] == widgets
        assert app.focused in (widgets[39], widgets[39].editor)

    run(tmp_path, test, n=60)


def test_delete_undo_and_counted_moves_work_across_the_window(tmp_path):
    async def test(app, pilot):
        pane = app.pane
        pane.goto_cell(250, "cell")
        await settle(app, pilot)
        await pilot.press("d", "d")
        await settle(app, pilot)
        assert pane.cell_count() == LONG - 1 and pane.states[249].label == "c250" and pane.states[249].source.startswith("v250 ")
        pane.goto_cell(1, "cell")
        await settle(app, pilot)
        await pilot.press("u")
        await settle(app, pilot)
        assert pane.cell_count() == LONG and pane.states[249].source.startswith("v249 ")
        assert app.focused is pane.states[249].widget
        pane.move_cell(-150)
        await settle(app, pilot)
        assert pane.states[99].source.startswith("v249 ") and pane.states[99].label == "c100"
        assert app.focused is pane.states[99].widget
        lo, hi = pane._window
        assert lo <= 99 < hi and mounted(pane) == list(range(lo, hi))
        await pilot.press("u")
        await settle(app, pilot)
        assert pane.states[249].source.startswith("v249 ") and app.focused is pane.states[249].widget
        assert mounted(pane) == list(range(*pane._window))

    run(tmp_path, test)


def test_ex_ranges_over_the_whole_notebook_reach_cells_off_screen(tmp_path):
    async def test(app, pilot):
        pane = app.pane
        far = pane.states[250]
        assert far.widget is None and "print(250)" in far.source
        app.pane.select(app.cells()[0], "cell")
        await app._run_ex("%s/print/show/")
        await settle(app, pilot)
        assert "show(250)" in far.source and far.widget is None
        assert "show(0)" in app.cells()[0].source and pane.dirty
        await app._run_ex("g/needle/s/True/False/")
        await settle(app, pilot)
        assert pane.states[200].source.endswith("needle = False")
        await app._run_ex("%>")
        await settle(app, pilot)
        assert far.source.startswith("    v250 = 250\n    show(250)")
        await app._run_ex("g/^    v2[0-9][0-9]/d")
        await settle(app, pilot)
        assert far.source == "    show(250)" and pane.states[199].source.startswith("    v199 = 199")
        await app._run_ex("w")
        await settle(app, pilot)
        saved = notebook.load_cells(pane.path)[0]
        assert "show(250)" in saved[250].source and "v250" not in saved[250].source
        pane.select_state(far, "cell")
        await settle(app, pilot)
        assert far.widget is not None and far.widget.source == "    show(250)"

    run(tmp_path, test)


def test_normal_over_the_whole_notebook_is_refused_for_cells_off_screen(tmp_path):
    async def test(app, pilot):
        app.pane.select(app.cells()[0], "cell")
        before = [state.source for state in app.pane.states]
        await app._run_ex("%normal A;")
        await settle(app, pilot)
        assert [state.source for state in app.pane.states] == before

    run(tmp_path, test)
