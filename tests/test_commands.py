"""Cell mode, run keys, jumps, undo/clipboard, dirty tracking and help (plan 03)."""

import asyncio

import pytest
from textual.widgets import DataTable

from gpython import config
from gpython.app import GPython, Prompt
from gpython.help import HelpScreen
from gpython.keymap import KEYMAP
from gpython.vim import VimTextArea

SOURCES = ["a = 1", "b = 2", "print('c')"]


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    VimTextArea.enabled = True
    yield
    VimTextArea.enabled = True
    VimTextArea.line_numbers = True


def notebook(tmp_path, sources=SOURCES):
    path = tmp_path / "nb.py"
    path.write_text("".join(f"# %%\n{s}\n\n" for s in sources))
    return path


def ui(tmp_path, test, sources=SOURCES, kernel=False):
    async def main():
        app = GPython(notebook(tmp_path, sources))
        async with app.run_test(size=(120, 40)) as pilot:
            await app.loaded.wait()
            if kernel:
                await asyncio.wait_for(app.kernel.ready.wait(), 60)
            await pilot.pause()
            await test(app, pilot)

    asyncio.run(main())


def sources(app):
    return [c.source for c in app.cells()]


def selected(app):
    return [c for c in app.cells() if c.has_class("-selected")]


def labels(app):
    return [c.label for c in app.cells()]


def test_escape_chain_and_cell_navigation(tmp_path):
    async def test(app, pilot):
        first, second, third = app.cells()
        assert not app.dirty
        await pilot.press("i")
        assert app.ui_mode == "insert"
        await pilot.press("escape")
        assert app.ui_mode == "normal" and app.focused is first.editor
        await pilot.press("escape")
        assert app.focused is first and app.ui_mode == "cell"
        assert selected(app) == [first]
        assert "CELL" in str(app.query_one("#mode").render())
        await pilot.press("escape")  # stays in cell mode
        assert app.focused is first

        await pilot.press("j")
        assert app.focused is second and selected(app) == [second]
        await pilot.press("k")
        assert app.focused is first
        await pilot.press("G")
        assert app.focused is third
        await pilot.press("g", "g")
        assert app.focused is first
        await pilot.press("2", "j")
        assert app.focused is third
        await pilot.press("2", "G")
        assert app.focused is second
        await pilot.press("q", "w")  # unknown keys do nothing and never reach the editor
        assert sources(app) == SOURCES and app.focused is second

        await pilot.press("enter")
        assert app.focused is second.editor and app.ui_mode == "normal"
        await pilot.press("escape", "i")
        assert app.focused is second.editor and app.ui_mode == "insert"

        # ctrl+home / ctrl+end jump to the first / last cell in any mode.
        await pilot.press("ctrl+end")
        assert app.focused is third.editor
        await pilot.press("ctrl+home")
        assert app.focused is first.editor
        await pilot.press("escape", "escape", "ctrl+end")
        assert app.focused is third

    ui(tmp_path, test)


def test_insert_above_below(tmp_path):
    async def test(app, pilot):
        first, second, _ = app.cells()
        second.focus()
        await pilot.press("a")
        cells = app.cells()
        assert len(cells) == 4 and app.focused is cells[1].editor and app.ui_mode == "insert"
        assert labels(app) == ["c1", "c2", "c3", "c4"]
        await pilot.press("escape", "escape", "b")
        cells = app.cells()
        assert len(cells) == 5 and app.focused is cells[2].editor and app.ui_mode == "insert"
        await pilot.press("ctrl+shift+n")
        cells = app.cells()
        assert len(cells) == 6 and app.focused is cells[2].editor
        assert sources(app) == ["a = 1", "", "", "", "b = 2", "print('c')"]
        assert app.dirty

    ui(tmp_path, test)


def test_delete_undo_copy_cut_paste(tmp_path):
    async def test(app, pilot):
        first, second, third = app.cells()
        second.focus()
        await pilot.press("d", "d")
        assert sources(app) == ["a = 1", "print('c')"] and app.focused is app.cells()[1]
        await pilot.press("z")
        assert sources(app) == SOURCES and app.focused is app.cells()[1]

        await pilot.press("y", "y", "p")
        assert sources(app) == ["a = 1", "b = 2", "b = 2", "print('c')"]
        assert app.focused is app.cells()[2]
        await pilot.press("d", "d")
        assert sources(app) == SOURCES

        app.cells()[0].focus()
        await pilot.press("x")
        assert sources(app) == ["b = 2", "print('c')"]
        await pilot.press("j", "p")  # paste below the last cell
        assert sources(app) == ["b = 2", "print('c')", "a = 1"]
        await pilot.press("g", "g", "P")
        assert sources(app)[0] == "a = 1"
        assert labels(app) == ["c1", "c2", "c3", "c4"]

    ui(tmp_path, test)


