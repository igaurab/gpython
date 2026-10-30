from dataclasses import replace
from pathlib import Path

import pytest

from gpython.notebook import CellSpec, dump_marimo, is_marimo, load_cells, parse_marimo, save

EXAMPLE = '''import marimo

__generated_with = "0.25.2"
app = marimo.App(width="medium")


@app.cell(hide_code=True)
def _():
    import marimo as mo

    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.vstack([
        mo.md('## [1. Two Sum  (Easy)](https://leetcode.com/problems/two-sum/)'),
        mo.Html('<p>You are given an array ...</p>'),
    ])
    return


@app.cell(hide_code=True)
def _():
    import sys as _sys, pathlib as _pathlib
    _sys.path[:0] = [str(p) for p in _pathlib.Path(__file__).resolve().parents if (p / "lc.py").exists()]
    from lc import run

    return (run,)


@app.class_definition
class Solution:
    def twoSum(self, nums: list[int], target: int) -> list[int]:
        pass


@app.cell
def _():
    TESTS = """
    Input: nums = [2,7,11,15], target = 9
    Output: [0,1]
    """
    return (TESTS,)


@app.cell
def _(TESTS, run):
    run(Solution, TESTS, any_order=True, ns=globals())
    return


if __name__ == "__main__":
    app.run()
'''

CORPUS = Path.home() / "leetcode" / "notebooks"


def test_detects_marimo():
    assert is_marimo(EXAMPLE)
    assert not is_marimo("import marimo\nx = 1\n")
    assert not is_marimo("app = App()\n")


def test_parse_example():
    cells = parse_marimo(EXAMPLE)
    assert len(cells) == 6
    assert cells[0] == CellSpec("import marimo as mo", hide_code=True)
    assert cells[1].source.startswith("mo.vstack([\n    mo.md(") and cells[1].hide_code
    assert cells[2].source.endswith("from lc import run") and cells[2].hide_code
    assert cells[3] == CellSpec(
        "class Solution:\n    def twoSum(self, nums: list[int], target: int) -> list[int]:\n        pass",
        decorator="class_definition",
    )
    # marimo strips the 4-space indent from string contents too.
    assert cells[4] == CellSpec('TESTS = """\nInput: nums = [2,7,11,15], target = 9\nOutput: [0,1]\n"""')
    assert cells[5] == CellSpec("run(Solution, TESTS, any_order=True, ns=globals())")
    assert all(c.kind == "code" and c.name is None for c in cells)


def test_dump_example_roundtrip():
    assert dump_marimo(parse_marimo(EXAMPLE)) == EXAMPLE


def test_load_save_roundtrip(tmp_path):
    path = tmp_path / "nb.py"
    path.write_text(EXAMPLE)
    cells, markers = load_cells(path)
    assert markers is True and len(cells) == 6
    save(path, cells, markers)
    assert path.read_text() == EXAMPLE


def test_markdown_cell():
    text = EXAMPLE.replace(
        "    mo.vstack([\n",
        '    mo.md(r"""\n    # Title\n\n    Some *text*\n    """)\n    return\n\n\n@app.cell\ndef _(mo):\n    mo.vstack([\n',
    )
    cells = parse_marimo(text)
    assert cells[1] == CellSpec("# Title\n\nSome *text*", "markdown", hide_code=True)
    assert dump_marimo(cells) == text
    # A fresh markdown cell gets hide_code and the `mo` parameter.
    out = dump_marimo([cells[0], CellSpec("hello", "markdown")])
    assert '@app.cell(hide_code=True)\ndef _(mo):\n    mo.md(r"""\n    hello\n    """)\n    return' in out


def test_non_canonical_md_stays_code():
    text = EXAMPLE.replace("    mo.vstack([\n", "    mo.md('# hi')\n    mo.vstack([\n")
    cells = parse_marimo(text)
    assert cells[1].kind == "code"
    assert dump_marimo(cells) == text


def test_edited_cells_recompute_refs_and_defs():
    cells = parse_marimo(EXAMPLE)
    cells.append(CellSpec("n = len(TESTS)\nhelper = 1"))
    cells.append(CellSpec("print(n)"))
    out = dump_marimo(cells)
    # Uses TESTS (defined by another cell) -> parameter; returns only names another cell uses.
    assert "@app.cell\ndef _(TESTS):\n    n = len(TESTS)\n    helper = 1\n    return (n,)" in out
    assert "@app.cell\ndef _(n):\n    print(n)\n    return" in out
    # The class_definition name is module-level, never a parameter.
    assert "def _(TESTS, run):\n    run(Solution" in out


def test_named_async_function_and_empty_cells():
    cells = [
        CellSpec("import asyncio\nx = 1"),
        replace(CellSpec("await asyncio.sleep(0)\ny = x"), name="wait"),
        CellSpec("def helper(a):\n    return a", decorator="function"),
        CellSpec(""),
        CellSpec("print(y)"),
    ]
    out = dump_marimo(cells)
    assert "@app.cell\nasync def wait(asyncio, x):\n    await asyncio.sleep(0)\n    y = x\n    return (y,)" in out
    assert "@app.function\ndef helper(a):\n    return a\n\n\n@app.cell\ndef _():\n    return\n" in out
    assert out.startswith('import marimo\n\n__generated_with = "0.25.2"\napp = marimo.App(width="medium")\n\n\n@app.cell')
    assert out.endswith('\n\n\nif __name__ == "__main__":\n    app.run()\n')
    back = parse_marimo(out)
    assert back[1].name == "wait" and back[2].decorator == "function" and back[3].source == ""
    assert dump_marimo(back) == out


def test_new_file_starting_with_marimo_import_saves_as_marimo(tmp_path):
    path = tmp_path / "new.py"
    save(path, [CellSpec("import marimo as mo"), CellSpec("x = 1")])
    text = path.read_text()
    assert is_marimo(text)
    assert "def _():\n    import marimo as mo\n\n    return\n" in text
    assert load_cells(path)[0] == [CellSpec("import marimo as mo"), CellSpec("x = 1")]


@pytest.mark.skipif(not CORPUS.is_dir(), reason="~/leetcode/notebooks not present")
def test_corpus_roundtrip_byte_for_byte():
    files = sorted(CORPUS.glob("*.py"))
    assert files
    failed = []
    for f in files:
        text = f.read_text()
        cells, _ = load_cells(f)
        if dump_marimo(cells, f) != text:
            failed.append(f.name)
    assert failed == [], f"{len(failed)}/{len(files)} files changed: {failed[:10]}"
