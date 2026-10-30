"""/text search, n / N, * / #, smart case, match highlighting and :noh."""

import asyncio

import pytest

from gpython.app import GPython
from gpython.vim import SEARCH_STYLE, VimTextArea

SOURCES = ["alpha = 1\nbeta = alpha", "gamma = 2", "Alpha = 3\nprint(alpha)"]


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    VimTextArea.enabled = True
    yield
    VimTextArea.enabled = True
    VimTextArea.line_numbers = True
    VimTextArea.search = None


def ui(tmp_path, test, sources=SOURCES):
    path = tmp_path / "nb.py"
    path.write_text("".join(f"# %%\n{s}\n\n" for s in sources))

    async def main():
        app = GPython(path)
        async with app.run_test(size=(120, 40)) as pilot:
            await app.loaded.wait()
            await pilot.pause()
            await test(app, pilot)

    asyncio.run(main())


def highlighted(editor, row):
    return [(span.start, span.end) for span in editor.get_line(row).spans if span.style == SEARCH_STYLE]


def painted(editor, y):
    return any(seg.style is not None and seg.style.bgcolor == SEARCH_STYLE.bgcolor for seg in editor.render_line(y))


def messages(app):
    return [n.message for n in app._notifications]


async def search(pilot, text):
    await pilot.press("/", *text, "enter")
    await pilot.pause()


def test_cell_mode_search_selects_matching_cells_and_wraps(tmp_path):
    async def test(app, pilot):
        first, second, third = app.cells()
        first.focus()
        await search(pilot, "alpha")
        assert app.focused is third and app.ui_mode == "cell"
        await pilot.press("n")
        await pilot.pause()
        assert app.focused is first
        await pilot.press("n")
        await pilot.pause()
        assert app.focused is third
        await pilot.press("N")
        await pilot.pause()
        assert app.focused is first
        await pilot.press("N")
        await pilot.pause()
        assert app.focused is third
        await search(pilot, "gamma")
        assert app.focused is second and app.ui_mode == "cell"

    ui(tmp_path, test)


def test_normal_mode_search_puts_the_cursor_on_matches(tmp_path):
    async def test(app, pilot):
        first, second, third = app.cells()
        first.editor.focus()
        first.editor.move_cursor((0, 0))
        await search(pilot, "alpha")
        assert app.focused is first.editor and first.editor.cursor_location == (1, 7)
        assert app.ui_mode == "normal"
        await pilot.press("n")
        await pilot.pause()
        assert app.focused is third.editor and third.editor.cursor_location == (0, 0)
        await pilot.press("n")
        await pilot.pause()
        assert third.editor.cursor_location == (1, 6)
        await pilot.press("n")
        await pilot.pause()
        assert app.focused is first.editor and first.editor.cursor_location == (0, 0)
        await pilot.press("N")
        await pilot.pause()
        assert app.focused is third.editor and third.editor.cursor_location == (1, 6)
        await pilot.press("N")
        await pilot.pause()
        assert third.editor.cursor_location == (0, 0)
        await pilot.press("2", "n")
        await pilot.pause()
        assert app.focused is first.editor and first.editor.cursor_location == (0, 0)
        assert app.ui_mode == "normal"

    ui(tmp_path, test)


def test_smart_case_and_not_found(tmp_path):
    async def test(app, pilot):
        first, second, third = app.cells()
        first.editor.focus()
        first.editor.move_cursor((0, 0))
        await search(pilot, "Alpha")
        assert app.focused is third.editor and third.editor.cursor_location == (0, 0)
        await pilot.press("n")
        await pilot.pause()
        assert app.focused is third.editor and third.editor.cursor_location == (0, 0)
        assert highlighted(third.editor, 0) == [(0, 5)]
        assert highlighted(third.editor, 1) == []

        third.focus()
        await search(pilot, "BETA")
        assert app.focused is third
        assert any("Not found: BETA" in m for m in messages(app))
        await search(pilot, "beta")
        assert app.focused is first

    ui(tmp_path, test)


def test_star_and_hash_search_the_word_under_the_cursor(tmp_path):
    async def test(app, pilot):
        first, second, third = app.cells()
        first.editor.focus()
        first.editor.move_cursor((0, 2))
        await pilot.press("*")
        await pilot.pause()
        assert app.focused is first.editor and first.editor.cursor_location == (1, 7)
        assert app.pane.search_pattern == "alpha"
        await pilot.press("n")
        await pilot.pause()
        assert app.focused is third.editor and third.editor.cursor_location == (0, 0)
        third.editor.move_cursor((1, 8))
        await pilot.press("#")
        await pilot.pause()
        assert app.focused is third.editor and third.editor.cursor_location == (0, 0)

    ui(tmp_path, test)


def test_n_without_a_search(tmp_path):
    async def test(app, pilot):
        first = app.cells()[0]
        first.editor.focus()
        await pilot.press("n")
        await pilot.pause()
        assert any("No previous search" in m for m in messages(app))
        first.focus()
        await pilot.press("N")
        await pilot.pause()
        assert app.focused is first

    ui(tmp_path, test)


def test_highlight_noh_and_escape(tmp_path):
    async def test(app, pilot):
        first, second, third = app.cells()
        assert highlighted(first.editor, 0) == [] and not painted(first.editor, 0)
        first.focus()
        await search(pilot, "alpha")
        assert highlighted(first.editor, 0) == [(0, 5)] and painted(first.editor, 0)
        assert highlighted(first.editor, 1) == [(7, 12)]
        assert highlighted(third.editor, 0) == [(0, 5)]
        assert highlighted(second.editor, 0) == []

        await pilot.press(":", *"noh", "enter")
        await pilot.pause()
        assert VimTextArea.search is None
        assert highlighted(first.editor, 0) == [] and not painted(first.editor, 0)
        assert app.pane.search_pattern == "alpha"

        await pilot.press("n")
        await pilot.pause()
        assert highlighted(first.editor, 0) == [(0, 5)]
        assert isinstance(app.focused, type(first))
        await pilot.press("escape")
        await pilot.pause()
        assert highlighted(first.editor, 0) == [] and highlighted(third.editor, 0) == []
        assert not painted(first.editor, 0)

    ui(tmp_path, test)
