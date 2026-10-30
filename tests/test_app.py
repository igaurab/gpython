import asyncio

from textual.widgets import DataTable

from gpython.app import GPython
from gpython.notebook import parse


def output_text(cell) -> str:
    return "".join(text for _, text in cell.outputs)


def test_reactive_run_edit_delete_save(tmp_path):
    path = tmp_path / "nb.py"
    path.write_text("# %%\nx = 1\n\n# %%\ny = x * 2\ny\n\n# %%\nprint('independent')\n")

    async def main():
        app = GPython(path)
        async with app.run_test(size=(120, 40)) as pilot:
            await asyncio.wait_for(app.kernel.ready.wait(), 60)
            first, second, third = app.cells()

            # Running the first cell re-runs its dependent, but not the unrelated cell.
            first.editor.focus()
            await pilot.press("ctrl+r")
            await app.workers.wait_for_complete()
            assert output_text(second).strip() == "2"
            assert third.status == "idle"
            assert (first.status, second.status) == ("ok", "ok")

            # Editing marks the cell stale; running again propagates the new value.
            first.editor.text = "x = 10"
            await pilot.pause()
            assert first.status == "stale"
            await pilot.press("ctrl+r")
            await app.workers.wait_for_complete()
            assert output_text(second).strip() == "20"
            rows = {app.query_one("#vars", DataTable).get_row_at(i)[0] for i in range(2)}
            assert rows == {"x", "y"}

            # Two cells defining the same name is an error.
            third.editor.text = "x = 3"
            third.editor.focus()
            await pilot.press("ctrl+r")
            await app.workers.wait_for_complete()
            assert third.status == "error" and "defined in 2 cells" in output_text(third)
            third.editor.text = "print('independent')"

            # Deleting the first cell removes x, so the dependent re-runs and fails.
            first.editor.focus()
            await pilot.press("ctrl+d")
            await app.workers.wait_for_complete()
            assert len(app.cells()) == 2
            assert second.status == "error" and "NameError" in output_text(second)

            await pilot.press("ctrl+s")
            assert parse(path.read_text()) == ["y = x * 2\ny", "print('independent')"]

    asyncio.run(main())
