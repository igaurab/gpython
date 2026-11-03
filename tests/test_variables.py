"""The variables panel (ctrl+v, :vars) and the debugger shown in it, with a real kernel."""

import asyncio

from gpython import notebook
from gpython.app import GPython
from gpython.notebook import CellSpec
from gpython.variables import InputPrompt, TimelineScreen, ValueScreen, VariablesPanel
from gpython.vim import DEBUG_LINE_STYLE

CELLS = [
    CellSpec("import json\nnums = [1, 3, 5, 7]\nlabel = 'abc'"),
    CellSpec("def double(x):\n    return 2 * x"),
    CellSpec("total = 0\nbreakpoint()\nfor i in range(len(nums)):\n    total += nums[i]\nprint(total)"),
    CellSpec("acc = 0\nfor k in range(len(nums) + 1):\n    acc += nums[k]"),
]


def ui(tmp_path, test, cells=CELLS, kernel=True):
    path = tmp_path / "nb.py"
    notebook.save(path, cells)

    async def main():
        app = GPython(path)
        async with app.run_test(size=(170, 50)) as pilot:
            await app.loaded.wait()
            if kernel:
                await asyncio.wait_for(app.kernel.ready.wait(), 60)
            await pilot.pause()
            await test(app, pilot)

    asyncio.run(asyncio.wait_for(main(), 150))


async def until(pilot, condition, seconds=20):
    for _ in range(int(seconds * 20)):
        if condition():
            return True
        await pilot.pause(0.05)
    raise AssertionError("condition never became true")


async def run(app, pilot, n):
    cell = app.cells()[n - 1]
    app.pane.select(cell, "cell")
    await pilot.press("r", "r")
    return cell


def rows(panel: VariablesPanel) -> list[str]:
    return list(panel._names)


async def stopped(app, pilot):
    await until(pilot, lambda: app.pane.debug is not None and not app.pane.debug.running)
    return app.pane.debug


def test_ctrl_v_builds_the_panel_on_first_use_and_lists_the_variables(tmp_path):
    async def test(app, pilot):
        assert not app.query(VariablesPanel) and not app.pane.variables_visible
        await pilot.press("ctrl+v")
        await pilot.pause()
        panel = app.query_one(VariablesPanel)
        assert app.pane.variables_visible and rows(panel) == []
        await run(app, pilot, 1)
        await until(pilot, lambda: rows(panel) == ["label", "nums"])
        assert panel.rows == [
            ("json", "module", "", "", "module", "c1"),
            ("label", "str", "'abc'", "len 3", "data", "c1"),
            ("nums", "list", "[1, 3, 5, 7]", "len 4", "data", "c1"),
        ]
        assert "1 hidden" in str(panel.title.render())
        await pilot.press("ctrl+w", "l")
        await pilot.pause()
        assert app.focused is panel.table and app.ui_mode == "vars"
        await pilot.press("a")
        assert rows(panel) == ["json", "label", "nums"]
        await pilot.press("slash")
        await pilot.press(*"list", "enter")
        await pilot.pause()
        assert rows(panel) == ["nums"]
        await pilot.press("slash", "ctrl+u", "enter")
        await pilot.pause()
        await pilot.press("ctrl+v")
        await pilot.pause()
        assert not app.pane.variables_visible
        await app._run_ex("vars")
        await pilot.pause()
        assert app.pane.variables_visible and app.query_one(VariablesPanel) is panel

    ui(tmp_path, test)


def test_enter_shows_the_value_g_jumps_and_d_deletes(tmp_path):
    async def test(app, pilot):
        await pilot.press("ctrl+v")
        c1 = await run(app, pilot, 1)
        panel = app.query_one(VariablesPanel)
        await until(pilot, lambda: rows(panel) == ["label", "nums"])
        panel.table.focus()
        panel.table.move_cursor(row=1)
        await pilot.press("enter")
        await until(pilot, lambda: isinstance(app.screen, ValueScreen))
        assert app.screen.value == {"kind": "text", "text": "[1, 3, 5, 7]"}
        await pilot.press("q")
        await pilot.pause()
        await pilot.press("g")
        await pilot.pause()
        assert app.focused is c1.editor and c1.editor.cursor_location == (1, 0)
        await pilot.press("ctrl+w", "l")
        await pilot.pause()
        panel.table.move_cursor(row=1)
        await pilot.press("d")
        await until(pilot, lambda: rows(panel) == ["label"])
        assert await app.kernel.variables({"nums"}) == [] and c1.status == "stale"

    ui(tmp_path, test)


