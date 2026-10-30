"""Colorschemes: palette data, custom app themes and editor (TextArea) themes.

A `Palette` holds the colors a syntax theme needs. `editor_themes()` turns a palette into
two `TextAreaTheme`s (insert mode and normal mode, which only differ in the cursor).
`palette_for()` returns the palette for any Textual app theme, deriving a generic one for
themes we do not ship explicit data for.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.style import Style
from textual.color import Color
from textual.theme import Theme
from textual.widgets.text_area import TextAreaTheme

DEFAULT_THEME = "material-darker"
THEME_ALIASES = {"material-dark": "material-darker", "material": "material-darker", "gruvbox-dark": "gruvbox", "catppuccin": "catppuccin-mocha"}


@dataclass(frozen=True)
class Palette:
    """Colors a syntax theme needs. All hex strings."""

    name: str  # app theme id, e.g. "catppuccin-mocha"
    dark: bool
    bg: str
    fg: str
    gutter_fg: str
    cursor_line_bg: str
    selection_bg: str
    comment: str
    keyword: str
    string: str
    number: str
    function: str
    type: str
    operator: str
    builtin: str
    constant: str
    punctuation: str
    cursor_insert: str  # bar/block color in insert mode
    cursor_normal: str  # block color in normal mode (distinct, e.g. yellow/peach)


# --------------------------------------------------------------------------- catppuccin

_CATPPUCCIN = {
    #               mocha      macchiato  frappe     latte
    "base":      ("#1e1e2e", "#24273a", "#303446", "#eff1f5"),
    "surface0":  ("#313244", "#363a4f", "#414559", "#ccd0da"),
    "surface2":  ("#585b70", "#5b6078", "#626880", "#acb0be"),
    "overlay0":  ("#6c7086", "#6e738d", "#737994", "#9ca0b0"),
    "overlay2":  ("#9399b2", "#939ab7", "#949cbb", "#7c7f93"),
    "text":      ("#cdd6f4", "#cad3f5", "#c6d0f5", "#4c4f69"),
    "rosewater": ("#f5e0dc", "#f4dbd6", "#f2d5cf", "#dc8a78"),
    "red":       ("#f38ba8", "#ed8796", "#e78284", "#d20f39"),
    "peach":     ("#fab387", "#f5a97f", "#ef9f76", "#fe640b"),
    "yellow":    ("#f9e2af", "#eed49f", "#e5c890", "#df8e1d"),
    "green":     ("#a6e3a1", "#a6da95", "#a6d189", "#40a02b"),
    "sky":       ("#89dceb", "#91d7e3", "#99d1db", "#04a5e5"),
    "blue":      ("#89b4fa", "#8aadf4", "#8caaee", "#1e66f5"),
    "mauve":     ("#cba6f7", "#c6a0f6", "#ca9ee6", "#8839ef"),
}  # fmt: skip


def _catppuccin(flavor: str, i: int) -> Palette:
    c = {role: values[i] for role, values in _CATPPUCCIN.items()}
    return Palette(
        name=f"catppuccin-{flavor}",
        dark=flavor != "latte",
        bg=c["base"],
        fg=c["text"],
        gutter_fg=c["overlay0"],
        cursor_line_bg=c["surface0"],
        selection_bg=c["surface2"],
        comment=c["overlay0"],
        keyword=c["mauve"],
        string=c["green"],
        number=c["peach"],
        function=c["blue"],
        type=c["yellow"],
        operator=c["sky"],
        builtin=c["red"],
        constant=c["peach"],
        punctuation=c["overlay2"],
        cursor_insert=c["rosewater"],
        cursor_normal=c["yellow"],
    )


# --------------------------------------------------------------------------- material

_MAT_RED, _MAT_ORANGE, _MAT_YELLOW, _MAT_GREEN = "#F07178", "#F78C6C", "#FFCB6B", "#C3E88D"
_MAT_CYAN, _MAT_BLUE, _MAT_PURPLE = "#89DDFF", "#82AAFF", "#C792EA"
# The shared accents are made for dark backgrounds (string is 1.3:1 on #FAFAFA), so the
# lighter variant uses Material Lighter's own darker accent set.
_MAT_LIGHTER = dict(
    red="#E53935", orange="#F76D47", yellow="#F6A434", green="#91B859",
    cyan="#39ADB5", blue="#6182B8", purple="#7C4DFF",
)  # fmt: skip


def _material_accents(variant: str) -> dict[str, str]:
    if variant == "lighter":
        return _MAT_LIGHTER
    return dict(
        red=_MAT_RED, orange=_MAT_ORANGE, yellow=_MAT_YELLOW, green=_MAT_GREEN,
        cyan=_MAT_CYAN, blue=_MAT_BLUE, purple=_MAT_PURPLE,
    )  # fmt: skip


_MATERIAL = {
    # variant:   (bg,        fg,        gutter_fg, cursor_line_bg, selection_bg, comment, surface, panel)
    "oceanic":   ("#263238", "#EEFFFF", "#546E7A", "#2C3B41", "#314549", "#546E7A", "#2E3C43", "#314549"),
    "palenight": ("#292D3E", "#A6ACCD", "#4A4E6A", "#32374D", "#3C435E", "#676E95", "#32374D", "#3C435E"),
    "darker":    ("#212121", "#EEFFFF", "#424242", "#2B2B2B", "#353535", "#616161", "#292929", "#353535"),
    "lighter":   ("#FAFAFA", "#546E7A", "#CFD8DC", "#F0F0F0", "#E0E0E0", "#90A4AE", "#F0F0F0", "#E7E7E8"),
}  # fmt: skip


def _material_palette(variant: str) -> Palette:
    bg, fg, gutter, line, sel, comment, _surface, _panel = _MATERIAL[variant]
    lighter = variant == "lighter"
    a = _material_accents(variant)
    return Palette(
        name=f"material-{variant}",
        dark=not lighter,
        bg=bg,
        fg=fg,
        gutter_fg=gutter,
        cursor_line_bg=line,
        selection_bg=sel,
        comment=comment,
        keyword=a["purple"],
        string=a["green"],
        number=a["orange"],
        function=a["blue"],
        type=a["yellow"],
        operator=a["cyan"],
        builtin=a["red"],
        constant=a["orange"],
        punctuation=a["cyan"],
        cursor_insert="#272727" if lighter else "#FFCC00",
        cursor_normal="#F76D47" if lighter else "#FFCB6B",
    )


def _material_theme(variant: str) -> Theme:
    bg, fg, _g, _l, _s, _c, surface, panel = _MATERIAL[variant]
    a = _material_accents(variant)
    return Theme(
        name=f"material-{variant}",
        primary=a["blue"],
        secondary=a["purple"],
        accent=a["yellow"],
        warning=a["orange"],
        error=a["red"],
        success=a["green"],
        foreground=fg,
        background=bg,
        surface=surface,
        panel=panel,
        dark=variant != "lighter",
    )


# --------------------------------------------------------------------------- the rest

_SOLARIZED_ACCENTS = dict(
    keyword="#859900",
    string="#2aa198",
    number="#d33682",
    function="#268bd2",
    type="#b58900",
    builtin="#cb4b16",
    constant="#6c71c4",
)

_PALETTE_LIST = [
    _catppuccin("mocha", 0),
    _catppuccin("macchiato", 1),
    _catppuccin("frappe", 2),
    _catppuccin("latte", 3),
    Palette(
        name="gruvbox", dark=True,
        bg="#282828", fg="#ebdbb2", gutter_fg="#7c6f64", cursor_line_bg="#3c3836",
        selection_bg="#504945", comment="#928374", keyword="#fb4934", string="#b8bb26",
        number="#d3869b", function="#fabd2f", type="#8ec07c", operator="#fe8019",
        builtin="#83a598", constant="#d3869b", punctuation="#a89984",
        cursor_insert="#ebdbb2", cursor_normal="#fabd2f",
    ),
    Palette(
        name="gruvbox-light", dark=False,
        bg="#fbf1c7", fg="#3c3836", gutter_fg="#a89984", cursor_line_bg="#ebdbb2",
        selection_bg="#d5c4a1", comment="#928374", keyword="#9d0006", string="#79740e",
        number="#8f3f71", function="#b57614", type="#427b58", operator="#af3a03",
        builtin="#076678", constant="#8f3f71", punctuation="#7c6f64",
        cursor_insert="#3c3836", cursor_normal="#b57614",
    ),
    _material_palette("oceanic"),
    _material_palette("palenight"),
    _material_palette("darker"),
    _material_palette("lighter"),
    Palette(
        name="nord", dark=True,
        bg="#2E3440", fg="#D8DEE9", gutter_fg="#4C566A", cursor_line_bg="#3B4252",
        selection_bg="#434C5E", comment="#616E88", keyword="#81A1C1", string="#A3BE8C",
        number="#B48EAD", function="#88C0D0", type="#8FBCBB", operator="#81A1C1",
        builtin="#D08770", constant="#B48EAD", punctuation="#ECEFF4",
        cursor_insert="#D8DEE9", cursor_normal="#EBCB8B",
    ),
    Palette(
        name="dracula", dark=True,
        bg="#282a36", fg="#f8f8f2", gutter_fg="#6272a4", cursor_line_bg="#44475a",
        selection_bg="#44475a", comment="#6272a4", keyword="#ff79c6", string="#f1fa8c",
        number="#bd93f9", function="#50fa7b", type="#8be9fd", operator="#ff79c6",
        builtin="#bd93f9", constant="#bd93f9", punctuation="#f8f8f2",
        cursor_insert="#f8f8f2", cursor_normal="#ffb86c",
    ),
    Palette(
        name="tokyo-night", dark=True,
        bg="#1a1b26", fg="#c0caf5", gutter_fg="#3b4261", cursor_line_bg="#292e42",
        selection_bg="#33467c", comment="#565f89", keyword="#bb9af7", string="#9ece6a",
        number="#ff9e64", function="#7aa2f7", type="#2ac3de", operator="#89ddff",
        builtin="#f7768e", constant="#ff9e64", punctuation="#a9b1d6",
        cursor_insert="#c0caf5", cursor_normal="#e0af68",
    ),
    Palette(
        name="solarized-dark", dark=True,
        bg="#002b36", fg="#839496", gutter_fg="#586e75", cursor_line_bg="#073642",
        selection_bg="#073642", comment="#586e75", operator="#93a1a1", punctuation="#93a1a1",
        cursor_insert="#93a1a1", cursor_normal="#b58900", **_SOLARIZED_ACCENTS,
    ),
    Palette(
        # operator/punctuation use base01 (#586e75): base1 (#93a1a1) is unreadable on base3.
        name="solarized-light", dark=False,
        bg="#fdf6e3", fg="#657b83", gutter_fg="#93a1a1", cursor_line_bg="#eee8d5",
        selection_bg="#eee8d5", comment="#93a1a1", operator="#586e75", punctuation="#586e75",
        cursor_insert="#586e75", cursor_normal="#b58900", **_SOLARIZED_ACCENTS,
    ),
    Palette(
        # Values taken from Textual's builtin TextAreaTheme "monokai".
        name="monokai", dark=True,
        bg="#272822", fg="#f8f8f2", gutter_fg="#90908a", cursor_line_bg="#3e3d32",
        selection_bg="#65686a", comment="#75715e", keyword="#f92672", string="#e6db74",
        number="#ae81ff", function="#a6e22e", type="#a6e22e", operator="#f8f8f2",
        builtin="#f8f8f2", constant="#66d9ef", punctuation="#f8f8f2",
        cursor_insert="#f8f8f0", cursor_normal="#e6db74",
    ),
    # ---- light variants of dark-only themes (custom app themes below)
    Palette(
        # Nord Snow Storm background with the darker "nord-light" accents for contrast.
        name="nord-light", dark=False,
        bg="#ECEFF4", fg="#2E3440", gutter_fg="#9AA3B5", cursor_line_bg="#E5E9F0",
        selection_bg="#D8DEE9", comment="#7B88A1", keyword="#3B6EA8", string="#4F894C",
        number="#97365B", function="#398EAC", type="#29838D", operator="#3B6EA8",
        builtin="#AC4426", constant="#97365B", punctuation="#4C566A",
        cursor_insert="#3B4252", cursor_normal="#9A7500",
    ),
    Palette(
        # Dracula's official light variant ("Alucard").
        name="dracula-light", dark=False,
        bg="#FFFBEB", fg="#1F1F1F", gutter_fg="#9F9BB8", cursor_line_bg="#F2EDD8",
        selection_bg="#CFCFDE", comment="#635D97", keyword="#A3144D", string="#846E15",
        number="#644AC9", function="#14710A", type="#036A96", operator="#A3144D",
        builtin="#644AC9", constant="#644AC9", punctuation="#1F1F1F",
        cursor_insert="#1F1F1F", cursor_normal="#A34D14",
    ),
    Palette(
        # Tokyo Night "Day".
        name="tokyo-night-light", dark=False,
        bg="#e1e2e7", fg="#3760bf", gutter_fg="#a8aecb", cursor_line_bg="#d0d5e3",
        selection_bg="#b7c1e3", comment="#848cb5", keyword="#9854f1", string="#587539",
        number="#b15c00", function="#2e7de9", type="#007197", operator="#006a83",
        builtin="#f52a65", constant="#b15c00", punctuation="#6172b0",
        cursor_insert="#3760bf", cursor_normal="#8c6c3e",
    ),
    Palette(
        # Monokai Pro "Light".
        name="monokai-light", dark=False,
        bg="#faf4f2", fg="#29242a", gutter_fg="#bfb9ba", cursor_line_bg="#efe8e6",
        selection_bg="#e0d9d7", comment="#a59fa0", keyword="#e14775", string="#cc7a0a",
        number="#7058be", function="#269d69", type="#1c8ca8", operator="#e14775",
        builtin="#e16032", constant="#7058be", punctuation="#706b6e",
        cursor_insert="#29242a", cursor_normal="#cc7a0a",
    ),
]  # fmt: skip

PALETTES: dict[str, Palette] = {p.name: p for p in _PALETTE_LIST}

CUSTOM_APP_THEMES: dict[str, Theme] = {
    "gruvbox-light": Theme(
        name="gruvbox-light",
        primary="#076678",
        secondary="#427b58",
        accent="#b57614",
        warning="#af3a03",
        error="#9d0006",
        success="#79740e",
        foreground="#3c3836",
        background="#fbf1c7",
        surface="#f2e5bc",
        panel="#ebdbb2",
        dark=False,
    ),
    **{f"material-{v}": _material_theme(v) for v in ("oceanic", "palenight", "darker", "lighter")},
    "nord-light": Theme(
        name="nord-light", primary="#3B6EA8", secondary="#29838D", accent="#AC4426",
        warning="#9A7500", error="#99324B", success="#4F894C", foreground="#2E3440",
        background="#ECEFF4", surface="#E5E9F0", panel="#D8DEE9", dark=False,
    ),
    "dracula-light": Theme(
        name="dracula-light", primary="#644AC9", secondary="#A3144D", accent="#036A96",
        warning="#A34D14", error="#CB3A2A", success="#14710A", foreground="#1F1F1F",
        background="#FFFBEB", surface="#F5F0DC", panel="#ECE6CF", dark=False,
    ),
    "tokyo-night-light": Theme(
        name="tokyo-night-light", primary="#2e7de9", secondary="#9854f1", accent="#b15c00",
        warning="#8c6c3e", error="#c64343", success="#587539", foreground="#3760bf",
        background="#e1e2e7", surface="#d0d5e3", panel="#c4c8da", dark=False,
    ),
    "monokai-light": Theme(
        name="monokai-light", primary="#7058be", secondary="#e14775", accent="#1c8ca8",
        warning="#e16032", error="#e14775", success="#269d69", foreground="#29242a",
        background="#faf4f2", surface="#f2ebe9", panel="#e6dfdd", dark=False,
    ),
}  # fmt: skip


# --------------------------------------------------------------------------- factories


def editor_themes(palette: Palette) -> tuple[TextAreaTheme, TextAreaTheme]:
    """(insert_theme, normal_theme) named f"{palette.name}" and f"{palette.name}-normal"."""
    p = palette

    def c(color: str, **kw) -> Style:
        return Style(color=color, **kw)

    syntax: dict[str, Style] = {"comment": c(p.comment, italic=True)}
    for key in ("keyword", "keyword.operator", "conditional", "repeat", "exception", "include",
                "keyword.return"):  # fmt: skip
        syntax[key] = c(p.keyword)
    syntax["keyword.function"] = c(p.keyword, bold=True)
    for key in ("string", "string.documentation"):
        syntax[key] = c(p.string)
    for key in ("number", "float"):
        syntax[key] = c(p.number)
    for key in ("function", "function.call", "method", "method.call"):
        syntax[key] = c(p.function)
    for key in ("type", "type.class", "type.builtin"):
        syntax[key] = c(p.type)
    syntax["class"] = c(p.type, bold=True)
    syntax["operator"] = c(p.operator)
    syntax["variable.builtin"] = c(p.builtin)
    for key in ("boolean", "constant.builtin"):
        syntax[key] = c(p.constant)
    for key in ("punctuation.bracket", "punctuation.delimiter", "punctuation.special"):
        syntax[key] = c(p.punctuation)
    # markdown
    syntax["heading"] = c(p.keyword, bold=True)
    syntax["heading.marker"] = c(p.punctuation)
    syntax["bold"] = c(p.fg, bold=True)
    syntax["inline_code"] = c(p.string)
    syntax["link.uri"] = c(p.function, underline=True)
    syntax["link.label"] = c(p.type)
    syntax["list.marker"] = c(p.number)
    syntax["italic"] = c(p.fg, italic=True)
    syntax["strikethrough"] = c(p.fg, strike=True)

    def make(name: str, cursor: Style) -> TextAreaTheme:
        return TextAreaTheme(
            name=name,
            base_style=Style(color=p.fg, bgcolor=p.bg),
            gutter_style=Style(color=p.gutter_fg, bgcolor=p.bg),
            cursor_style=cursor,
            cursor_line_style=Style(bgcolor=p.cursor_line_bg),
            cursor_line_gutter_style=Style(color=p.fg, bgcolor=p.cursor_line_bg),
            bracket_matching_style=Style(bgcolor=p.selection_bg, bold=True),
            selection_style=Style(bgcolor=p.selection_bg),
            syntax_styles=dict(syntax),
        )

    insert = make(p.name, Style(color=p.bg, bgcolor=p.cursor_insert))
    normal = make(f"{p.name}-normal", Style(color=p.bg, bgcolor=p.cursor_normal, bold=True))
    return insert, normal


def _hex(value: str | None, default: str) -> str:
    """Plain #rrggbb for a Textual color string; `default` for None/ansi-default/transparent."""
    if not value:
        return default
    try:
        color = Color.parse(value)
    except Exception:
        return default
    if color.ansi == -1 or color.a == 0:
        return default
    return Color(color.r, color.g, color.b).hex


