# Plan 02 – Colorschemes

Read `docs/plans/README.md` first. Plan 01 is done: outputs live in `outputs.py`, `VimTextArea` has
`set_editor_theme(insert_name, normal_name)` (if plan 01 did not add it, add it now as described below).

## Goal

The user picks a colorscheme and **everything** follows it: app chrome (Textual `Theme`), the code
editors (`TextAreaTheme` with a full Python syntax palette and a distinct normal-mode block cursor),
cell status colors, outputs. The choice persists across runs. Ships with:

| Name (app theme id) | Source | Light? |
|---|---|---|
| `catppuccin-mocha` (**default**), `catppuccin-macchiato`, `catppuccin-frappe`, `catppuccin-latte` | Textual builtin app themes; we add editor palettes | latte |
| `gruvbox`, `gruvbox-light` | builtin dark; **custom** light app theme | light |
| `material-oceanic`, `material-palenight`, `material-darker`, `material-lighter` | **custom** app themes | lighter |
| `nord`, `dracula`, `tokyo-night`, `solarized-dark`, `solarized-light`, `monokai` | builtin app themes; we add editor palettes | solarized-light |

Any other builtin Textual theme still works (falls back to a generic editor palette derived from the
app theme), but the list above is the supported set.

## Files

- **New `src/gpython/themes.py`** – all palette data and the two factory functions.
- **New `src/gpython/config.py`** – tiny TOML config read/write.
- `src/gpython/vim.py` – remove the hardcoded `monokai`; add `set_editor_theme`.
- `src/gpython/app.py` – register custom themes, watch `theme`, apply to editors, `ctrl+t`, `:theme`.
- `src/gpython/app.tcss` – no hardcoded colors; only `$variables`.
- `tests/test_themes.py`, `tests/test_config.py` (new).
- Do **not** edit `__init__.py` (CLI). Accept `GPython(path, vim=True, theme: str | None = None)`; the peer
  will wire `--theme` to it (request it in your report).

## `themes.py` design

```python
@dataclass(frozen=True)
class Palette:
    """Colors a syntax theme needs. All hex strings."""
    name: str            # app theme id, e.g. "catppuccin-mocha"
    dark: bool
    bg: str; fg: str; gutter_fg: str; cursor_line_bg: str; selection_bg: str
    comment: str; keyword: str; string: str; number: str; function: str
    type: str; operator: str; builtin: str; constant: str; punctuation: str
    cursor_insert: str   # bar/block color in insert mode
    cursor_normal: str   # block color in normal mode (distinct, e.g. yellow/peach)

PALETTES: dict[str, Palette]                      # one per supported theme
CUSTOM_APP_THEMES: dict[str, textual.theme.Theme]  # gruvbox-light, material-*
DEFAULT_THEME = "catppuccin-mocha"

def editor_themes(palette: Palette) -> tuple[TextAreaTheme, TextAreaTheme]:
    """(insert_theme, normal_theme) named f"{palette.name}" and f"{palette.name}-normal"."""

def palette_for(app_theme: textual.theme.Theme) -> Palette:
    """PALETTES[name] if supported, else a generic palette derived from theme.primary/secondary/
    accent/foreground/background/surface (keyword=primary, string=success or secondary,
    number=accent, function=secondary, type=warning, comment=foreground at 50 % blend with background)."""
```

`editor_themes` maps palette → `syntax_styles` (use `rich.style.Style(color=...)`; add `italic=True`
for `comment`, `bold=True` for `keyword.function`/`class`):

| syntax key(s) | palette field |
|---|---|
| `comment` | comment (italic) |
| `keyword`, `keyword.operator`, `conditional`, `repeat`, `exception`, `include`, `keyword.return` | keyword |
| `keyword.function` | keyword (bold) |
| `string`, `string.documentation` | string |
| `number`, `float` | number |
| `function`, `function.call`, `method`, `method.call` | function |
| `class`, `type`, `type.class`, `type.builtin` | type |
| `operator` | operator |
| `variable.builtin` (self, cls) | builtin |
| `boolean`, `constant.builtin` (None/True/False) | constant |
| `punctuation.bracket`, `punctuation.delimiter`, `punctuation.special` | punctuation |
| `heading`, `heading.marker`, `bold` | keyword / punctuation / fg bold |
| `inline_code`, `link.uri`, `link.label`, `list.marker`, `italic`, `strikethrough` | string, function, type, number, fg italic, fg strike |

