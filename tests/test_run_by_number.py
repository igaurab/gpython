"""Run a cell by its number: r N in cell mode and in vim normal mode (space r N too), :run N."""

import asyncio

import pytest

from gpython import notebook, runnumber
from gpython.app import GPython
from gpython.notebook import CellSpec
from gpython.pane import needs_run
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
        assert app._number_sequence is not None
        await pilot.press("3")
        await settle(app, pilot)
        assert show.output_text == "3\n" and show.exec_count > 0
        assert first.status == "ok"
        assert app.focused is last and last.exec_count == 0
        assert app._number_sequence is None and "CELL" in status(app)

    ui(tmp_path, test, kernel=True)


def test_status_bar_while_pending(tmp_path):
    async def test(app, pilot):
        app.cells()[0].focus()
        await pilot.press("r")
        await pilot.pause()
        text = status(app)
        assert "run cell: c_" in text and "number · r/c this · C to here · a after · A all · s stale" in text

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
        assert app._number_sequence is not None
        await asyncio.sleep(0.4)
        await pilot.pause()
        assert app._number_sequence is None
        assert "run cell" not in status(app) and "CELL" in status(app)
        await pilot.press("j")
        assert app.focused is app.cells()[1]

    ui(tmp_path, test)


def test_escape_and_other_keys_cancel(tmp_path):
    async def test(app, pilot):
        cells = app.cells()
        cells[0].focus()
        await pilot.press("r", "escape")
        assert app._number_sequence is None and "run cell" not in status(app)
        await pilot.press("r", "x")
        assert app._number_sequence is None and len(app.cells()) == len(cells)
        assert all(c.exec_count == 0 for c in app.cells())

    ui(tmp_path, test)


def test_multi_digit_waits_until_unambiguous(tmp_path, monkeypatch):
    monkeypatch.setattr(runnumber, "PAUSE", 30)

    async def test(app, pilot):
        cells = app.cells()
        cells[0].focus()
        await pilot.press("r", "1")
        await pilot.pause()
        assert app._number_sequence is not None
        await pilot.press("2")
        await settle(app, pilot)
        assert app._number_sequence is None
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
        assert app._number_sequence is None and cells[1].output_text == "cell 2\n"

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
        assert app._number_sequence is None and cells[0].output_text == "cell 1\n"

    ui(tmp_path, test, cells=many_cells(12), kernel=True)


def test_unknown_cell_number_warns_and_cancels(tmp_path):
    async def test(app, pilot):
        app.cells()[0].focus()
        await pilot.press("r", "9")
        await pilot.pause()
        assert app._number_sequence is None
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


def test_r_from_vim_normal_mode_runs_without_editing(tmp_path):
    async def test(app, pilot):
        first, _, show, last = app.cells()
        app.select(last, "normal")
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        assert "run cell: c_" in status(app)
        await pilot.press("3")
        await settle(app, pilot)
        assert show.output_text == "3\n"
        await pilot.press("r", "C")
        await settle(app, pilot)
        assert first.status == last.status == "ok" and last.exec_count > 0
        assert app.focused is last.editor and VimTextArea.mode == "normal"
        assert last.source == "b = 4"

    ui(tmp_path, test, kernel=True)


def test_r_then_an_unknown_letter_in_normal_mode_cancels_and_edits_nothing(tmp_path):
    async def test(app, pilot):
        last = app.cells()[-1]
        app.select(last, "normal")
        await pilot.pause()
        await pilot.press("r", "x")
        await pilot.pause()
        assert app._number_sequence is None
        assert last.source == "b = 4" and VimTextArea.mode == "normal"

    ui(tmp_path, test)


def test_insert_mode_types_r_and_space(tmp_path):
    async def test(app, pilot):
        last = app.cells()[-1]
        app.select(last, "insert")
        last.editor.move_cursor((0, 5))
        await pilot.press("space", "r", "3")
        await pilot.pause()
        assert app._number_sequence is None
        assert last.source == "b = 4 r3"

    ui(tmp_path, test)