def test_vim_dd_deletes_the_cell_and_z_restores(tmp_path):
    async def test(app, pilot):
        second = app.cells()[1]
        second.editor.focus()
        await pilot.press("d", "d")
        await app.workers.wait_for_complete()
        assert sources(app) == ["a = 1", "print('c')"]
        assert app.focused is app.cells()[1].editor and app.ui_mode == "normal"
        await pilot.press("escape", "z")
        assert sources(app) == SOURCES
        # 2dd from the first cell deletes two cells.
        app.cells()[0].editor.focus()
        await pilot.press("2", "d", "d")
        assert sources(app) == ["print('c')"]

    ui(tmp_path, test)


def test_move_cells(tmp_path):
    async def test(app, pilot):
        app.cells()[0].focus()
        await pilot.press("J")
        assert sources(app) == ["b = 2", "a = 1", "print('c')"]
        assert app.focused is app.cells()[1]
        await pilot.press("shift+k")
        assert sources(app) == SOURCES
        app.cells()[2].editor.focus()
        await pilot.press("alt+shift+up")
        assert sources(app) == ["a = 1", "print('c')", "b = 2"]
        assert app.focused is app.cells()[1].editor
        await pilot.press("alt+shift+down")
        assert sources(app) == SOURCES
        assert labels(app) == ["c1", "c2", "c3"]

    ui(tmp_path, test)


def test_output_collapse_and_clear(tmp_path):
    async def test(app, pilot):
        cell = app.cells()[0]
        cell.add_output("stream", {"name": "stdout", "text": "".join(f"{i}\n" for i in range(10))})
        await pilot.pause()
        cell.focus()
        await pilot.press("o")
        assert cell.has_class("-collapsed")
        assert "7 more lines" in str(cell.query_one(".collapsed-note").render())
        await pilot.press("o")
        assert not cell.has_class("-collapsed") and not cell.query(".collapsed-note")
        await pilot.press("O")
        assert cell.output_text == "" and not cell.output_box.has_output

    ui(tmp_path, test)


def test_run_keys(tmp_path):
    async def test(app, pilot):
        first, second, third = app.cells()
        first.editor.focus()
        await pilot.press("ctrl+shift+enter")
        await app.workers.wait_for_complete()
        assert first.status == "ok" and app.focused is first.editor
        assert first.exec_count == 1

        # shift+enter with a cell below: run it and go there, no new cell.
        await pilot.press("shift+enter")
        await app.workers.wait_for_complete()
        assert len(app.cells()) == 3 and app.focused is second.editor
        third.editor.focus()
        await pilot.press("shift+enter")
        await app.workers.wait_for_complete()
        assert third.output_text == "c\n"
        cells = app.cells()
        assert len(cells) == 4 and app.focused is cells[3].editor and app.ui_mode == "insert"

        # alt+enter always inserts.
        cells[0].editor.focus()
        await pilot.press("alt+enter")
        await app.workers.wait_for_complete()
        cells = app.cells()
        assert len(cells) == 5 and app.focused is cells[1].editor and app.ui_mode == "insert"

        # shift+enter in cell mode selects the next cell.
        await pilot.press("escape", "escape", "shift+enter")
        await app.workers.wait_for_complete()
        assert app.focused is cells[2]

        # :runbelow runs only the current cell and those after it.
        for cell in app.cells():
            cell.clear_output()
        app.cells()[3].editor.text = "print('d')"
        app.cells()[3].focus()
        await pilot.press(":", *"runbelow", "enter")
        await app.workers.wait_for_complete()
        assert app.cells()[3].output_text == "d\n"
        assert app.cells()[2].output_text == ""  # third: above, not run

    ui(tmp_path, test, kernel=True)


def test_dirty_and_quit(tmp_path):
    async def test(app, pilot):
        assert not app.dirty
        await pilot.press("i", "x")
        assert app.dirty and app.sub_title.endswith("•")
        await pilot.press("ctrl+s")
        assert not app.dirty and not app.sub_title.endswith("•")
        await pilot.press("x", "escape", ":", "q", "enter")
        await pilot.pause()
        assert isinstance(app.screen, Prompt)
        await pilot.press("n", "enter")
        await pilot.pause()
        assert app.is_running and not isinstance(app.screen, Prompt)
        await pilot.press(":", "q", "!", "enter")
        await pilot.pause()
        assert app.return_code == 0

    ui(tmp_path, test)