def test_ctrl_v_in_insert_mode_stays_in_the_editor_and_ctrl_w_ctrl_v_splits(tmp_path):
    async def test(app, pilot):
        c1 = app.cells()[0]
        c1.editor.focus()
        await pilot.press("i", "ctrl+v")
        await pilot.pause()
        assert not app.pane.variables_visible and app.ui_mode == "insert"
        await pilot.press("escape", "escape")
        panes = len(app.workspace.tab_panes())
        await pilot.press("ctrl+w", "ctrl+v")
        await pilot.pause()
        assert len(app.workspace.tab_panes()) == panes + 1 and not app.pane.variables_visible

    ui(tmp_path, test, kernel=False)


def test_outline_and_variables_stack_in_the_right_column(tmp_path):
    async def test(app, pilot):
        from gpython.outline import Outline

        await pilot.press("ctrl+v")
        await pilot.press("ctrl+o")
        await pilot.pause()
        side = app.query_one("#side")
        outline, panel = app.query_one(Outline), app.query_one(VariablesPanel)
        assert list(side.children) == [outline, panel] and outline.region.y < panel.region.y
        await pilot.press("ctrl+w", "l")
        await pilot.pause()
        assert app.focused is outline.tree_view
        await pilot.press("ctrl+w", "j")
        await pilot.pause()
        assert app.focused is panel.table
        await pilot.press("ctrl+w", "k")
        await pilot.pause()
        assert app.focused is outline.tree_view
        await pilot.press("ctrl+w", "h")
        await pilot.pause()
        assert app.focused not in (outline.tree_view, panel.table)
        await pilot.press("ctrl+o", "ctrl+v")
        await pilot.pause()
        assert not side.display

    ui(tmp_path, test, kernel=False)


def test_input_in_a_cell_asks_at_the_bottom_and_sends_the_answer(tmp_path):
    cells = [CellSpec("name = input('who? ')\nprint('hi', name)")]

    async def test(app, pilot):
        cell = await run(app, pilot, 1)
        await until(pilot, lambda: isinstance(app.screen, InputPrompt))
        assert app.screen.prompt == "who? "
        await pilot.press(*"ada", "enter")
        await until(pilot, lambda: "hi ada" in cell.output_text)
        assert cell.run_status == "ok"

    ui(tmp_path, test, cells)


def test_a_breakpoint_pauses_in_the_panel_and_steps_through_the_loop(tmp_path):
    async def test(app, pilot):
        await run(app, pilot, 1)
        await until(pilot, lambda: app.pane._var_rows)
        c3 = await run(app, pilot, 3)
        session = await stopped(app, pilot)
        panel = app.query_one(VariablesPanel)
        assert panel.mode == "debug" and app.focused is panel.table and app.ui_mode == "debug"
        assert app.query_one("#side").has_class("-wide")
        assert c3.editor.debug_line == 1 and app.pane._debug_line == (c3.cid, 2)
        line = c3.editor.get_line(1)
        assert any(span.style == DEBUG_LINE_STYLE for span in line.spans)
        assert "DEBUG" in str(app._status.render())
        for key in ("n", "n"):
            await pilot.press(key)
            await stopped(app, pilot)
        assert session.record["line"] == 4 and session.record["loops"][0]["k"] == 1
        await pilot.press("right_square_bracket")
        await stopped(app, pilot)
        await pilot.press("right_square_bracket")
        await stopped(app, pilot)
        assert session.record["loops"][0]["k"] == 3 and dict(session.record["vars"])["i"] == "2"
        assert session.history_of("total")[-3:] == ["0", "1", "4"]
        assert "4" in str(panel.table.get_row("total")[2])
        picture = str(panel.picture.render())
        assert "for i in range(len(nums))" in picture and "3/4" in picture and "↑i" in picture
        assert "full · " in str(panel.footer.render()) and "MB / 200 MB" in str(panel.footer.render())
        name_cell = panel.table.get_row("total")[0]
        assert name_cell.style == "bold black on yellow"
        await pilot.press("h")
        await pilot.pause()
        assert not session.live and "⏪" in str(panel.title.render())
        await pilot.press("l", "l", "l", "l", "l", "l", "l", "l")
        await pilot.pause()
        assert session.live
        await pilot.press("T")
        await until(pilot, lambda: isinstance(app.screen, TimelineScreen))
        assert [node.k for node in app.screen.roots] == [1, 2, 3]
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        await pilot.press(*"b 5", "enter")
        await until(pilot, lambda: c3.editor.debug_breaks == {4})
        await pilot.press("right_parenthesis")
        await stopped(app, pilot)
        assert session.record["line"] == 5 and c3.editor.debug_line == 4
        await pilot.press("c")
        await until(pilot, lambda: app.pane.debug is None and c3.run_status == "ok")
        assert c3.editor.debug_breaks == frozenset()
        assert "16" in c3.output_text and panel.mode == "vars" and c3.editor.debug_line is None
        assert not app.query_one("#side").has_class("-wide")

    ui(tmp_path, test)