def palette_for(app_theme: Theme) -> Palette:
    """PALETTES[name] if supported, else a generic palette derived from the app theme's colors."""
    if app_theme.name in PALETTES:
        return PALETTES[app_theme.name]
    dark = app_theme.dark
    bg = _hex(app_theme.background, "#1e1e1e" if dark else "#fafafa")
    fg = _hex(app_theme.foreground, "#d4d4d4" if dark else "#1f1f1f")
    primary = _hex(app_theme.primary, fg)
    secondary = _hex(app_theme.secondary, primary)
    accent = _hex(app_theme.accent, primary)
    warning = _hex(app_theme.warning, accent)
    error = _hex(app_theme.error, accent)
    success = _hex(app_theme.success, secondary)
    surface = _hex(app_theme.surface, bg)
    bg_c, fg_c = Color.parse(bg), Color.parse(fg)
    comment = bg_c.blend(fg_c, 0.5).hex
    line = surface if surface.lower() != bg.lower() else bg_c.blend(fg_c, 0.08).hex
    return Palette(
        name=app_theme.name,
        dark=dark,
        bg=bg,
        fg=fg,
        gutter_fg=comment,
        cursor_line_bg=line,
        selection_bg=bg_c.blend(fg_c, 0.2).hex,
        comment=comment,
        keyword=primary,
        string=success,
        number=accent,
        function=secondary,
        type=warning,
        operator=fg,
        builtin=error,
        constant=accent,
        punctuation=fg,
        cursor_insert=fg,
        cursor_normal=warning,
    )