def test_jumps_labels_and_vars_panel(tmp_path):
    async def test(app, pilot):
        assert app.query_one("#vars", DataTable).display is False
        await pilot.press("f2")
        assert app.query_one("#vars", DataTable).display is True
        await pilot.press("f2")

        first, second, third = app.cells()
        await pilot.press("/", "3", "enter")
        await pilot.pause()
        assert app.focused is third.editor and app.ui_mode == "normal"
        await pilot.press(":", "1", "enter")
        await pilot.pause()
        assert app.focused is first.editor

        # Labels are display positions; ctrl+n between 1 and 2 renumbers.
        await pilot.press("ctrl+n")
        assert labels(app) == ["c1", "c2", "c3", "c4"]
        assert app.cells()[1].label == "c2" and second.label == "c3"
        await pilot.press("escape", "d", "d")
        assert labels(app) == ["c1", "c2", "c3"]

        # Delete the second cell: /2 now means the former third one.
        second.editor.focus()
        await pilot.press("d", "d")
        assert third.label == "c2"
        first.editor.focus()
        await pilot.press("/", "2", "enter")
        await pilot.pause()
        assert app.focused is third.editor

        # Text search finds the next cell containing it; from cell mode it selects.
        first.focus()
        await pilot.press("/", *"print", "enter")
        await pilot.pause()
        assert app.focused is third and app.ui_mode == "cell"
        await pilot.press("/", *"nothing here", "enter")
        await pilot.pause()
        assert app.focused is third

        # J / K in vim normal mode hop between cells.
        first.editor.focus()
        await pilot.press("J")
        assert app.focused is third.editor
        await pilot.press("K")
        assert app.focused is first.editor

    ui(tmp_path, test)


def test_help_and_keymap(tmp_path):
    keys = {key for _, key, _ in KEYMAP}
    for binding in GPython.BINDINGS:
        for key in binding.key.split(","):
            assert key in keys, key

    async def test(app, pilot):
        await pilot.press("f1")
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        assert not isinstance(app.screen, HelpScreen)
        app.cells()[0].focus()
        await pilot.press("?")
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("q")
        assert not isinstance(app.screen, HelpScreen)

    ui(tmp_path, test)


def test_set_line_numbers_persists(tmp_path):
    async def test(app, pilot):
        await pilot.press(":", *"set nonu", "enter")
        await pilot.pause()
        assert not any(c.editor.show_line_numbers for c in app.cells())
        assert config.load()["line_numbers"] is False
        app.cells()[0].focus()
        await pilot.press("l")
        assert all(c.editor.show_line_numbers for c in app.cells())
        assert config.load()["line_numbers"] is True

    ui(tmp_path, test)


def test_shift_enter_keeps_the_viewport(tmp_path):
    many = [f"v{i} = {i}\nprint(v{i})" for i in range(30)]

    async def test(app, pilot):
        box = app.query_one("#cells")
        cells = app.cells()
        box.scroll_to_widget(cells[14], animate=False, immediate=True, top=True)  # cell 15 at the top
        await pilot.pause()
        cells[14].editor.focus(scroll_visible=False)
        await pilot.pause()
        before = box.scroll_offset.y
        assert before > 0
        assert cells[15].region.bottom <= box.region.bottom  # cell 16 already visible

        await pilot.press("shift+enter")
        await pilot.pause()
        assert app.focused is cells[15].editor
        assert box.scroll_offset.y == before
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert box.scroll_offset.y == before
        assert cells[14].output_text == "14\n"

        # Re-running keeps the old output until the new one arrives, then replaces it.
        cells[15].editor.focus(scroll_visible=False)
        cells[14].editor.text = "pass"
        cells[14].editor.focus(scroll_visible=False)
        await pilot.press("ctrl+e")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert cells[14].output_text == "" and not cells[14].output_box.has_output

        # On the last cell shift+enter creates a cell, scrolled into view.
        cells[-1].editor.focus(scroll_visible=False)
        cells[-1].scroll_visible(animate=False, immediate=True)
        await pilot.pause()
        await pilot.press("shift+enter")
        await pilot.pause()
        new = app.cells()[-1]
        assert len(app.cells()) == 31 and app.focused is new.editor
        assert box.region.contains_region(new.region)

    ui(tmp_path, test, sources=many, kernel=True)