def test_ex_number_sequence(tmp_path):
    async def test(app, pilot):
        cells = app.cells()
        cells[3].focus()
        await pilot.press(":", *"run 3", "enter")
        await settle(app, pilot)
        assert cells[2].output_text == "3\n" and app.focused is cells[3]

    ui(tmp_path, test, kernel=True)


CHAIN = [CellSpec("a = 1"), CellSpec("b = a + 1\nprint(b)"), CellSpec("print(a, b)"), CellSpec("c = 9")]


def test_r_A_runs_every_cell(tmp_path):
    async def test(app, pilot):
        cells = app.cells()
        cells[2].focus()
        await pilot.press("r", "A")
        await settle(app, pilot)
        assert app._number_sequence is None
        assert [c.status for c in cells] == ["ok"] * 4
        assert cells[2].output_text == "1 2\n" and cells[3].exec_count == 4
        assert app.focused is cells[2]

    ui(tmp_path, test, cells=CHAIN, kernel=True)


def test_r_C_runs_down_to_the_current_cell_and_leaves_the_rest(tmp_path):
    async def test(app, pilot):
        first, second, reader, last = app.cells()
        second.focus()
        await pilot.press("r", "C")
        await settle(app, pilot)
        assert app._number_sequence is None
        assert first.status == second.status == "ok" and second.output_text == "2\n"
        assert reader.status == "stale" and reader.exec_count == 0
        assert last.status == "idle" and last.exec_count == 0
        assert app.focused is second

    ui(tmp_path, test, cells=CHAIN, kernel=True)


def test_space_r_C_from_vim_normal_mode(tmp_path):
    async def test(app, pilot):
        first, second, reader, last = app.cells()
        app.select(second, "normal")
        await pilot.pause()
        await pilot.press("space", "r", "C")
        await settle(app, pilot)
        assert first.status == second.status == "ok" and second.output_text == "2\n"
        assert reader.exec_count == 0 and last.exec_count == 0
        assert app.focused is second.editor and VimTextArea.mode == "normal"
        assert second.source == "b = a + 1\nprint(b)"

    ui(tmp_path, test, cells=CHAIN, kernel=True)


def test_a_and_c_after_a_digit_cancel(tmp_path, monkeypatch):
    monkeypatch.setattr(runnumber, "PAUSE", 30)

    async def test(app, pilot):
        cells = app.cells()
        cells[0].focus()
        for key in ("a", "c"):
            await pilot.press("r", "1")
            await pilot.pause()
            assert app._number_sequence is not None and "enter runs · esc cancels" in status(app)
            await pilot.press(key)
            await settle(app, pilot)
            assert app._number_sequence is None and "run cell" not in status(app)
        assert all(c.exec_count == 0 and c.status == "idle" for c in cells)
        assert app.focused is cells[0]

    ui(tmp_path, test, cells=many_cells(12))


def test_r_r_and_r_c_run_only_the_current_cell_and_stay(tmp_path):
    async def test(app, pilot):
        first, second, reader, last = app.cells()
        for key in ("r", "c"):
            last.focus()
            await pilot.press("r", key)
            await settle(app, pilot)
            assert app._number_sequence is None and app.focused is last
            assert last.status == "ok" and last.exec_count >= 1
            assert first.exec_count == 0 and second.exec_count == 0 and reader.exec_count == 0
        assert last.exec_count == 2

    ui(tmp_path, test, cells=CHAIN, kernel=True)


def test_r_a_runs_the_cells_after_the_current_one(tmp_path):
    async def test(app, pilot):
        first, second, reader, last = app.cells()
        first.focus()
        await pilot.press("r", "a")
        await settle(app, pilot)
        assert app._number_sequence is None and app.focused is first
        assert first.status == "ok" and first.exec_count >= 1
        assert second.output_text == "2\n" and reader.output_text == "1 2\n" and last.status == "ok"
        before = {c: c.exec_count for c in (first, second, reader, last)}
        reader.focus()
        await pilot.press("r", "a")
        await settle(app, pilot)
        assert last.exec_count > before[last]
        assert all(c.exec_count == before[c] for c in (first, second, reader))

    ui(tmp_path, test, cells=CHAIN, kernel=True)


