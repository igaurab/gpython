"""Stored .ipynb outputs and marimo hide_code cells in the UI."""

import asyncio
import json

from gpython.app import GPython
from gpython.notebook import CellSpec, load_cells
from gpython.vim import VimTextArea


def ui(path, test):
    async def main():
        app = GPython(path)
        async with app.run_test(size=(120, 40)) as pilot:
            await app.loaded.wait()
            await pilot.pause()
            await test(app, pilot)

    asyncio.run(asyncio.wait_for(main(), 90))


def test_ipynb_outputs_show_on_load_and_survive_save(tmp_path):
    nb = {
        "cells": [
            {
                "cell_type": "code",
                "execution_count": 7,
                "metadata": {},
                "outputs": [
                    {"output_type": "stream", "name": "stdout", "text": ["hello\n", "world\n"]},
                    {"output_type": "execute_result", "data": {"text/plain": ["42"]}, "metadata": {}, "execution_count": 7},
                ],
                "source": "print('hello')\nprint('world')\n42",
            },
            {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": "b = 2"},
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    path = tmp_path / "nb.ipynb"
    path.write_text(json.dumps(nb))

    async def test(app, pilot):
        first, second = app.cells()
        assert "hello\nworld\n" in first.output_text and "42" in first.output_text
        assert first.exec_count == 7 and "[7]" in str(first.query_one(".cell-header").render())
        assert first.status == "idle" and second.output_text == ""
        second.editor.text = "b = 3"
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
        saved = json.loads(path.read_text())
        outs = saved["cells"][0]["outputs"]
        assert outs[0] == {"output_type": "stream", "name": "stdout", "text": "hello\nworld\n"}
        assert outs[1]["output_type"] == "execute_result" and outs[1]["data"] == {"text/plain": "42"} and outs[1]["execution_count"] == 7
        assert saved["cells"][0]["execution_count"] == 7
        assert saved["cells"][1]["source"] == ["b = 3"] and saved["cells"][1]["outputs"] == []
        # Clearing an output clears it in the file too.
        first.focus()
        await pilot.press("O", "escape", ":", *"w", "enter")
        await pilot.pause()
        assert json.loads(path.read_text())["cells"][0]["outputs"] == []

    ui(path, test)


MARIMO = '''import marimo

__generated_with = "0.25.2"
app = marimo.App(width="medium")


@app.cell(hide_code=True)
def _():
    import marimo as mo
    return (mo,)


@app.cell
def _():
    x = 1
    return (x,)


if __name__ == "__main__":
    app.run()
'''


def test_marimo_hide_code_cells_start_collapsed_and_h_toggles(tmp_path):
    path = tmp_path / "nb.py"
    path.write_text(MARIMO)

    async def test(app, pilot):
        hidden, shown = app.cells()
        assert hidden.code_hidden and not hidden.editor.display
        assert "code hidden" in str(hidden.query_one(".cell-header").render())
        assert shown.editor.display
        # The first cell has no editor on screen, so the app starts in cell mode on it.
        assert app.focused is hidden and app.ui_mode == "cell"
        await pilot.press("h")  # show the code
        await pilot.pause()
        assert not hidden.code_hidden and hidden.editor.display and app.dirty
        await pilot.press("enter")  # edit it
        assert app.focused is hidden.editor
        await pilot.press("escape", "escape", "h")  # back to cell mode, hide again
        await pilot.pause()
        assert hidden.code_hidden and not hidden.editor.display and app.focused is hidden
        # j/k move over a hidden-code cell in cell mode; enter shows the code and edits.
        await pilot.press("j", "k")
        assert app.focused is hidden
        await pilot.press("enter")
        assert app.focused is hidden.editor and not hidden.code_hidden
        await pilot.press("escape", "escape", "j", "h")  # hide the second cell's code
        await pilot.pause()
        assert shown.code_hidden
        await pilot.press(":", *"w", "enter")
        await pilot.pause()
        cells, _ = load_cells(path)
        assert [c.hide_code for c in cells] == [False, True]
        assert path.read_text().count("hide_code=True") == 1
        await pilot.press(":", *"show", "enter")
        await pilot.pause()
        assert not shown.code_hidden and shown.editor.display

    ui(path, test)
