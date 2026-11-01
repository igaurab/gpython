"""Plain .py notebooks are saved as ordinary code; their cell layout lives in ~/.gpython."""

import asyncio

from gpython import notebook, sidecar
from gpython.app import GPython
from gpython.notebook import CellSpec, load_cells, save


CELLS = [
    CellSpec("# Notes\n\nSome *text*", "markdown"),
    CellSpec("import os\nimport sys"),
    CellSpec("x = 1\ny = 2", hide_code=True),
    CellSpec(""),
    CellSpec("print(x + y)"),
]


def test_plain_script_has_no_markers_and_keeps_its_cells(tmp_path):
    path = tmp_path / "nb.py"
    save(path, CELLS, markers=False)
    text = path.read_text()
    assert "# %%" not in text
    cells, markers = load_cells(path)
    assert not markers
    assert [(c.source, c.kind, c.hide_code) for c in cells] == [(c.source, c.kind, c.hide_code) for c in CELLS]


def test_layout_is_kept_outside_the_file_in_gpython_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("GPYTHON_HOME", str(home))
    path = tmp_path / "nb.py"
    save(path, CELLS, markers=False)
    layout = sidecar.layout_file(path.read_text(), path)
    assert layout.parent.parent == home / "cells" and layout.exists()
    assert sidecar.outputs_file(path.read_text(), path).exists()


def test_a_file_edited_elsewhere_is_split_afresh(tmp_path):
    path = tmp_path / "nb.py"
    save(path, [CellSpec("a = 1\nb = 2")], markers=False)
    path.write_text("a = 1\n\nb = 2\n")
    cells, _ = load_cells(path)
    assert [c.source for c in cells] == ["a = 1", "b = 2"]


def test_a_new_file_starts_without_markers(tmp_path):
    cells, markers = load_cells(tmp_path / "new.py")
    assert not markers


def test_files_that_already_use_markers_keep_them(tmp_path):
    path = tmp_path / "nb.py"
    path.write_text("# %%\na = 1\n\n# %%\nb = 2\n")
    cells, markers = load_cells(path)
    assert markers
    save(path, cells, markers)
    assert path.read_text().count("# %%") == 2


def test_a_moved_file_keeps_its_cells(tmp_path):
    path = tmp_path / "nb.py"
    save(path, CELLS, markers=False)
    moved = tmp_path / "elsewhere" / "renamed.py"
    moved.parent.mkdir()
    path.rename(moved)
    cells, _ = load_cells(moved)
    assert [(c.source, c.kind) for c in cells] == [(c.source, c.kind) for c in CELLS]


def test_a_copy_keeps_its_cells_when_the_original_is_saved_again(tmp_path):
    original, copy = tmp_path / "a.py", tmp_path / "b.py"
    save(original, CELLS, markers=False)
    copy.write_text(original.read_text())
    save(original, [*CELLS, CellSpec("z = 9")], markers=False)
    cells, _ = load_cells(copy)
    assert [(c.source, c.kind, c.hide_code) for c in cells] == [(c.source, c.kind, c.hide_code) for c in CELLS]


def test_each_copy_keeps_its_own_outputs(tmp_path):
    first, second = tmp_path / "a.py", tmp_path / "b.py"
    out = lambda text: [{"output_type": "stream", "name": "stdout", "text": text}]
    save(first, [CellSpec("print(1)", outputs=out("from a\n"), execution_count=1)], markers=False)
    second.write_text(first.read_text())
    assert notebook.load_outputs(second, load_cells(second)[0])[0]["outputs"] == out("from a\n")
    assert notebook.remember_outputs(second, [CellSpec("print(1)", outputs=out("from b\n"), execution_count=1)], False)
    assert notebook.load_outputs(first, load_cells(first)[0])[0]["outputs"] == out("from a\n")
    assert notebook.load_outputs(second, load_cells(second)[0])[0]["outputs"] == out("from b\n")


def test_a_stale_outputs_write_cannot_remove_the_saved_layout(tmp_path):
    path = tmp_path / "nb.py"
    old = [CellSpec("a = 1"), CellSpec("b = 2")]
    save(path, old, markers=False)
    old_text = path.read_text()
    new = [CellSpec("a = 1\nb = 2", hide_code=True)]
    save(path, new, markers=False)
    sidecar.write_outputs(path, old_text, notebook.outputs_json(old))
    cells, _ = load_cells(path)
    assert [(c.source, c.hide_code) for c in cells] == [("a = 1\nb = 2", True)]


