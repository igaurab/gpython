import asyncio

import pytest
from textual.color import Color
from textual.theme import BUILTIN_THEMES
from textual.widgets.text_area import TextAreaTheme

from gpython import config
from gpython.app import GPython
from gpython.vim import VimTextArea
from gpython.themes import (
    CUSTOM_APP_THEMES,
    DEFAULT_THEME,
    PALETTES,
    TERMINAL_PALETTE,
    TERMINAL_THEME,
    Palette,
    editor_themes,
    palette_for,
)

SYNTAX_KEYS = [
    "comment",
    "keyword", "keyword.operator", "conditional", "repeat", "exception", "include",
    "keyword.return", "keyword.function",
    "string", "string.documentation",
    "number", "float",
    "function", "function.call", "method", "method.call",
    "class", "type", "type.class", "type.builtin",
    "operator",
    "variable.builtin",
    "boolean", "constant.builtin",
    "punctuation.bracket", "punctuation.delimiter", "punctuation.special",
    "heading", "heading.marker", "bold",
    "inline_code", "link.uri", "link.label", "list.marker", "italic", "strikethrough",
]

SUPPORTED = [
    "catppuccin-mocha", "catppuccin-macchiato", "catppuccin-frappe", "catppuccin-latte",
    "gruvbox", "gruvbox-light",
    "material-oceanic", "material-palenight", "material-darker", "material-lighter",
    "nord", "dracula", "tokyo-night", "solarized-dark", "solarized-light", "monokai",
]

LIGHT = {
    "catppuccin-latte", "gruvbox-light", "material-lighter", "solarized-light",
    "nord-light", "dracula-light", "tokyo-night-light", "monokai-light", "hiberbee-light",
}


def app_theme(name):
    return BUILTIN_THEMES.get(name) or CUSTOM_APP_THEMES[name]


def test_supported_set_and_default_present():
    assert set(SUPPORTED) <= set(PALETTES)
    assert DEFAULT_THEME == "terminal" and palette_for(TERMINAL_THEME) is TERMINAL_PALETTE


def test_terminal_theme_uses_the_terminals_own_colours(notebook_path):
    """Native ANSI colours (terminal background and palette) while "terminal" is on, and
    back to truecolor after switching to a regular theme."""
    assert TERMINAL_THEME.ansi and TERMINAL_THEME.background == "ansi_default"
    insert, normal = editor_themes(TERMINAL_PALETTE)
    assert insert.cursor_style.reverse

    async def main():
        app = GPython(notebook_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert app.theme == "terminal" and app.native_ansi_color
            assert editor_names(app) == {"terminal-normal"}
            app.cells()[0].set_status("ok")
            await pilot.pause()
            app.theme = "nord"
            await pilot.pause()
            assert not app.native_ansi_color
            await app._run_ex("theme default")
            await pilot.pause()
            assert app.theme == "terminal" and app.native_ansi_color

    asyncio.run(main())


@pytest.mark.parametrize("name", sorted(PALETTES))
def test_palette_names_resolve_to_app_themes(name):
    assert name in BUILTIN_THEMES or name in CUSTOM_APP_THEMES
    assert PALETTES[name].name == name


HIBERBEE_COLORS = {
    "#f25022": ("keyword", "keyword.function"),
    "#ee7762": ("conditional",),
    "#7fdbca": ("repeat", "function", "class", "function.builtin", "type.class"),
    "#92d923": ("include", "number"),
    "#ffb900": ("string",),
    "#bbbab9": ("function.call", "operator"),
}


def test_hiberbee_has_the_colors_of_neovims_python_syntax():
    palette = PALETTES["hiberbee"]
    for theme in editor_themes(palette):
        styles = theme.syntax_styles
        seen = {
            color: tuple(styles[key].color.triplet.hex.lower() for key in keys)
            for color, keys in HIBERBEE_COLORS.items()
        }
        assert seen == {color: (color,) * len(keys) for color, keys in HIBERBEE_COLORS.items()}
        assert styles["class"].bold and styles["link.uri"].underline
    assert app_theme("hiberbee").background == palette.bg


def contrast(first, second):
    def luminance(color):
        parts = [v / 255 for v in Color.parse(color).rgb]
        r, g, b = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in parts]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_hiberbee_light_gives_each_word_the_same_color_role_and_is_easy_to_read():
    dark, light = PALETTES["hiberbee"], PALETTES["hiberbee-light"]
    assert [key for key, _ in light.syntax_colors] == [key for key, _ in dark.syntax_colors]
    styles = editor_themes(light)[0].syntax_styles
    colors = {style.color.triplet.hex for key, style in styles.items() if key != "comment"}
    assert all(contrast(color, light.bg) >= 4.5 for color in colors)
    assert contrast(light.cursor_normal, light.bg) >= 4.5
    assert app_theme("hiberbee-light").background == light.bg


