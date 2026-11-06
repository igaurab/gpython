# 11 – Full vim in the editor (done 2026-11-05)

## Rule
- Inside an editor (normal, visual, insert, replace), vim's meaning wins.
- An app shortcut that collides moves to cell mode. Cell mode keeps every old key.
- How: `GPython.check_action` returns False for the actions in `_VIM_OWNS[ui_mode]`, so the
  key reaches `VimTextArea`. The set is checked before `ui_mode` is computed (keystroke cost).

## Moved keys
| key | editor now | old action, now |
|---|---|---|
| r | replace char | cell mode `r …`; editor `space r …` |
| J / K | join / nothing | cell mode J/K move cells; j/k past edge, ctrl+↑↓ move between cells |
| gJ | join, no space | — |
| ctrl+d | ½ page down; insert dedent | cell mode, `dd`, `:delete` |
| ctrl+e | scroll 1 line; insert copy-below | `ctrl+enter`, cell mode (own binding `run_cell_ctrl_e`) |
| ctrl+n / ctrl+p | j / k; insert complete | cell mode new cell / palette |
| ctrl+o | jump back; insert one-shot | cell mode outline, `:outline` |
| ctrl+v | visual block; insert literal | cell mode vars, `:vars` |
| ctrl+b | page up (normal/visual only) | cell mode / insert explorer, `:Ex` |
| ctrl+w (insert) | delete word | other modes window prefix |
| ctrl+t (insert) | indent | other modes theme |
| ctrl+k (insert) | digraph (digraphs.py, lazy import) | other modes ctrl+k, `space r i`, `:interrupt` |
| K | kernel help (inspect_request, TextView) | — |
| :d (editor) | delete lines | cell mode `:d` deletes the cell |

## Cursor
- `mode_themes()`: 4 editor themes per colorscheme, same colours, cursor = mode badge colour
  (`MODE_CURSOR_COLORS`: normal yellow, insert green, visual bright_magenta, replace red).
  Names: insert/normal keep the colorscheme's names; visual/replace are `<insert name>-visual|replace`.
- Default `insert_cursor` is now `block` (painted). bar/underline: terminal cursor, OSC 12 colour, OSC 112 reset.
- Cell mode: `Cell.paint_bar` uses `CELL_MODE_BAR` (= `vim.CELL_MODE_COLOR` #3b82f6, the same value as the CELL badge and the cell-mode cursor, so terminals' ANSI palettes cannot turn it purple) when the cell itself is focused; CELL badge blue;
  editing bar = `MODE_CURSOR_COLORS[mode]` (fixed hex per mode, shared by badge, cursor and bar; repainted on ModeChanged); stale bar `STALE_BAR` #e8846b; the header starts one column in, aligned with the code. `VimTextArea.show_cell_cursor` paints a blue block there by swapping `_theme` only (a theme switch restyles the widget on every cell move).

## Ex ranges = vim's file
- `GPython._ex_segments`: % every cell; $ notebook's last line; . cursor line; N = gutter line of current cell;
  no range = cursor line (editor) / whole cell (cell mode). Used by :s, :g (default %), :d :j :> :< :m :t :normal.
- Undo is per cell (TextArea history); a :%s needs one u per changed cell.

## Folds
- Line folds inside a TextArea are not feasible (no hidden-line support); folds = cells' hide_code / markdown fold.

## Not done
- `gn`, `d/pattern`, `q:`, `gR`, `:N` still jumps to cell N (not line N), notebook-wide undo, line folds.

## Next: standalone package
vim.py touches gpython in: `focus()` (`app.cell_mode`), `on_mount` (`app.editor_theme_pair`),
`_kernel()` + `_complete()` (completion), `self.app._driver` (cursor writes), the cell-level
messages (EdgeReached, RunByNumber, FindFiles, TabSwitch, ExRun). Split: core `VimTextArea`
(no app attributes; completion via an overridable `async complete(code, offset)`), gpython
subclass adds the cell messages.