def test_overlapping_or_unordered_ranges_are_not_trusted():
    lines = ["a = 1", "b = 2", "c = 3"]
    entry = lambda start, end: {"start": start, "end": end, "kind": "code"}
    assert sidecar._cells(lines, [entry(0, 2), entry(1, 3)]) is None
    assert sidecar._cells(lines, [entry(2, 3), entry(0, 2)]) is None
    assert sidecar._cells(lines, [entry(0, 1), entry(2, 3)]) is None
    assert [c.source for c in sidecar._cells(lines, [entry(0, 1), entry(1, 3)])] == ["a = 1", "b = 2\nc = 3"]


def test_prune_forgets_layouts_unused_for_a_month_once_a_day(tmp_path):
    import os
    import time

    old, fresh = tmp_path / "old.py", tmp_path / "fresh.py"
    save(old, [CellSpec("a = 1")], markers=False)
    save(fresh, [CellSpec("b = 2")], markers=False)
    now = time.time()
    month_ago = now - (sidecar.KEEP_DAYS + 1) * 24 * 3600
    for entry in sidecar.content_dir(old.read_text()).iterdir():
        os.utime(entry, (month_ago, month_ago))
    assert sidecar.prune(now) == 2
    assert not sidecar.content_dir(old.read_text()).exists()
    assert sidecar.layout_file(fresh.read_text(), fresh).exists()
    for entry in sidecar.content_dir(fresh.read_text()).iterdir():
        os.utime(entry, (month_ago, month_ago))
    assert sidecar.prune(now + 60) == 0


def test_save_can_leave_outputs_for_later(tmp_path):
    path = tmp_path / "nb.py"
    save(path, [CellSpec("x = 1", execution_count=1)], markers=False, outputs=False)
    assert sidecar.layout_file(path.read_text(), path).exists()
    assert not sidecar.outputs_file(path.read_text(), path).exists()


def test_a_hand_written_file_keeps_its_spacing_when_saved(tmp_path):
    original = 'import os\nimport sys\n\n\n\ndef f():\n    return 1\nx = f()\n'
    path = tmp_path / "nb.py"
    path.write_text(original)
    cells, markers = load_cells(path)
    save(path, cells, markers)
    assert path.read_text() == original
    again, _ = load_cells(path)
    save(path, again, markers)
    assert path.read_text() == original


def test_outputs_run_number_and_time_survive_save_and_load(tmp_path):
    path = tmp_path / "nb.py"
    out = [{"output_type": "stream", "name": "stdout", "text": "hello\n"}]
    save(path, [CellSpec("print('hello')", outputs=out, execution_count=3, duration=1.25), CellSpec("y = 2", execution_count=4, duration=0.01)], markers=False)
    assert "hello\n" not in path.read_text().replace("print('hello')", "")
    cells = load_cells(path)[0]
    assert all(not c.outputs and c.execution_count is None for c in cells)
    first, second = (notebook.with_run_state(c, s) for c, s in zip(cells, notebook.load_outputs(path, cells)))
    assert (first.outputs, first.execution_count, first.duration) == (out, 3, 1.25)
    assert (second.outputs, second.execution_count, second.duration) == ([], 4, 0.01)


def test_remember_outputs_only_when_the_file_on_disk_is_these_cells(tmp_path):
    path = tmp_path / "nb.py"
    cells = [CellSpec("x = 1")]
    save(path, cells, markers=False)
    ran = [CellSpec("x = 1", execution_count=1, duration=0.5)]
    assert notebook.remember_outputs(path, ran, False)
    assert notebook.load_outputs(path, load_cells(path)[0])[0]["duration"] == 0.5
    assert not notebook.remember_outputs(path, [CellSpec("x = 2", execution_count=1)], False)


def test_a_run_is_kept_without_saving_and_shown_after_reopening(tmp_path):
    path = tmp_path / "nb.py"
    save(path, [CellSpec("print('kept')"), CellSpec("z = 3")], markers=False)

    async def run_then_quit():
        app = GPython(path)
        async with app.run_test(size=(120, 40)) as pilot:
            await asyncio.wait_for(app.kernel.ready.wait(), 60)
            await app._run_ex("run")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert not app.pane.dirty
            first, second = app.cells()
            assert first.output_text.strip() == "kept" and second.duration is not None

    async def reopen():
        app = GPython(path)
        async with app.run_test(size=(120, 40)) as pilot:
            await app.loaded.wait()
            await pilot.pause()
            first, second = app.cells()
            assert first.output_text.strip() == "kept"
            assert first.exec_count == 1 and second.exec_count == 2
            assert first.duration is not None and second.duration is not None

    asyncio.run(run_then_quit())
    assert path.read_text() == "print('kept')\n\nz = 3\n"
    asyncio.run(reopen())
