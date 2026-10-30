import asyncio
import gc
import weakref

from gpython.app import GPython


def collect() -> None:
    for _ in range(3):
        gc.collect()


def test_removed_cells_and_their_editors_are_freed(tmp_path):
    """The cell list's arrangement cache keeps the last four layouts, so the most recently
    removed cell lingers until a few more layouts happen; the resizes force them."""
    path = tmp_path / "nb.py"
    path.write_text("# %%\nx = 1\n\n# %%\ny = 2\n")

    async def main():
        app = GPython(path)
        async with app.run_test(size=(120, 40)) as pilot:
            await app.loaded.wait()
            pane = app.pane
            gone: weakref.WeakSet = weakref.WeakSet()
            for _ in range(3):
                cell = await pane._insert(app.cells()[-1])
                gone.add(cell)
                gone.add(cell.editor)
                pane.select(cell, "insert")
                await pilot.press("a", "escape")
                await pane._remove_cells([cell], record=False)
            del cell
            for step in range(1, 6):
                await pilot.resize_terminal(120 + step, 40)
                await pilot.pause()
            collect()
            assert not [type(x).__name__ for x in gone]
            watchers = getattr(app, "__watchers")["theme"]
            assert len(watchers) == len(app.cells())
            assert set(app.screen.text_selection_started_signal._subscriptions) == {c.editor for c in app.cells()}

    asyncio.run(main())


def test_closed_notebook_is_freed(tmp_path):
    first = tmp_path / "a.py"
    first.write_text("# %%\nx = 1\n")
    second = tmp_path / "b.py"
    second.write_text("# %%\ny = 2\n\n# %%\nz = 3\n")

    async def main():
        app = GPython(first)
        async with app.run_test(size=(120, 40)) as pilot:
            await app.loaded.wait()
            pane = await app.open_file(second)
            await pane.loaded.wait()
            gone: weakref.WeakSet = weakref.WeakSet(pane.cells())
            gone.add(pane)
            await app.workspace.delete(pane)
            del pane
            await app.workers.wait_for_complete()
            for step in range(1, 6):
                await pilot.resize_terminal(120 + step, 40)
                await pilot.pause()
            collect()
            assert not [type(x).__name__ for x in gone]

    asyncio.run(main())
