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
    editor.refresh()
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
                ("j",), ("j",), ("l", "l", "l"),
                ("$",), ("0",), ("G",), ("g", "g"),
                ("f",),
                ("i", "a", "b", "c"),
                ("enter", "x", "y"),
                ("backspace", "backspace", "backspace"),
                ("escape", "j", "$"),
                ("h", "h", "h"),
                ("o", "z", "=", "(", ")"),
                ("escape", "d", "w"), ("u",), ("ctrl+r",),
                ("shift+right", "shift+right"),
                ("escape",), ("x",), ("escape",), ("y", "y", "p"),
            ]
            for keys in steps:
                await pilot.press(*keys)
                await pilot.pause()
                await same_as_full(editor, pilot)
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
            colors = {role: Color.parse(value) for role, value in app.bar_colors.items()}
            assert colors["panel"].a > 0
            bar = lambda cell: cell.styles.outline_left[1]
            assert bar(first) == colors["accent"]
            assert bar(second) == colors["panel"]
            await pilot.press("escape", "escape", "j")
            await pilot.pause()
            assert bar(second) == colors["accent"] and bar(first) == colors["panel"]
            assert second.selected and second.has_class("-selected") and not first.has_class("-selected")
            header = str(second.query_one(".cell-header").render())
            assert "c2" in header
            second.set_status("error")
            assert bar(second) == colors["accent"]
            await pilot.press("k")
            await pilot.pause()
            assert bar(second) == colors["error"]
            second.set_status("stale")
            assert bar(second) == colors["warning"]
            second.set_status("running")
            assert bar(second) == colors["primary"]
            app.theme = "nord"
            await pilot.pause()
            assert bar(second) == Color.parse(app.bar_colors["primary"]) and bar(first) == Color.parse(app.bar_colors["accent"])
            assert bar(second) != colors["primary"]

    asyncio.run(asyncio.wait_for(main(), 60))