def test_palettes_without_syntax_colors_keep_their_group_colors():
    styles = editor_themes(PALETTES["nord"])[0].syntax_styles
    assert PALETTES["nord"].syntax_colors == ()
    assert styles["repeat"] == styles["keyword"] and styles["function.call"] == styles["function"]


def test_custom_themes_do_not_shadow_builtins():
    assert not set(CUSTOM_APP_THEMES) & set(BUILTIN_THEMES)
    for name, theme in CUSTOM_APP_THEMES.items():
        assert theme.name == name
        assert name in PALETTES


@pytest.mark.parametrize("name", sorted(PALETTES))
def test_dark_flag_matches_app_theme(name):
    assert PALETTES[name].dark == (name not in LIGHT)
    assert app_theme(name).dark == PALETTES[name].dark


def test_every_dark_theme_family_has_a_light_variant():
    light = {n for n, p in PALETTES.items() if not p.dark}
    assert light == LIGHT


@pytest.mark.parametrize("name", sorted(PALETTES))
def test_editor_themes(name):
    palette = PALETTES[name]
    insert, normal = editor_themes(palette)
    assert isinstance(insert, TextAreaTheme) and isinstance(normal, TextAreaTheme)
    assert insert.name == name
    assert normal.name == f"{name}-normal"
    for theme in (insert, normal):
        missing = [k for k in SYNTAX_KEYS if k not in theme.syntax_styles]
        assert not missing
        assert theme.base_style.bgcolor.triplet.hex.lower() == palette.bg.lower()
    assert insert.cursor_style.bgcolor.triplet.hex.lower() == palette.cursor_insert.lower()
    assert normal.cursor_style.bgcolor.triplet.hex.lower() == palette.cursor_normal.lower()
    assert insert.syntax_styles["comment"].italic
    assert insert.syntax_styles["keyword.function"].bold


@pytest.mark.parametrize("name", sorted(BUILTIN_THEMES))
def test_palette_for_never_raises(name):
    theme = BUILTIN_THEMES[name]
    palette = palette_for(theme)
    assert isinstance(palette, Palette)
    assert palette.name == name
    assert palette.dark == theme.dark
    editor_themes(palette)


def test_palette_for_supported_returns_exact_data():
    for name in PALETTES:
        assert palette_for(app_theme(name)) is PALETTES[name]


def test_generic_palette_follows_app_theme():
    theme = BUILTIN_THEMES["textual-light"]
    palette = palette_for(theme)
    assert palette.bg.lower() == theme.background.lower()
    assert palette.keyword.lower() == theme.primary.lower()
    assert not palette.dark


@pytest.fixture
def notebook_path(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    path = tmp_path / "nb.py"
    path.write_text("# %%\nx = 1\n\n# %%\ny = x + 1\n")
    return path


def editor_names(app):
    return {e.theme for e in app.query(VimTextArea)}


def test_switch_theme_updates_every_editor(notebook_path):
    async def main():
        app = GPython(notebook_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert editor_names(app) == {f"{DEFAULT_THEME}-normal"}
            app.theme = "gruvbox"
            await pilot.pause()
            assert editor_names(app) == {"gruvbox-normal"}
            assert app.palette is PALETTES["gruvbox"]

            await pilot.press("i")
            app.theme = "nord"
            await pilot.pause()
            assert editor_names(app) == {"nord"}

            await pilot.press("escape", "escape", "ctrl+n")
            await pilot.pause()
            assert len(app.cells()) == 3
            assert app.current_cell().editor.theme == "nord"
            await pilot.press("escape")
            assert app.current_cell().editor.theme == "nord-normal"
            assert config.load() == {"theme": "nord"}

    asyncio.run(main())


def test_theme_persists_and_ex_command(notebook_path):
    async def main():
        app = GPython(notebook_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert config.load() == {}
            await app._run_ex("theme material-palenight")
            await pilot.pause()
            assert app.theme == "material-palenight"
            await app._run_ex("theme no-such-theme")
            assert app.theme == "material-palenight"
        assert config.load()["theme"] == "material-palenight"

        assert GPython(notebook_path).theme == "material-palenight"
        assert GPython(notebook_path, theme="dracula").theme == "dracula"

    asyncio.run(main())


def test_unknown_theme_falls_back_to_default(notebook_path):
    async def main():
        app = GPython(notebook_path, theme="does-not-exist")
        assert app.theme == DEFAULT_THEME
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert any("does-not-exist" in n.message for n in app._notifications)
            assert editor_names(app) == {f"{DEFAULT_THEME}-normal"}
        assert config.load() == {}

    asyncio.run(main())


def test_light_theme_editor_background(notebook_path):
    async def main():
        app = GPython(notebook_path, theme="gruvbox-light")
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert not app.current_theme.dark
            bg = app.cells()[0].editor._theme.base_style.bgcolor.triplet.hex.lower()
            assert bg == PALETTES["gruvbox-light"].bg.lower()

    asyncio.run(main())
