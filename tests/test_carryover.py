"""A .py notebook changed by another editor keeps the cells and the outputs that are still there."""

import asyncio

from gpython import notebook, sidecar
from gpython.app import GPython
from gpython.carryover import carried_states
from gpython.notebook import CellSpec, load_cells, save

STREAM = [{"output_type": "stream", "name": "stdout", "text": "kept\n"}]
CELLS = [
    CellSpec("# Notes", "markdown"),
    CellSpec("a = 1\nb = 2"),
    CellSpec("c = a + b\nprint(c)", hide_code=True),
    CellSpec("d = 4"),
]


def _saved(tmp_path, cells=CELLS):
    path = tmp_path / "nb.py"
    save(path, cells, markers=False)
    return path


def _shape(cells):
    return [(c.source, c.kind, c.hide_code) for c in cells]


def test_a_line_changed_in_a_cell_keeps_every_cell(tmp_path):
    path = _saved(tmp_path)
    path.write_text(path.read_text().replace("b = 2", "b = 20"))
    cells, markers = load_cells(path)
    assert not markers
    assert _shape(cells) == [("# Notes", "markdown", False), ("a = 1\nb = 20", "code", False), ("c = a + b\nprint(c)", "code", True), ("d = 4", "code", False)]


def test_a_blank_line_added_in_a_cell_does_not_split_it(tmp_path):
    path = _saved(tmp_path)
    path.write_text(path.read_text().replace("a = 1\nb = 2", "a = 1\n\nb = 2"))
    assert [c.source for c in load_cells(path)[0]][1] == "a = 1\n\nb = 2"


def test_code_added_at_the_end_becomes_new_cells(tmp_path):
    path = _saved(tmp_path)
    path.write_text(path.read_text() + "\n\ndef f():\n    return 1\n\n\ne = f()\n")
    assert [c.source for c in load_cells(path)[0]] == ["# Notes", "a = 1\nb = 2", "c = a + b\nprint(c)", "d = 4", "def f():\n    return 1", "e = f()"]


def test_a_line_added_right_after_a_cell_joins_it(tmp_path):
    path = _saved(tmp_path)
    path.write_text(path.read_text().replace("d = 4\n", "d = 4\ne = 5\n"))
    assert [c.source for c in load_cells(path)[0]][-1] == "d = 4\ne = 5"


def test_a_cell_deleted_elsewhere_is_gone_and_the_others_stay(tmp_path):
    path = _saved(tmp_path)
    path.write_text(path.read_text().replace("c = a + b\nprint(c)\n", ""))
    assert [c.source for c in load_cells(path)[0]] == ["# Notes", "a = 1\nb = 2", "d = 4"]


def test_the_layout_found_is_kept_and_the_file_text_is_what_save_writes(tmp_path):
    path = _saved(tmp_path)
    changed = path.read_text().replace("b = 2", "b = 20")
    path.write_text(changed)
    cells, markers = load_cells(path)
    assert sidecar.layout_file(changed, path).exists()
    assert notebook.file_text(path, cells, markers) != "" and notebook.dump_script_with_layout(cells)[0] == changed


def test_a_cell_edge_inside_a_statement_splits_the_file_afresh(tmp_path):
    path = _saved(tmp_path, [CellSpec("a = 1"), CellSpec("b = 2")])
    path.write_text("a = (\n\n    2)\n")
    assert [c.source for c in load_cells(path)[0]] == ["a = (\n\n    2)"]


def test_a_path_never_saved_by_gpython_is_split_as_before(tmp_path):
    path = tmp_path / "nb.py"
    path.write_text("a = 1\n\nb = 2\n")
    assert [c.source for c in load_cells(path)[0]] == ["a = 1", "b = 2"]


def _ran(cells):
    return [notebook.with_run_state(c, {"outputs": STREAM, "execution_count": i + 1, "duration": 0.5}) if c.kind == "code" else c for i, c in enumerate(cells)]


