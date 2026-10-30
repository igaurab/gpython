import asyncio

from textual.widgets import DataTable

from gpython.app import GPython
from gpython.notebook import parse
from gpython.vim import CompletionPopup


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
            await pilot.press("ctrl+enter")
            await app.workers.wait_for_complete()
            assert second.output_text.strip() == "2"
            assert third.status == "idle"
            assert (first.status, second.status) == ("ok", "ok")

            # Editing marks the cell stale; running again propagates the new value.
            first.editor.text = "x = 10"
            await pilot.pause()
            assert first.status == "stale"
            await pilot.press("ctrl+e")
            await app.workers.wait_for_complete()
            assert second.output_text.strip() == "20"
            rows = {app.query_one("#vars", DataTable).get_row_at(i)[0] for i in range(2)}
            assert rows == {"x", "y"}

            # Lazy mode: dependents are only marked stale, f4 runs them.
            await pilot.press("f3")
            first.editor.text = "x = 100"
            await pilot.press("ctrl+e")
            await app.workers.wait_for_complete()
            assert second.status == "stale" and second.output_text.strip() == "20"
            await pilot.press("f4")
            await app.workers.wait_for_complete()
            assert second.status == "ok" and second.output_text.strip() == "200"
            await pilot.press("f3")

            # Two cells defining the same name is an error.
            third.editor.text = "x = 3"
            third.editor.focus()
            await pilot.press("ctrl+e")
            await app.workers.wait_for_complete()
            assert third.status == "error" and "defined in 2 cells" in third.output_text
            third.editor.text = "print('independent')"

            # Deleting the first cell removes x, so the dependent re-runs and fails.
            first.editor.focus()
            await pilot.press("ctrl+d")
            await app.workers.wait_for_complete()
            assert len(app.cells()) == 2
            assert second.status == "error" and "NameError" in second.output_text

            await pilot.press("ctrl+s")
            assert parse(path.read_text()) == ["y = x * 2\ny", "print('independent')"]

            # shift+enter runs and opens a new cell below in insert mode; the mode is global.
            second.editor.focus()
            await pilot.press("escape", "shift+enter")
            await app.workers.wait_for_complete()
            cells = app.cells()
            assert len(cells) == 3 and app.focused is cells[1].editor
            assert cells[1].editor.mode == "insert" and cells[0].editor.mode == "insert"
            await pilot.press("escape")
            assert cells[2].editor.mode == "normal"
            assert "NORMAL" in str(app.query_one("#status").render())
            assert app.sub_title.endswith("-- NORMAL --")

    asyncio.run(main())


def test_mutation_reruns_owner_and_orders_readers(tmp_path):
    path = tmp_path / "nb.py"
    path.write_text("# %%\nxs = []\n\n# %%\nxs.append(1)\n\n# %%\nlen(xs)\n")

    async def main():
        app = GPython(path)
        async with app.run_test(size=(120, 40)) as pilot:
            await asyncio.wait_for(app.kernel.ready.wait(), 60)
            own, mut, read = app.cells()
            mut.editor.focus()
            for _ in range(2):  # running the mutator twice must not append twice
                await pilot.press("ctrl+e")
                await app.workers.wait_for_complete()
            assert read.output_text.strip() == "1"
            assert "mutates xs" in mut.info

    asyncio.run(main())


def test_untitled_plain_script_split_and_completion(tmp_path):
    script = tmp_path / "script.py"
    script.write_text("import math\n\n\ndef area(r):\n    return math.pi * r * r\n\n\nprint(area(2))\n")

    async def main():
        app = GPython(script)
        async with app.run_test(size=(120, 40)) as pilot:
            await asyncio.wait_for(app.kernel.ready.wait(), 60)
            cells = app.cells()
            assert [c.source for c in cells] == ["import math", "def area(r):\n    return math.pi * r * r", "print(area(2))"]
            assert app.markers is False

            # __file__ points at the script so sys.path tricks work.
            cells[2].editor.text = "print(__file__)"
            cells[2].editor.focus()
            await pilot.press("ctrl+e")
            await app.workers.wait_for_complete()
            assert cells[2].output_text.strip() == str(script.resolve())

            # Tab completion comes from the kernel.
            cells[0].editor.focus()
            await pilot.press("ctrl+e")
            await app.workers.wait_for_complete()
            cells[2].editor.text = "math.sqr"
            cells[2].editor.focus()
            await pilot.press("A", "tab")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert cells[2].source == "math.sqrt"
            cells[2].editor.text = "math.ta"
            await pilot.press("end", "tab")
            await app.workers.wait_for_complete()
            await pilot.pause()
            popup = app.query(CompletionPopup)
            assert popup and popup.first().option_count >= 2  # tan, tanh
            await pilot.press("escape")
            assert not app.query(CompletionPopup)

            # Autocomplete as you type: the popup opens on its own, enter accepts.
            cells[2].editor.text = ""
            await pilot.press(*"math.fl")
            await pilot.pause(0.3)
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app.query(CompletionPopup).first().option_count >= 1  # floor
            await pilot.press("enter")
            assert cells[2].source.startswith("math.fl") and len(cells[2].source) > 7
            assert not app.query(CompletionPopup)

            # Saving a plain script keeps it a plain script.
            await pilot.press("escape", "ctrl+s")
            assert "# %%" not in script.read_text()

    asyncio.run(main())


def test_untitled_save_prompt(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    async def main():
        app = GPython(None)
        async with app.run_test(size=(120, 40)) as pilot:
            app.cells()[0].editor.text = "z = 1"
            await pilot.press("ctrl+s")
            await pilot.press(*"new.py", "enter")
            await pilot.pause()
            assert (tmp_path / "new.py").read_text() == "# %%\nz = 1\n"

    asyncio.run(main())


def test_ex_commands(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    async def main():
        app = GPython(None)
        async with app.run_test(size=(120, 40)) as pilot:
            table = app.query_one("#vars")
            await pilot.press("escape", ":")
            await pilot.press(*"variables", "enter")
            await pilot.pause()
            assert table.display is False
            await pilot.press(":", *"vars", "enter")
            await pilot.pause()
            assert table.display is True
            await pilot.press(":", *"new", "enter")
            await pilot.pause()
            assert len(app.cells()) == 2
            app.cells()[0].editor.text = "a = 1"
            await pilot.press("escape", ":", *"wqall out.py", "enter")
            await pilot.pause()
            assert app.return_code == 0
            assert (tmp_path / "out.py").read_text() == "# %%\na = 1\n\n# %%\n\n"

    asyncio.run(main())


def test_click_anywhere_in_cell_focuses_it():
    async def main():
        app = GPython(None)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("ctrl+n")
            first, second = app.cells()
            assert app.focused is second.editor
            await pilot.click(first.query_one(".cell-header"))
            assert app.focused is first.editor
            await pilot.click("#cells", offset=(10, 25))  # empty area below the last cell
            assert app.focused is second.editor

    asyncio.run(main())
