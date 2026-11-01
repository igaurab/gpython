"""Plain .py notebooks are saved as ordinary code; their cell layout lives in ~/.gpython."""

from gpython import sidecar
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
    layout = sidecar.layout_file(path.read_text())
    assert layout.parent == home / "cells" and layout.exists()


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


def test_saving_again_forgets_the_old_layout(tmp_path):
    path = tmp_path / "nb.py"
    save(path, [CellSpec("a = 1")], markers=False)
    first = sidecar.layout_file(path.read_text())
    save(path, [CellSpec("a = 2")], markers=False)
    assert not first.exists() and sidecar.layout_file(path.read_text()).exists()


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