def test_outputs_stay_on_unchanged_cells_and_are_outdated_on_the_changed_cell(tmp_path):
    path = _saved(tmp_path, _ran(CELLS))
    path.write_text(path.read_text().replace("b = 2", "b = 20"))
    cells, markers = load_cells(path)
    states = notebook.load_outputs(path, cells, markers)
    assert states[0] == {}
    assert states[1]["outputs"] == STREAM and states[1]["outdated"] is True
    assert states[2]["outputs"] == STREAM and "outdated" not in states[2]
    assert states[3]["execution_count"] == 4 and "outdated" not in states[3]


def test_outputs_of_a_marker_file_changed_elsewhere_are_kept(tmp_path):
    path = tmp_path / "nb.py"
    path.write_text("# %%\na = 1\n\n# %%\nprint(a)\n")
    cells, markers = load_cells(path)
    assert notebook.remember_outputs(path, _ran(cells), markers)
    path.write_text("# %%\na = 1\n\n# %%\nz = 0\n\n# %%\nprint(a + 1)\n")
    cells, markers = load_cells(path)
    states = notebook.load_outputs(path, cells, markers)
    assert [bool(s) for s in states] == [True, False, True]
    assert "outdated" not in states[0] and states[2]["outdated"] is True


def test_a_new_cell_takes_no_output_from_a_cell_not_like_it():
    states = [{"execution_count": 1}, {"execution_count": 2}, {"execution_count": 3}]
    carried = carried_states(["a = 1", "total = sum(values)", "c = 3"], states, ["a = 1", "import os", "total = sum(values) + 1", "c = 3"])
    assert carried == [{"execution_count": 1}, {}, {"execution_count": 2, "outdated": True}, {"execution_count": 3}]
    assert carried_states(["a = 1"], [{}], ["a = 1"]) is None


def _open_then(path, steps):
    async def session():
        app = GPython(path)
        async with app.run_test(size=(120, 40)) as pilot:
            await app.loaded.wait()
            await pilot.pause()
            await steps(app, pilot)

    asyncio.run(session())


def test_a_changed_cell_and_the_cells_that_use_it_show_their_old_output_as_stale(tmp_path):
    path = _saved(tmp_path, _ran(CELLS))
    path.write_text(path.read_text().replace("b = 2", "b = 20"))

    async def look(app, pilot):
        _, changed, uses_it, apart = app.cells()
        assert [cell.output_text.strip() for cell in (changed, uses_it, apart)] == ["kept", "kept", "kept"]
        assert [cell.status for cell in (changed, uses_it, apart)] == ["stale", "stale", "idle"]

    _open_then(path, look)


def test_an_output_from_code_edited_and_saved_without_a_run_is_stale_when_opened_again(tmp_path):
    path = _saved(tmp_path, [CellSpec("print('kept')"), CellSpec("z = 3")])

    async def run_edit_save(app, pilot):
        await asyncio.wait_for(app.kernel.ready.wait(), 60)
        await app._run_ex("run")
        await app.workers.wait_for_complete()
        await pilot.pause()
        first = app.cells()[0]
        first.editor.text = "print('kept')  "
        await pilot.pause()
        await app._run_ex("w")
        await pilot.pause()

    async def reopened(app, pilot):
        first, second = app.cells()
        assert first.output_text.strip() == "kept" and first.status == "stale"
        assert second.status == "idle" and second.exec_count == 2

    _open_then(path, run_edit_save)
    _open_then(path, reopened)


def test_a_restart_does_not_make_the_stored_outputs_outdated(tmp_path):
    path = _saved(tmp_path, [CellSpec("print('kept')")])

    async def run_restart(app, pilot):
        await asyncio.wait_for(app.kernel.ready.wait(), 60)
        await app._run_ex("run")
        await app.workers.wait_for_complete()
        await pilot.pause()
        await app._run_ex("restart")
        await app.workers.wait_for_complete()
        await pilot.pause()
        app.pane.outputs_changed()

    async def reopened(app, pilot):
        (first,) = app.cells()
        assert first.output_text.strip() == "kept" and first.status == "idle"

    _open_then(path, run_restart)
    _open_then(path, reopened)
