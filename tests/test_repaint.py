"""Partial repaints (perf round 2) must draw exactly what a full repaint would.

VimTextArea repaints only the lines a cursor move or an edit touched, and Cell paints
its left bar inline. After every step we compare the editor's cached strips (what the
screen shows) with a forced full render.
"""

import asyncio

from textual.color import Color
from textual.geometry import Region

from gpython.app import GPython
from gpython.vim import VimTextArea


def strips(editor):
    region = Region(0, 0, editor.size.width, editor.size.height)
    return [[(seg.text, str(seg.style)) for seg in strip] for strip in editor.render_lines(region)]


async def same_as_full(editor, pilot) -> None:
    cached = strips(editor)
    editor.refresh()  # everything dirty: the next render is from scratch
    await pilot.pause()
    assert cached == strips(editor)


def test_editor_partial_repaints_match_full_render(tmp_path):
    path = tmp_path / "nb.py"
    path.write_text("# %%\ndef f(x):\n    return (x + 1) * 2\n\n\nprint(f(3))\n\n# %%\nb = 2\n")

    async def main():
        app = GPython(path)
        async with app.run_test(size=(100, 40)) as pilot:
            await app.loaded.wait()
            await pilot.pause()
            editor = app.cells()[0].editor
            editor.focus()
            await pilot.pause()
            steps = [
                ("j",), ("j",), ("l", "l", "l"),  # cursor moves across lines and columns
                ("$",), ("0",), ("G",), ("g", "g"),
                ("f",),  # not a motion here: nothing happens
                ("i", "a", "b", "c"),  # typing on a line
                ("enter", "x", "y"),  # new line: full repaint path
                ("backspace", "backspace", "backspace"),  # join lines back
                ("escape", "j", "$"),  # onto ')' : bracket match highlight
                ("h", "h", "h"),  # off the bracket
                ("o", "z", "=", "(", ")"),  # open a line, type brackets
                ("escape", "d", "w"), ("u",), ("ctrl+r",),
                ("shift+right", "shift+right"),  # a selection
                ("escape",), ("x",), ("escape",), ("y", "y", "p"),
            ]
            for keys in steps:
                await pilot.press(*keys)
                await pilot.pause()
                await same_as_full(editor, pilot)
            # Leaving and re-entering the editor (focus = cursor shown / hidden).
            await pilot.press("escape", "escape")
            await pilot.pause()
            await same_as_full(editor, pilot)
            await pilot.press("enter")
            await pilot.pause()
            await same_as_full(editor, pilot)

    asyncio.run(asyncio.wait_for(main(), 120))


def test_cell_bar_follows_status_and_focus(tmp_path):
    path = tmp_path / "nb.py"
    path.write_text("# %%\na = 1\n\n# %%\nb = a + 1\n")

    async def main():
        app = GPython(path)
        async with app.run_test(size=(100, 40)) as pilot:
            await app.loaded.wait()
            await pilot.pause()
            first, second = app.cells()
            colors = app.bar_colors
            bar = lambda cell: cell.styles.outline_left[1].hex.upper()
            hexof = lambda c: Color.parse(c).hex.upper()
            assert bar(first) == hexof(colors["accent"])  # focused
            assert bar(second) == hexof(colors["panel"])
            await pilot.press("escape", "escape", "j")  # cell mode, select the second cell
            await pilot.pause()
            assert bar(second) == hexof(colors["accent"]) and bar(first) == hexof(colors["panel"])
            assert second.selected and second.has_class("-selected") and not first.has_class("-selected")
            header = str(second.query_one(".cell-header").render())
            assert "c2" in header
            second.set_status("error")
            assert bar(second) == hexof(colors["accent"])  # selection wins while selected
            await pilot.press("k")
            await pilot.pause()
            assert bar(second) == hexof(colors["error"])
            second.set_status("stale")
            assert bar(second) == hexof(colors["warning"])
            second.set_status("running")
            assert bar(second) == hexof(colors["primary"])
            # Theme change recolours every bar.
            app.theme = "nord"
            await pilot.pause()
            assert bar(second) == hexof(app.bar_colors["primary"]) and bar(first) == hexof(app.bar_colors["accent"])

    asyncio.run(asyncio.wait_for(main(), 60))
