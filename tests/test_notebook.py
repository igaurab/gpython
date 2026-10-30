import json

from gpython.notebook import CellSpec, dump, dump_ipynb, load_cells, parse, parse_ipynb, save, split_script


def test_roundtrip():
    cells = [CellSpec("import math"), CellSpec("x = math.pi\nprint(x)"), CellSpec("")]
    assert parse(dump(cells)) == cells


def test_dump_accepts_plain_strings():
    assert dump(["a = 1", ""]) == "# %%\na = 1\n\n# %%\n\n"


def test_preamble_becomes_cell():
    assert parse("a = 1\n# %%\nb = 2\n") == [CellSpec("a = 1"), CellSpec("b = 2")]


def test_empty_file():
    assert parse("") == [CellSpec("")]


def test_markdown_percent_roundtrip():
    cells = [CellSpec("# Title\n\nSome *text*", "markdown"), CellSpec("x = 1")]
    text = dump(cells)
    assert text == "# %% [markdown]\n# # Title\n#\n# Some *text*\n\n# %%\nx = 1\n"
    assert parse(text) == cells
    assert parse("# %% [md]\n# hi\n") == [CellSpec("hi", "markdown")]


SCRIPT = '''"""Docstring for the file."""
import os
import sys

# helper comment
def f(x):
    return x + 1


class C:
    pass

a = 1
b = 2

c = a + b
print(c)

if __name__ == "__main__":
    f(a)
'''


def test_split_script():
    assert split_script(SCRIPT) == [
        CellSpec("Docstring for the file.", "markdown"),
        CellSpec("import os\nimport sys"),
        CellSpec("# helper comment\ndef f(x):\n    return x + 1"),
        CellSpec("class C:\n    pass"),
        CellSpec("a = 1\nb = 2"),
        CellSpec("c = a + b\nprint(c)"),
        CellSpec('if __name__ == "__main__":\n    f(a)'),
    ]


def test_split_script_roundtrip_without_markers():
    cells = split_script(SCRIPT)
    text = dump(cells, markers=False)
    assert text.startswith('"""\nDocstring for the file.\n"""\n\n\nimport os')
    assert split_script(text) == cells


def test_markdown_with_triple_quotes_survives_script_roundtrip():
    cells = [CellSpec('use """ like this \\ ok', "markdown"), CellSpec("x = 1")]
    assert split_script(dump(cells, markers=False)) == cells


def test_split_invalid_script_is_one_cell():
    assert split_script("x = (\n") == [CellSpec("x = (")]


def test_ipynb_roundtrip(tmp_path):
    nb = {
        "cells": [
            {"cell_type": "markdown", "metadata": {}, "source": ["# Hello\n", "world"]},
            {"cell_type": "code", "execution_count": 3, "metadata": {}, "outputs": [{"output_type": "stream", "text": "x"}], "source": "x = 1\nx"},
        ],
        "metadata": {"kernelspec": {"name": "python3", "display_name": "Py", "language": "python"}, "custom": True},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    path = tmp_path / "nb.ipynb"
    path.write_text(json.dumps(nb))
    cells, markers = load_cells(path)
    assert cells == [CellSpec("# Hello\nworld", "markdown"), CellSpec("x = 1\nx", outputs=[{"output_type": "stream", "text": "x"}], execution_count=3)]
    assert markers is True

    save(path, cells + [CellSpec("y = 2")])
    saved = json.loads(path.read_text())
    assert saved["metadata"]["custom"] is True  # metadata preserved
    assert [c["cell_type"] for c in saved["cells"]] == ["markdown", "code", "code"]
    assert saved["cells"][1]["outputs"] == [{"output_type": "stream", "text": "x"}] and saved["cells"][1]["execution_count"] == 3
    assert saved["cells"][2]["outputs"] == [] and saved["cells"][2]["execution_count"] is None
    assert saved["cells"][1]["source"] == ["x = 1\n", "x"]
    assert parse_ipynb(dump_ipynb(cells)) == cells
