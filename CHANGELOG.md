# Changelog

All notable changes to gpython (published on PyPI as `gpynb`). The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/). Each `vX.Y.Z` tag publishes to PyPI and
creates a GitHub release with the wheel and sdist attached and the matching section of
this file as its notes.

## [Unreleased]

### Changed
- The bottom bar shows only the mode badge (NORMAL, INSERT, CELL, …); the kernel state,
  theme name and key hints are gone from it. `:set nostatusbar` hides it (`status_bar =
  false` in the config; `:set statusbar` brings it back). The kernel chip in the header
  carries the kernel dot (green ready, yellow starting or installing, red failed) and,
  while the kernel is not ready, the state text; `r 6`, `g 6` and `r t` prompts show in
  the header subtitle.
- Cell mode is sky blue (`#38bdf8`) with dark text: the badge, the cell bar and the block cursor.
- User-facing text (notifications, the `:` command list, the help screen, the environment
  picker, the install prompt, the debugger and variables panels) is rewritten in plain,
  short sentences in the style of ASD-STE100 Simplified Technical English.
- `ctrl+w h/j/k/l` chooses, among equally near windows, the one that lies most fully beside
  the current one as a share of its own size, so two windows from one split tie and the
  first wins; a one-row rounding difference no longer decides.
- The editor follows vim where app shortcuts collided; the old actions stay in cell mode
  (README, "What changed for vim"): `r` is vim's replace (`r 6` runs a cell in cell mode,
  `space r 6` in the editor), `J` joins lines, `gJ` joins without spaces, `K` no longer
  moves to the previous cell; in normal and visual mode `ctrl+d ctrl+e ctrl+n ctrl+p ctrl+o
  ctrl+v ctrl+b` are vim keys (scroll, jump list, visual block), in insert mode `ctrl+w
  ctrl+t ctrl+d ctrl+e ctrl+n ctrl+p ctrl+v` are.
- The cursor is a block in every mode, coloured like the mode's badge (yellow normal, green
  insert, magenta visual, red replace); a cell selected in cell mode has a blue bar and a blue
  block cursor on its current line; the cell being edited has a bar in the vim mode's colour, a stale cell a salmon one, the cell the debugger is paused in red like the DEBUG badge (was the theme's
  accent, often orange like the normal-mode cursor); and the
  CELL badge is blue. `:set cursor=bar|underline` brings back the terminal cursor, coloured
  per mode.

### Added
- Vim: `r`, `J`, `U`, `gi gI gp gP g_ gm g0 g$ ga gd gD g; g, g* g# g& g?`, `gq` / `gw`,
  `( )`, `is as`, `[( [{ ]) ]} [[ ]] [] ][ [m ]m`, the jump list (`ctrl+o`, `tab`, `''`),
  scrolling (`ctrl+d ctrl+u ctrl+f ctrl+b ctrl+e ctrl+y zz zt zb z. z<enter> z-`), `ZZ ZQ @: &`,
  `ctrl+g`, `?` backward search (`n` follows its direction), registers `"0 "1-"9 "- ". "+`,
  blockwise visual mode (`ctrl+v`: `I A c d y p r ~ u U O $`), visual `ctrl+a ctrl+x g ctrl+a :`,
  insert-mode `ctrl+w ctrl+u ctrl+t ctrl+d ctrl+r ctrl+a ctrl+e ctrl+y ctrl+n ctrl+p ctrl+v ctrl+j`.
- `:s` with ranges, vim patterns and replacements, flags `g i I n &`, and `:&` / `:&&`. The
  notebook is one file as in vim: `:%s` changes every cell, `$` is the notebook's last line.
- `:Q!`, `:WQ!`, `:Wq`, `:W`, `:X`, `:QA!` (any case of a save / quit command) work like the
  lower-case ones.
- `:g` / `:g!` / `:v` with `d s j > < m t normal` (no command: list the lines), and the line
  commands `:d :j :> :< :m :t :co :normal`.
- `K` shows the kernel's help for the name under the cursor; insert-mode `ctrl+k` types a
  digraph (`:digraphs`); folds are cells: `zc za zM zR`, `:foldclose`, `:foldopen`, `H` in
  cell mode. Interrupting from insert mode moves to `space r i` / other modes' `ctrl+k`.
