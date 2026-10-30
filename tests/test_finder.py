"""The fuzzy file finder (space f f, :find): matcher ranking on its own, then the popup in the app."""

import asyncio
import subprocess

import pytest
from textual.widgets import Input, OptionList

from gpython import finder, notebook
from gpython.app import GPython
from gpython.finder import FileFinder, fuzzy_match, highlight, list_files, rank
from gpython.notebook import CellSpec
from gpython.vim import VimTextArea


@pytest.fixture(autouse=True)
def _fresh():
    VimTextArea.enabled = True
    finder._cache.clear()
    yield
    VimTextArea.mode = "normal"


def test_subsequence_or_nothing():
    assert fuzzy_match("fdr", "src/finder.py") is not None
    assert fuzzy_match("rdf", "src/finder.py") is None
    assert fuzzy_match("", "a.py") == (0.0, [])
    assert fuzzy_match("FIN der", "src/finder.py")[1] == [4, 5, 6, 7, 8, 9]


def test_ranking():
    files = ["docs/happy.md", "tests/test_app.py", "src/gpython/app.py", "a/p/p.txt", "src/gpython/apple_pie.py"]
    order = [f for f, _ in rank("app", files)[0]]
    assert order[0] == "src/gpython/app.py"
    assert order.index("tests/test_app.py") < order.index("docs/happy.md")
    assert fuzzy_match("find", "x/finder.py")[0] > fuzzy_match("find", "x/f_i_n_d.py")[0]
    assert fuzzy_match("vim", "src/vim.py")[0] > fuzzy_match("vim", "v/i/m/x.py")[0]
    assert fuzzy_match("app", "a/app.py")[0] > fuzzy_match("app", "a/b/c/d/app.py")[0]
    _, matched = rank("zz", files)
    assert matched == []


def test_match_prefers_the_file_name_and_highlights_it():
    score, found = fuzzy_match("app", "src/gpython/app.py")
    assert found == [12, 13, 14]
    text = highlight("src/gpython/app.py", found, "bold red")
    styled = {i for span in text.spans if "red" in str(span.style) for i in range(span.start, span.end)}
    assert styled == {12, 13, 14}
    assert any(span.style == "dim" and span.end == 12 for span in text.spans)


def test_list_files_skips_hidden_and_ignored(tmp_path):
    for name in ["a.py", "sub/b.py", ".hidden/c.py", "node_modules/d.js", ".venv/e.py", "sub/__pycache__/f.pyc", "build/g.py"]:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("")
    assert list_files(tmp_path) == ["a.py", "build/g.py", "sub/b.py"]
    (tmp_path / ".gitignore").write_text("build/\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    assert list_files(tmp_path) == ["a.py", "sub/b.py"]


def ui(tmp_path, test):
    path = tmp_path / "nb.py"
    notebook.save(path, [CellSpec("x = 1"), CellSpec("y = 2")])
    for name in ["alpha.py", "pkg/beta.py", "pkg/gamma_notes.py"]:
        (tmp_path / name).parent.mkdir(exist_ok=True)
        (tmp_path / name).write_text("z = 3\n")

    async def main():
        app = GPython(path)
        async with app.run_test(size=(120, 40)) as pilot:
            await app.loaded.wait()
            await pilot.pause()
            await test(app, pilot)

    asyncio.run(main())


async def open_from_cell_mode(app, pilot):
    app.cells()[0].focus()
    await pilot.pause()
    await pilot.press("space", "f", "f")
    await pilot.pause()
    assert isinstance(app.screen, FileFinder)
    for _ in range(50):
        if app.screen.files:
            break
        await pilot.pause(0.02)


def rows(app):
    return list(app.screen._shown)


def test_space_ff_opens_from_cell_mode_filters_and_enter_opens(tmp_path):
    async def test(app, pilot):
        await open_from_cell_mode(app, pilot)
        assert set(rows(app)) == {"nb.py", "alpha.py", "pkg/beta.py", "pkg/gamma_notes.py"}
        await pilot.press(*"gmn")
        await pilot.pause()
        assert rows(app) == ["pkg/gamma_notes.py"]
        await pilot.press("backspace", "backspace", "backspace", *"bt")
        await pilot.pause()
        assert rows(app)[0] == "pkg/beta.py"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert not isinstance(app.screen, FileFinder)
        assert app.pane.path == tmp_path / "pkg" / "beta.py"
        assert len(app.workspace.tabs) == 2

    ui(tmp_path, test)


def test_keys_move_and_escape_closes(tmp_path):
    async def test(app, pilot):
        cells = len(app.cells())
        await open_from_cell_mode(app, pilot)
        options = app.screen.query_one(OptionList)
        assert options.highlighted == 0
        await pilot.press("ctrl+n", "ctrl+j", "down")
        assert options.highlighted == 3
        await pilot.press("ctrl+p", "ctrl+k", "up")
        assert options.highlighted == 0
        assert isinstance(app.screen, FileFinder)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, FileFinder)
        assert len(app.cells()) == cells
        assert len(app.workspace.tabs) == 1

    ui(tmp_path, test)


def test_space_ff_in_normal_mode_and_space_in_insert_mode(tmp_path):
    async def test(app, pilot):
        editor = app.cells()[0].editor
        editor.focus()
        editor.set_mode("insert")
        editor.move_cursor((0, 5))
        await pilot.press("space", "f", "f")
        await pilot.pause()
        assert not isinstance(app.screen, FileFinder)
        assert editor.text == "x = 1 ff"
        await pilot.press("escape")
        assert VimTextArea.mode == "normal"
        await pilot.press("space", "f", "f")
        await pilot.pause()
        assert isinstance(app.screen, FileFinder)
        assert editor.text == "x = 1 ff"
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, FileFinder)

    ui(tmp_path, test)


def test_ex_find_with_query_and_palette(tmp_path):
    async def test(app, pilot):
        await app._run_ex("find alp")
        await pilot.pause()
        assert isinstance(app.screen, FileFinder)
        assert app.screen.query_one(Input).value == "alp"
        for _ in range(50):
            if app.screen.files:
                break
            await pilot.pause(0.02)
        assert rows(app)[0] == "alpha.py"
        await pilot.press("escape")
        await pilot.pause()
        titles = [c.title for c in app.get_system_commands(app.screen)]
        assert any("(:find)" in t for t in titles)

    ui(tmp_path, test)
