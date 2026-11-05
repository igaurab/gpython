"""With :set cursor=bar, insert and replace mode show the terminal's own cursor as a bar; normal
mode hides it and paints a block. The default is a painted block in every mode, coloured like the
mode. Tests run headless, so the terminal is faked and its writes recorded."""

import asyncio

import pytest
from textual.color import Color

from gpython import config, notebook, vim
from gpython.app import GPython
from gpython.notebook import CellSpec
from gpython.app import _MODE_STYLES
from gpython.vim import CELL_MODE_COLOR, EDITING_COLOR, HIDE_CURSOR, MODE_CURSOR_COLORS, RESET_CURSOR_COLOR, SHOW_BAR_CURSOR, VimTextArea, terminal_cursor_color

SHOW_INSERT_BAR = SHOW_BAR_CURSOR + terminal_cursor_color("insert")
HIDE = HIDE_CURSOR + RESET_CURSOR_COLOR


@pytest.fixture(autouse=True)
def _fake_terminal(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(vim, "has_real_terminal", lambda app: True)
    config.save({"insert_cursor": "bar"})
    VimTextArea.bar_editor = None
    VimTextArea.bar_color = ""
    yield
    VimTextArea.bar_editor = None
    VimTextArea.bar_color = ""
    VimTextArea.insert_cursor = "block"


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
        assert VimTextArea.bar_editor is editor and written[-1] == SHOW_INSERT_BAR
        assert not editor._draw_cursor and not editor.cursor_blink
        assert app.cursor_position == editor.cursor_screen_offset
        await pilot.press("escape")
        await pilot.pause()
        assert VimTextArea.bar_editor is None and written[-1] == HIDE
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
        assert VimTextArea.bar_editor is second and written.count(SHOW_INSERT_BAR) == 1
        assert app.cursor_position == second.cursor_screen_offset

    run(tmp_path, monkeypatch, test)


def test_underline_shape_is_set_and_saved(tmp_path, monkeypatch):
    async def test(app, pilot, written):
        editor = app.cells()[0].editor
        await app._run_ex("set cursor=underline")
        await pilot.press("i")
        await pilot.pause()
        assert VimTextArea.bar_editor is editor and written[-1] == "\x1b[3 q" + vim.SHOW_CURSOR + terminal_cursor_color("insert")
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
        assert VimTextArea.bar_editor is None and written[-1] == HIDE

    run(tmp_path, monkeypatch, test)


def test_block_is_the_default_and_the_cursor_takes_each_mode_colour(tmp_path, monkeypatch):
    config._path().unlink()

    async def test(app, pilot, written):
        editor = app.cells()[0].editor
        assert VimTextArea.insert_cursor == "block"

        def colour() -> str:
            return editor._theme.cursor_style.bgcolor.name

        assert colour() == MODE_CURSOR_COLORS["normal"]
        await pilot.press("i")
        await pilot.pause()
        assert VimTextArea.bar_editor is None and editor._draw_cursor
        assert colour() == MODE_CURSOR_COLORS["insert"]
        await pilot.press("escape", "R")
        assert colour() == MODE_CURSOR_COLORS["replace"]
        await pilot.press("escape", "v")
        assert colour() == MODE_CURSOR_COLORS["visual"]
        await pilot.press("escape", "ctrl+v")
        assert colour() == MODE_CURSOR_COLORS["visual"] and app.ui_mode == "v-block"
        await pilot.press("escape")
        assert colour() == MODE_CURSOR_COLORS["normal"]
        await pilot.press("escape")
        await pilot.pause()
        cell = app.cells()[0]
        blue = Color.parse(CELL_MODE_COLOR)
        assert app.ui_mode == "cell" and cell.styles.outline_left[1] == blue
        assert editor._draw_cursor and colour() == MODE_CURSOR_COLORS["cell"] == CELL_MODE_COLOR
        assert CELL_MODE_COLOR in _MODE_STYLES["cell"]
        await pilot.press("enter")
        await pilot.pause()
        assert cell.styles.outline_left[1] == Color.parse(EDITING_COLOR)
        assert colour() == MODE_CURSOR_COLORS["normal"]
        await pilot.press("escape", "j")
        await pilot.pause()
        second = app.cells()[1].editor
        assert second._draw_cursor and second._theme.cursor_style.bgcolor.name == CELL_MODE_COLOR
        assert not editor._draw_cursor and colour() == MODE_CURSOR_COLORS["normal"]

    run(tmp_path, monkeypatch, test)


def test_copy_all_metadata_puts_the_whole_file_on_the_clipboard(tmp_path, monkeypatch):
    async def test(app, pilot, written):
        await app._run_ex("copy all metadata")
        assert app.clipboard == (tmp_path / "nb.py").read_text()
        assert "a = 1" in app.clipboard and "b = 2" in app.clipboard
        await app._run_ex("copy")
        assert app.clipboard == "a = 1"

    run(tmp_path, monkeypatch, test)


def test_copy_all_metadata_of_an_ipynb_is_percent_code_not_json(tmp_path, monkeypatch):
    cells = (CellSpec("# Title", "markdown"), CellSpec("x = 1"), CellSpec("print(x)"))

    async def test(app, pilot, written):
        await app._run_ex("copy all metadata")
        text = app.clipboard
        assert "{" not in text and '"cell_type"' not in text
        assert text == notebook.dump(list(cells), True)
        assert "# %% [markdown]" in text and "# # Title" in text
        assert "x = 1" in text and "print(x)" in text

    run(tmp_path, monkeypatch, test, "nb.ipynb", cells)


def test_copy_all_has_no_cell_markers_by_default(tmp_path, monkeypatch):
    cells = (CellSpec("# Title", "markdown"), CellSpec("x = 1"), CellSpec("print(x)"))

    async def test(app, pilot, written):
        for command in ("copy all", "copy all clean"):
            await app._run_ex(command)
            assert app.clipboard == "# # Title\n\nx = 1\n\nprint(x)\n"

    run(tmp_path, monkeypatch, test, "nb.ipynb", cells)
