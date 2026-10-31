# Changelog

All notable changes to gpython (published on PyPI as `gpynb`). The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/). Each `vX.Y.Z` tag publishes to PyPI and
creates a GitHub release with the wheel and sdist attached and the matching section of
this file as its notes.

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