def test_quit_while_paused_stops_the_cell(tmp_path):
    async def test(app, pilot):
        await run(app, pilot, 1)
        await until(pilot, lambda: app.pane._var_rows)
        c3 = await run(app, pilot, 3)
        await stopped(app, pilot)
        await pilot.press("q")
        await until(pilot, lambda: app.pane.debug is None and c3.status != "running")
        assert "16" not in c3.output_text and app.pane.kernel_state == "kernel ready"
        c1 = await run(app, pilot, 1)
        await until(pilot, lambda: c1.run_status == "ok" and not app.pane._run_lock.locked())

    ui(tmp_path, test)


def test_interrupt_and_restart_while_paused_do_not_get_stuck(tmp_path):
    async def test(app, pilot):
        await run(app, pilot, 1)
        await until(pilot, lambda: app.pane._var_rows)
        c3 = await run(app, pilot, 3)
        await stopped(app, pilot)
        await app.pane.interrupt()
        await until(pilot, lambda: app.pane.debug is None and c3.run_status == "error")
        await run(app, pilot, 3)
        await stopped(app, pilot)
        app.pane.run_worker(app.pane.restart_kernel(), group="kernel")
        await until(pilot, lambda: app.pane.debug is None and app.pane.kernel_state == "kernel ready", 40)
        await until(pilot, lambda: not app.pane._run_lock.locked())
        c1 = await run(app, pilot, 1)
        await until(pilot, lambda: c1.run_status == "ok" and not app.pane._run_lock.locked(), 40)

    ui(tmp_path, test)


def test_an_index_error_offers_post_mortem_with_the_pointer_past_the_end(tmp_path):
    async def test(app, pilot):
        await run(app, pilot, 1)
        await until(pilot, lambda: app.pane._var_rows)
        c4 = await run(app, pilot, 4)
        await until(pilot, lambda: c4.run_status == "error" and not app.pane._run_lock.locked())
        assert "r d opens the debugger where it failed" in c4.output_text
        app.pane.select(c4, "cell")
        await pilot.press("r", "d")
        session = await stopped(app, pilot)
        panel = app.query_one(VariablesPanel)
        assert session.payload["postmortem"] and session.record["line"] == 3
        assert "POST-MORTEM" in str(panel.title.render())
        picture = str(panel.picture.render())
        assert "5/5" in picture and "⚠ k = 4 is past the end of nums (len 4)" in picture
        await pilot.press("n")
        await pilot.pause()
        assert app.pane.debug is not None
        await pilot.press("q")
        await until(pilot, lambda: app.pane.debug is None and not app.pane._run_lock.locked())

    ui(tmp_path, test)


def test_r_d_runs_a_clean_cell_paused_on_its_first_line(tmp_path):
    async def test(app, pilot):
        await run(app, pilot, 1)
        await until(pilot, lambda: app.pane._var_rows)
        c2 = app.cells()[1]
        app.pane.select(c2, "cell")
        await pilot.press("r", "d")
        session = await stopped(app, pilot)
        assert session.record["cell"] == c2.cid and session.record["line"] == 1
        await pilot.press("c")
        await until(pilot, lambda: app.pane.debug is None and c2.run_status == "ok")

    ui(tmp_path, test)


def test_set_debug_options_are_saved_and_explained(tmp_path):
    async def test(app, pilot):
        from gpython import config

        async def ex(command):
            await app._run_ex(command)
            await pilot.pause()
            return [n.message for n in app._notifications][-1]

        assert await ex("set debug_history?") == "debug_history = 200 MB (from the full preset)"
        assert "next recording" in await ex("set debug_detail=balanced")
        assert config.load()["debug_detail"] == "balanced"
        assert await ex("set debug_history?") == "debug_history = 20 MB (from the balanced preset)"
        await ex("set debug_history=1GB")
        assert await ex("set debug_history?") == "debug_history = 1024 MB (override)"
        assert "Not a value" in await ex("set debug_thinning=sideways")
        assert "one of: full, balanced, lean" in await ex("set debug_detail=huge")

    ui(tmp_path, test, kernel=False)


def test_ctrl_v_after_a_run_lists_the_variables_at_once(tmp_path):
    async def test(app, pilot):
        await run(app, pilot, 1)
        await until(pilot, lambda: app.pane._var_rows)
        await pilot.press("ctrl+v")
        panel = app.query_one(VariablesPanel)
        await until(pilot, lambda: rows(panel) == ["label", "nums"])
        assert "Variables" in str(panel.title.render()) and "g go to cell" in str(panel.footer.render())

    ui(tmp_path, test)