Other fields: `base_style=Style(color=fg, bgcolor=bg)`, `gutter_style=Style(color=gutter_fg, bgcolor=bg)`,
`cursor_style=Style(color=bg, bgcolor=cursor_insert)` (normal variant: `bgcolor=cursor_normal, bold=True`),
`cursor_line_style=Style(bgcolor=cursor_line_bg)`, `cursor_line_gutter_style=Style(color=fg, bgcolor=cursor_line_bg)`,
`bracket_matching_style=Style(bgcolor=selection_bg, bold=True)`, `selection_style=Style(bgcolor=selection_bg)`.

### Palette data (exact hex; copy as is)

Catppuccin roles → fields: `bg=base, fg=text, gutter_fg=overlay0, cursor_line_bg=surface0,
selection_bg=surface2, comment=overlay0, keyword=mauve, string=green, number=peach, function=blue,
type=yellow, operator=sky, builtin=red, constant=peach, punctuation=overlay2, cursor_insert=rosewater,
cursor_normal=yellow`.

| role | mocha | macchiato | frappe | latte |
|---|---|---|---|---|
| base | #1e1e2e | #24273a | #303446 | #eff1f5 |
| surface0 | #313244 | #363a4f | #414559 | #ccd0da |
| surface2 | #585b70 | #5b6078 | #626880 | #acb0be |
| overlay0 | #6c7086 | #6e738d | #737994 | #9ca0b0 |
| overlay2 | #9399b2 | #939ab7 | #949cbb | #7c7f93 |
| text | #cdd6f4 | #cad3f5 | #c6d0f5 | #4c4f69 |
| rosewater | #f5e0dc | #f4dbd6 | #f2d5cf | #dc8a78 |
| red | #f38ba8 | #ed8796 | #e78284 | #d20f39 |
| peach | #fab387 | #f5a97f | #ef9f76 | #fe640b |
| yellow | #f9e2af | #eed49f | #e5c890 | #df8e1d |
| green | #a6e3a1 | #a6da95 | #a6d189 | #40a02b |
| sky | #89dceb | #91d7e3 | #99d1db | #04a5e5 |
| blue | #89b4fa | #8aadf4 | #8caaee | #1e66f5 |
| mauve | #cba6f7 | #c6a0f6 | #ca9ee6 | #8839ef |

Gruvbox dark: bg #282828, fg #ebdbb2, gutter_fg #7c6f64, cursor_line_bg #3c3836, selection_bg #504945,
comment #928374, keyword #fb4934, string #b8bb26, number #d3869b, function #fabd2f (bold), type #8ec07c,
operator #fe8019, builtin #83a598, constant #d3869b, punctuation #a89984, cursor_insert #ebdbb2,
cursor_normal #fabd2f.
Gruvbox light: bg #fbf1c7, fg #3c3836, gutter_fg #a89984, cursor_line_bg #ebdbb2, selection_bg #d5c4a1,
comment #928374, keyword #9d0006, string #79740e, number #8f3f71, function #b57614, type #427b58,
operator #af3a03, builtin #076678, constant #8f3f71, punctuation #7c6f64, cursor_insert #3c3836,
cursor_normal #b57614.
Custom app `Theme("gruvbox-light", primary="#076678", secondary="#427b58", accent="#b57614",
warning="#af3a03", error="#9d0006", success="#79740e", foreground="#3c3836", background="#fbf1c7",
surface="#f2e5bc", panel="#ebdbb2", dark=False)`.

