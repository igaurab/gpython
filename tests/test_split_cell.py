"""ctrl+shift+enter and :splitcell: the lines below the cursor line move to a new cell."""

import asyncio

from gpython import notebook
from gpython.app import GPython
from gpython.notebook import CellSpec

BOTH = "def accuracy(loader):\n    return 1\n\nfor epoch in range(3):\n    print(epoch)"
CELLS = [CellSpec("a = 1"), CellSpec(BOTH), CellSpec("b = 2")]


def ui(tmp_path, test, cells=CELLS):
    path = tmp_path / "nb.ipynb"
    notebook.save(path, cells)

    async def main():
        app = GPython(path)
        async with app.run_test(size=(120, 40)) as pilot:
            await app.loaded.wait()
            await pilot.pause()
            await test(app, pilot)

    asyncio.run(asyncio.wait_for(main(), 120))


def sources(app) -> list[str]:
    return [state.source for state in app.pane.states]


async def cursor_in(app, pilot, cell: int, row: int, column: int = 0):
    editor = app.cells()[cell].editor
    editor.focus()
    editor.move_cursor((row, column))
    await pilot.pause()
    return editor


def test_the_lines_below_the_cursor_move_to_a_new_cell_and_the_cursor_stays(tmp_path):
    async def test(app, pilot):
        editor = await cursor_in(app, pilot, 1, 2)
        await pilot.press("ctrl+shift+enter")
        await pilot.pause()
        assert sources(app) == ["a = 1", "def accuracy(loader):\n    return 1", "for epoch in range(3):\n    print(epoch)", "b = 2"]
        assert [state.label for state in app.pane.states] == ["c1", "c2", "c3", "c4"]
        assert app.focused is editor and editor.cursor_location == (1, 0)
        assert app.pane.states[2].kind == "code" and app.pane.dirty

    ui(tmp_path, test)


def test_the_cursor_line_stays_in_the_first_cell(tmp_path):
    async def test(app, pilot):
        editor = await cursor_in(app, pilot, 1, 1, 6)
        await pilot.press("ctrl+shift+enter")
        await pilot.pause()
        assert sources(app)[1:3] == ["def accuracy(loader):\n    return 1", "for epoch in range(3):\n    print(epoch)"]
        assert editor.cursor_location == (1, 6)
        await cursor_in(app, pilot, 2, 0)
        await app._run_ex("splitcell")
        await pilot.pause()
        assert sources(app)[2:4] == ["for epoch in range(3):", "    print(epoch)"]

    ui(tmp_path, test)


def test_undo_takes_the_new_cell_back_and_then_the_text(tmp_path):
    async def test(app, pilot):
        await cursor_in(app, pilot, 1, 2)
        await pilot.press("ctrl+shift+enter")
        await pilot.pause()
        await app.pane.undo()
        await pilot.pause()
        assert len(app.pane.states) == 3
        await app.pane.undo()
        await pilot.pause()
        assert sources(app) == ["a = 1", BOTH, "b = 2"]

    ui(tmp_path, test)


def test_nothing_below_the_cursor_or_no_cursor_line_changes_nothing(tmp_path):
    async def test(app, pilot):
        await cursor_in(app, pilot, 1, 4)
        await pilot.press("ctrl+shift+enter")
        await pilot.pause()
        assert sources(app) == ["a = 1", BOTH, "b = 2"]
        await pilot.press("escape", "escape")
        await pilot.pause()
        assert app.ui_mode == "cell"
        await pilot.press("ctrl+shift+enter")
        await pilot.pause()
        assert sources(app) == ["a = 1", BOTH, "b = 2"]

    ui(tmp_path, test)


def test_the_key_works_in_insert_mode_and_does_not_run_the_cell(tmp_path):
    async def test(app, pilot):
        editor = await cursor_in(app, pilot, 1, 1)
        await pilot.press("i")
        await pilot.press("ctrl+shift+enter")
        await pilot.pause()
        assert len(app.pane.states) == 4 and app.focused is editor
        assert all(state.exec_count == 0 for state in app.pane.states)

    ui(tmp_path, test)
