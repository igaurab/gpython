import asyncio

import pytest
from textual.widgets import OptionList

from gpython import config
from gpython.app import GPython
from gpython.themepicker import OTHER_ID, ThemePicker
from gpython.themes import DEFAULT_THEME, PALETTES
from gpython.vim import VimTextArea


@pytest.fixture
def notebook_path(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    path = tmp_path / "nb.py"
    path.write_text("# %%\nx = 1\n\n# %%\ny = x + 1\n")
    return path


def option_ids(app):
    options = app.screen.query_one(OptionList)
    return [options.get_option_at_index(i).id for i in range(options.option_count)]


async def open_picker(app, pilot):
    await pilot.pause()
    await pilot.press("ctrl+t")
    await pilot.pause()
    assert isinstance(app.screen, ThemePicker)
    assert app._previewing_theme


def test_order_and_current_highlighted(notebook_path):
    async def main():
        app = GPython(notebook_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await open_picker(app, pilot)
            ids = option_ids(app)
            split = ids.index(OTHER_ID)
            ours, others = ids[:split], ids[split + 1 :]
            assert set(ours) == set(PALETTES) & set(app.available_themes)
            darks = [n for n in ours if app.available_themes[n].dark]
            assert ours[: len(darks)] == sorted(darks)  # dark group first, sorted
            assert ours[len(darks) :] == sorted(n for n in ours if not app.available_themes[n].dark)
            assert others == sorted(others) and not set(others) & set(PALETTES)
            options = app.screen.query_one(OptionList)
            assert ids[options.highlighted] == DEFAULT_THEME

    asyncio.run(main())


def test_down_previews_and_escape_restores(notebook_path):
    async def main():
        app = GPython(notebook_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await open_picker(app, pilot)
            await pilot.press("down")
            await pilot.pause()
            previewed = app.theme
            assert previewed != DEFAULT_THEME  # applied before any confirm
            assert {e.theme for e in app.query(VimTextArea)} == {f"{previewed}-normal"}
            assert previewed in str(app.query_one("#status").render())
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, ThemePicker)
            assert app.theme == DEFAULT_THEME
            assert not app._previewing_theme
        assert config.load() == {}

    asyncio.run(main())


def test_enter_confirms_and_persists(notebook_path):
    async def main():
        app = GPython(notebook_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await open_picker(app, pilot)
            await pilot.press("down", "down")
            await pilot.pause()
            chosen = app.theme
            assert chosen != DEFAULT_THEME
            assert config.load() == {}  # previews are not written
            await pilot.press("enter")
            await pilot.pause()
            assert not isinstance(app.screen, ThemePicker)
            assert app.theme == chosen
            assert not app._previewing_theme
        assert config.load() == {"theme": chosen}

    asyncio.run(main())


def test_filter_gruv_previews_first_match(notebook_path):
    async def main():
        app = GPython(notebook_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await open_picker(app, pilot)
            await pilot.press(*"gruv")
            await pilot.pause()
            ids = option_ids(app)
            assert ids and all("gruvbox" in n for n in ids)
            assert app.theme == ids[0]
            await pilot.press("down")
            await pilot.pause()
            assert len(ids) == 1 or app.theme == ids[1]
            await pilot.press("escape")
            await pilot.pause()
            assert app.theme == DEFAULT_THEME
        assert config.load() == {}

    asyncio.run(main())


def test_palette_change_theme_opens_picker(notebook_path):
    async def main():
        app = GPython(notebook_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            commands = list(app.get_system_commands(app.screen))
            titles = [c.title for c in commands]
            assert "Change theme" in titles and "Theme" not in titles
            next(c for c in commands if c.title == "Change theme").callback()
            await pilot.pause()
            assert isinstance(app.screen, ThemePicker)

    asyncio.run(main())