Material (shared accents: red #F07178, orange #F78C6C, yellow #FFCB6B, green #C3E88D, cyan #89DDFF,
blue #82AAFF, purple #C792EA, brown #916B53):

| field | oceanic | palenight | darker | lighter |
|---|---|---|---|---|
| bg | #263238 | #292D3E | #212121 | #FAFAFA |
| fg | #EEFFFF | #A6ACCD | #EEFFFF | #80CBC4 → use #546E7A for readability |
| gutter_fg | #546E7A | #4A4E6A | #424242 | #CFD8DC |
| cursor_line_bg | #2C3B41 | #32374D | #2B2B2B | #F0F0F0 |
| selection_bg | #314549 | #3C435E | #353535 | #E0E0E0 |
| comment | #546E7A | #676E95 | #616161 | #90A4AE |
| surface (app) | #2E3C43 | #32374D | #292929 | #F0F0F0 |
| panel (app) | #314549 | #3C435E | #353535 | #E7E7E8 |

keyword=purple, string=green, number=orange, function=blue, type=yellow, operator=cyan, builtin=red,
constant=orange, punctuation=cyan, cursor_insert=#FFCC00 (lighter: #272727), cursor_normal=#FFCB6B
(lighter: #F76D47). App themes: `Theme(name, primary=blue, secondary=purple, accent=yellow,
warning=orange, error=red, success=green, foreground=fg, background=bg, surface=…, panel=…, dark=not lighter)`.

Nord: bg #2E3440, fg #D8DEE9, gutter_fg #4C566A, cursor_line_bg #3B4252, selection_bg #434C5E,
comment #616E88, keyword #81A1C1, string #A3BE8C, number #B48EAD, function #88C0D0, type #8FBCBB,
operator #81A1C1, builtin #D08770, constant #B48EAD, punctuation #ECEFF4, cursor_insert #D8DEE9,
cursor_normal #EBCB8B.
Dracula: bg #282a36, fg #f8f8f2, gutter_fg #6272a4, cursor_line_bg #44475a, selection_bg #44475a,
comment #6272a4, keyword #ff79c6, string #f1fa8c, number #bd93f9, function #50fa7b, type #8be9fd,
operator #ff79c6, builtin #bd93f9, constant #bd93f9, punctuation #f8f8f2, cursor_insert #f8f8f2,
cursor_normal #ffb86c.
Tokyo Night: bg #1a1b26, fg #c0caf5, gutter_fg #3b4261, cursor_line_bg #292e42, selection_bg #33467c,
comment #565f89, keyword #bb9af7, string #9ece6a, number #ff9e64, function #7aa2f7, type #2ac3de,
operator #89ddff, builtin #f7768e, constant #ff9e64, punctuation #a9b1d6, cursor_insert #c0caf5,
cursor_normal #e0af68.
Solarized dark: bg #002b36, fg #839496, gutter_fg #586e75, cursor_line_bg #073642, selection_bg #073642,
comment #586e75, keyword #859900, string #2aa198, number #d33682, function #268bd2, type #b58900,
operator #93a1a1, builtin #cb4b16, constant #6c71c4, punctuation #93a1a1, cursor_insert #93a1a1,
cursor_normal #b58900. Solarized light: bg #fdf6e3, fg #657b83, gutter_fg #93a1a1, cursor_line_bg #eee8d5,
selection_bg #eee8d5, comment #93a1a1, same accents, cursor_insert #586e75, cursor_normal #b58900.
Monokai: take the existing builtin `TextAreaTheme` "monokai" values (bg #272822, fg #f8f8f2) and
cursor_normal #e6db74 (what `vim.py` uses today).

## Wiring

1. `GPython.__init__(path, vim=True, theme=None)`: `for t in CUSTOM_APP_THEMES.values(): self.register_theme(t)`;
   resolve the theme: argument → config file → `DEFAULT_THEME`; if the name is unknown, `notify` a warning
   and use the default. Set `self.theme = name`.
2. `watch_theme(self, name)` in `GPython` (Textual calls it after its own `_watch_theme`): compute
   `palette_for(self.current_theme)`, build editor themes **once per palette** (cache in a dict on the
   app), then for every `VimTextArea` in the screen call `editor.set_editor_theme(insert, normal)`.
   Also store `self.palette` so `Cell._render_header` colors status icons with
   `palette`/theme colors: ok=`theme.success`, stale=`theme.warning`, error/skipped=`theme.error`,
   running/queued=`theme.primary`, idle=`comment`. Save the choice to config (step 4) unless it equals
   what was loaded. Also call `update_status()` so the status line can show the theme name.
3. `VimTextArea.set_editor_theme(insert: TextAreaTheme, normal: TextAreaTheme)` (added in plan 01;
   if missing, add it): `register_theme` both, remember their names, and set `self.theme` according to
   the current (global) mode. `_apply_mode` uses the remembered names instead of the `monokai` literals.
   New editors mounted later (`action_add_cell`, progressive mount) must get the current pair in
   `on_mount`: read `self.app.editor_theme_pair` if present.
4. Persistence – `config.py`:
   ```python
   CONFIG_PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "gpython" / "config.toml"
   def load() -> dict   # {} if missing or invalid (never raise)
   def save(values: dict) -> None  # merge into existing keys, write atomically (tmp + replace); only str/bool/int values
   ```
   Keys used now: `theme`. (Plan 03 adds `vim`.) Tests set `XDG_CONFIG_HOME` to `tmp_path`.
5. Picker: binding `ctrl+t` → `action_change_theme` (Textual's live-preview palette). Ex command
   `:theme <name>` sets it (error notify if unknown); `:theme` alone notifies the current name. Add a
   system command "Change theme" if Textual does not already list one (it does: "Change theme"; don't duplicate).
6. `app.tcss`: replace any literal colors with theme variables; cell border colors already use
   `$panel/$accent/$error/$warning/$primary`. Output background `$boost`; `CompletionPopup` uses
   `$surface`/`$accent`. Editor `background` must come from the `TextAreaTheme.base_style`, so set
   `Cell TextArea { background: transparent; }` is **wrong** – leave TextArea background to its theme.
7. Light themes: verify in `run_test` that `catppuccin-latte` and `gruvbox-light` produce `dark=False`
   and the editor base bg is the light bg.

## Tests

- `tests/test_themes.py`: every name in `PALETTES` is either a Textual builtin or in `CUSTOM_APP_THEMES`;
  `editor_themes(p)` returns two themes with all syntax keys listed in the table present; names end
  correctly; `palette_for` works for every `BUILTIN_THEMES` entry (generic fallback never raises).
  App-level: `GPython(path)` in `run_test`, `app.theme = "gruvbox"` → every `VimTextArea.theme` equals
  `"gruvbox"` (insert) or `"gruvbox-normal"` (normal) depending on mode; pressing `i` then switching
  theme keeps insert naming; add a cell (`ctrl+n`) after switching → new editor has the new theme.
- `tests/test_config.py`: load missing → `{}`; save then load roundtrip; invalid TOML → `{}`;
  save merges keys.
- Existing tests green; `scripts/bench.py` within 10 % of plan 01 results (paste both tables).

## Report

Files changed, test summary line, bench table, and a request to the peer: add `--theme NAME` in
`__init__.py` passing `theme=` to `GPython`, and document `~/.config/gpython/config.toml` in README.

## Addendum (user ask 2026-10-29): live theme preview

Textual 8.2.8's `ThemeProvider` applies a theme only when a palette entry is **selected**, so `ctrl+t`
gives no live preview. Build our own `ThemePicker(ModalScreen[str | None])` in
`src/gpython/themepicker.py`:

- Layout: a bottom-docked panel (like `Prompt`) with an `Input` filter on top and an `OptionList` below
  listing every theme in `app.available_themes`, our supported palettes first (dark group, then light
  group, each sorted), then the other builtin Textual themes under a dim "other" separator. The current
  theme is pre-highlighted. Each row shows the name and, right-aligned in dim, `light`/`dark`.
- **Live preview:** on `OptionList.OptionHighlighted` set `app.theme = name` immediately (the app's
  `watch_theme` restyles editors/headers), typing in the Input filters the list (fuzzy/substring,
  case-insensitive) and keeps the first match highlighted → previewed. `up`/`down` move while the Input
  keeps focus (forward those keys to the list).
- `enter` → dismiss with the highlighted name (confirm); `escape` → restore the theme that was active
  when the picker opened and dismiss with `None`.
- Persistence happens **only on confirm**: `GPython.watch_theme` must not write the config while a
  preview is in progress. Add `GPython._previewing_theme: bool` set by the picker on open/close; the
  confirm handler calls `config.save({"theme": name})` once. The ex command `:theme name` keeps saving
  directly.
- Bind `ctrl+t` to `action_pick_theme` (replacing Textual's `change_theme`), keep the palette row
  "Change theme" pointing at our picker too (override `get_system_commands` filtering or add our own
  `SystemCommand`), and show the theme name in the status line (`update_status`).
- Tests (`tests/test_themepicker.py`): opening the picker and pressing `down` changes `app.theme`
  before any confirm; `escape` restores the original and writes no config; `enter` on a highlighted theme
  keeps it and writes `theme = ...` to the config (XDG_CONFIG_HOME → tmp_path); typing `gruv` filters to
  gruvbox themes and previews the first.