def test_r_s_runs_the_stale_cells(tmp_path):
    async def test(app, pilot):
        first, second, reader, last = app.cells()
        last.focus()
        await pilot.press("r", "A")
        await settle(app, pilot)
        first.editor.load_text("a = 5")
        await pilot.pause()
        assert needs_run(first) and not needs_run(last)
        last_count = last.exec_count
        second.focus()
        await pilot.press("r", "s")
        await settle(app, pilot)
        assert app.focused is second
        assert not needs_run(first) and second.output_text == "6\n" and reader.output_text == "5 6\n"
        assert last.exec_count == last_count

    ui(tmp_path, test, cells=CHAIN, kernel=True)


def test_r_k_R_i_reach_the_kernel(tmp_path, monkeypatch):
    calls = []

    async def test(app, pilot):
        pane = app.pane

        async def restart_kernel():
            calls.append("restart")

        async def interrupt():
            calls.append("interrupt")

        monkeypatch.setattr(pane, "restart_kernel", restart_kernel)
        monkeypatch.setattr(pane, "interrupt", interrupt)
        monkeypatch.setattr(pane, "run_all", lambda: calls.append("run_all"))
        cells = app.cells()
        cells[1].focus()
        for key in ("k", "i", "R"):
            await pilot.press("r", key)
            await settle(app, pilot)
            assert app._number_sequence is None and app.focused is cells[1]
        assert calls == ["restart", "interrupt", "restart", "run_all"]

    ui(tmp_path, test, cells=CHAIN)


def test_r_t_times_the_cell_over_n_runs(tmp_path):
    async def test(app, pilot):
        first, _, show, last = app.cells()
        last.focus()
        await pilot.press("r", "t")
        assert isinstance(app._number_sequence, runnumber.TimeRuns)
        assert "TIME" in status(app)
        await pilot.press("3", "enter")
        assert app._number_sequence is None
        await settle(app, pilot)
        assert last.status == "ok" and app.focused is last
        assert "3 runs" in last.output_text and "mean" in last.output_text and "p90" in last.output_text
        await pilot.press(":", *"timeit", "enter")
        await settle(app, pilot)
        assert "1 run:" in last.output_text
        first.editor.focus()
        await pilot.press("r", "t", "2", "enter")
        await settle(app, pilot)
        assert "2 runs" in first.output_text and "p99" in first.output_text and app.focused is first.editor

    ui(tmp_path, test, kernel=True)


def test_time_summary():
    from gpython.cell import percentile, time_brief, time_stats, time_summary

    assert time_summary([0.5]) == "1 run: 500ms"
    stats = time_stats([1.0, 2.0, 6.0])
    assert (stats["mean"], stats["median"], stats["min"], stats["max"]) == (3.0, 2.0, 1.0, 6.0)
    assert stats["p50"] == 2.0 and round(stats["p90"], 2) == 5.2 and round(stats["p99"], 2) == 5.92
    assert round(stats["std"], 3) == 2.646 and stats["variance"] == 7.0
    assert percentile([1.0, 2.0, 6.0], 0) == 1.0 and percentile([1.0, 2.0, 6.0], 100) == 6.0
    table = time_summary([1.0, 2.0, 6.0]).splitlines()
    assert table[0] == "3 runs" and len(table) == 6
    assert table[1].startswith("┌") and table[3].startswith("├") and table[5].startswith("└")
    assert [c.strip() for c in table[2].strip("│").split("│")] == ["mean", "median", "std", "var", "p50", "p90", "p99", "min", "max"]
    assert [c.strip() for c in table[4].strip("│").split("│")] == ["3.00s", "2.00s", "2.65s", "7s²", "2.00s", "5.20s", "5.92s", "1.00s", "6.00s"]
    assert len({len(line) for line in table[1:]}) == 1
    assert "5.7ms²" in time_summary([0.003, 0.004, 0.004, 0.009, 0.006]).splitlines()[4]
    assert time_brief([1.0, 2.0, 6.0]) == "3 runs: mean 3.00s · median 2.00s · p90 5.20s"
