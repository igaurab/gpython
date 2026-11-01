"""Insert and replace mode show the terminal's own cursor as a bar; normal mode hides it and
paints a block. Tests run headless, so the terminal is faked and its writes recorded."""

import asyncio

import pytest

from gpython import config, notebook, vim
from gpython.app import GPython
from gpython.notebook import CellSpec
from gpython.vim import HIDE_CURSOR, SHOW_BAR_CURSOR, VimTextArea


@pytest.fixture(autouse=True)
def _fake_terminal(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(vim, "has_real_terminal", lambda app: True)
    VimTextArea.bar_editor = None
    yield
    VimTextArea.bar_editor = None
    VimTextArea.insert_cursor = "bar"


def run(tmp_path, monkeypatch, test, name="nb.py", cells=(CellSpec("a = 1"), CellSpec("b = 2"))):
    path = tmp_path / name
    notebook.save(path, list(cells))
    written: list[str] = []
    monkeypatch.setattr(vim, "write_to_terminal", lambda app, data: written.append(data))

    async def main():
        app = GPython(path)
        async with app.run_test(size=(100, 30)) as pilot:
            await app.loaded.wait()
            await pilot.pause()
            await test(app, pilot, written)

    asyncio.run(main())


def test_insert_mode_shows_a_bar_and_normal_mode_hides_it(tmp_path, monkeypatch):
    async def test(app, pilot, written):
        editor = app.cells()[0].editor
        assert app.focused is editor and VimTextArea.mode == "normal"
        assert VimTextArea.bar_editor is None and editor._draw_cursor
        await pilot.press("i")
        await pilot.pause()
        assert VimTextArea.bar_editor is editor and written[-1] == SHOW_BAR_CURSOR
        assert not editor._draw_cursor and not editor.cursor_blink
        assert app.cursor_position == editor.cursor_screen_offset
        await pilot.press("escape")
        await pilot.pause()
        assert VimTextArea.bar_editor is None and written[-1] == HIDE_CURSOR
        assert editor._draw_cursor

    run(tmp_path, monkeypatch, test)


def test_the_bar_follows_focus_to_the_next_cell(tmp_path, monkeypatch):
    async def test(app, pilot, written):
        first, second = (cell.editor for cell in app.cells())
        await pilot.press("i")
        await pilot.pause()
        assert VimTextArea.bar_editor is first
        second.focus()
        await pilot.pause()
        assert VimTextArea.bar_editor is second and written.count(SHOW_BAR_CURSOR) == 1
        assert app.cursor_position == second.cursor_screen_offset

    run(tmp_path, monkeypatch, test)


def test_underline_shape_is_set_and_saved(tmp_path, monkeypatch):
    async def test(app, pilot, written):
        editor = app.cells()[0].editor
        await app._run_ex("set cursor=underline")
        await pilot.press("i")
        await pilot.pause()
        assert VimTextArea.bar_editor is editor and written[-1] == "\x1b[3 q" + vim.SHOW_CURSOR
        assert config.load()["insert_cursor"] == "underline"

    run(tmp_path, monkeypatch, test)


def test_block_shape_paints_the_cursor_and_hides_the_terminal_one(tmp_path, monkeypatch):
    async def test(app, pilot, written):
        editor = app.cells()[0].editor
        await pilot.press("i")
        await pilot.pause()
        assert VimTextArea.bar_editor is editor
        await app._run_ex("set cursor=block")
        await pilot.pause()
        assert VimTextArea.bar_editor is None and written[-1] == HIDE_CURSOR

    run(tmp_path, monkeypatch, test)


def test_copy_all_puts_the_whole_file_on_the_clipboard(tmp_path, monkeypatch):
    async def test(app, pilot, written):
        await app._run_ex("copy all")
        assert app.clipboard == (tmp_path / "nb.py").read_text()
        assert "a = 1" in app.clipboard and "b = 2" in app.clipboard
        await app._run_ex("copy")
        assert app.clipboard == "a = 1"

    run(tmp_path, monkeypatch, test)


def test_copy_all_of_an_ipynb_is_code_with_markdown_as_comments(tmp_path, monkeypatch):
    cells = (CellSpec("# Title", "markdown"), CellSpec("x = 1"), CellSpec("print(x)"))

    async def test(app, pilot, written):
        await app._run_ex("copy all")
        text = app.clipboard
        assert "{" not in text and '"cell_type"' not in text
        assert text == notebook.dump(list(cells), True)
        assert "# %% [markdown]" in text and "# # Title" in text
        assert "x = 1" in text and "print(x)" in text

    run(tmp_path, monkeypatch, test, "nb.ipynb", cells)


def test_copy_all_clean_has_no_cell_markers(tmp_path, monkeypatch):
    cells = (CellSpec("# Title", "markdown"), CellSpec("x = 1"), CellSpec("print(x)"))

    async def test(app, pilot, written):
        await app._run_ex("copy all clean")
        assert app.clipboard == "# # Title\n\nx = 1\n\nprint(x)\n"

    run(tmp_path, monkeypatch, test, "nb.ipynb", cells)