- `gpy`, a short alias for the `gpython` command.
- Window management: `ctrl+w z` zooms a window to fill its tab (again restores the splits),
  `ctrl+w x` swaps, `ctrl+w r` / `R` rotate, `ctrl+w H J K L` move a window to an edge,
  `ctrl+w M` / `:tabmerge` undoes `ctrl+w T`, `:tabmove N` reorders tabs.
- Window manager (`ctrl+w space`, `:windows`): a floating list of tabs, windows and hidden
  buffers with a map of the selected tab's layout; go, split, move to a tab, zoom, close,
  delete, reorder tabs and filter by name from one popup.

### Fixed
- `ctrl+w + - < >` resized the layout only after the next unrelated change.

## [0.1.2] - 2026-10-30

### Added
- Matplotlib figures and other PNG outputs draw as direct kitty graphics placements
  (kitty, Ghostty, Rex, WezTerm, Konsole); textual_image's Sixel widget where the terminal
  has Sixel, and a card with View and Download buttons where it has no graphics at all.
- `v` in cell mode (and `:img [N]`) views a cell's image full screen: zoom, pan, `s` saves
  it as PNG next to the notebook, `o` opens it in the browser; `:img save [N|all] [file]`
  and `:img open [N|all]`.
- Markdown cells draw `![alt](url-or-path)` pictures in place, and `[text](x.png)` links to
  pictures too: http(s) and data URLs, or paths relative to the notebook, fetched in the
  background and cached under `~/.cache/gpython/images`. Animated GIFs play. The cell's
  pictures are part of `v` / `:img`.
- Vim: `.` repeat, counted inserts, `~` `g~` `gu` `gU`, `R`, `>` `<` `=` with motions and
  text objects, marks, registers, macros, `ctrl+a` / `ctrl+x`, more motions.
- Running a cell that imports a missing module offers to install it (pip or uv, like marimo).
- `t` in cell mode shows each cell's run time in its header (saved as `timing` in the config);
  `r t [n]` times a cell over n runs and prints mean, median, std, variance, p50/p90/p99,
  min and max.
- `ctrl+h` / `ctrl+l` move between the explorer and the notebook.
- `:wq!`, `:x!`, `:w!`, `:wqa!`, `:xa!` and `:exit` are accepted as their unforced forms.
- `GPYTHON_TTY_LOG=file` records every byte sent to the terminal, so a session can be
  replayed with `cat`; `scripts/kitty_probe.py` checks a terminal's graphics support.
- CI runs the tests on every push to main and on pull requests.

### Fixed
- A notebook whose folder does not exist still gets a kernel: it starts in the current folder.
- Only the focused editor paints a cursor.

## [0.1.1] - 2026-10-30

### Fixed
- `r` is the run prefix in vim normal mode too: `r r`, `r C`, `r 6`, `r k` work from the
  editor (a fresh launch starts in the editor, where `r` was unbound). `space r` stays a
  synonym.

## [0.1.0] - 2026-10-30

First release on PyPI as `gpynb`: `uv tool install gpynb` gives the `gpython` command.

### Added
- A terminal notebook built on Textual: cells with a vim-like editor (normal, insert and
  visual modes, operators, text objects, search with `/` `n` `N` `*` `#`), a global cell /
  edit mode, cell mode keys to move, add, delete, yank and paste cells, and `:` commands
  (`:w`, `:q`, `:run`, `:theme`, `:find`, `:ls` / `:b` buffers).
- Jupyter kernel execution with marimo's staleness model: lazy runs bring stale ancestors
  up to date first; `r r` / `r c` / `r a` / `r A` / `r s` run this cell, to here, after,
  all, or the stale ones; `r k` / `r R` / `r i` manage the kernel; `g 6` and `r 6` select
  or run a cell by number.
- Markdown cells rendered with Rich in the theme's colours, big h1–h3 headings where the
  terminal has kitty graphics, marimo display cells (`mo.md`, `mo.Html`) rendered without
  running them, `h` folds a markdown cell.
- Python environment switching (venvs, uv, conda) with an env picker; themes with a
  picker and a "terminal" theme that keeps the terminal's own colours; a fuzzy file finder;
  files open in their own tab with vim-style buffers; yanks also go to the system clipboard.
- Performance work so a keystroke paints in under 10 ms and the first frame is on screen
  within about 0.3 s of launch, with `scripts/bench.py` to keep it that way.

[0.1.2]: https://github.com/igaurab/gpython/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/igaurab/gpython/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/igaurab/gpython/releases/tag/v0.1.0
