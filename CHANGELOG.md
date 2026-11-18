# Changelog

All notable changes to gpython (published on PyPI as `gpynb`). The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/). Each `vX.Y.Z` tag publishes to PyPI and
creates a GitHub release with the wheel and sdist attached and the matching section of
this file as its notes.

## [Unreleased]

### Added
- `:clean` saves a `.py` file without its `# %%` lines. The file becomes an ordinary script:
  code as you wrote it, markdown cells as text in triple quotes. gpython keeps where the cells
  are in `~/.gpython`, as it does for a new file, and the outputs stay.
- `:set nomarkers` does this for each `.py` file that you save (`markers = false` in the
  config). `:set markers` goes back: a file that has `# %%` lines keeps them.

### Changed
- A `.py` notebook that another program changed (an editor, git, an agent) keeps its cells and
  its outputs. Before, one changed character made gpython split the file again and drop all
  outputs. Now gpython compares the file with what it last saved: a cell with a changed line
  stays one cell, new code between cells becomes new cells, and a deleted cell goes. A cell
  with the same code keeps its output. A cell with changed code shows its old output and is
  marked stale, and so are the cells that use its names; run them (`f4`) to get new outputs.
  This is also for files with `# %%` lines and marimo notebooks (their outputs).
- An output that came from older code stays marked stale when you open the file again: a cell
  that you edited and saved without a run, and a cell whose inputs changed after its run.

### Fixed
- A `.py` notebook with `# %%` lines, and a marimo notebook, keep their outputs when you quit.
  Before, only a `.py` file without `# %%` lines did: you had to run the cells again each time
  you opened the file. The outputs, the run numbers and the run times go to `~/.gpython` after
  each run, as for other `.py` files; the file itself is not changed.

## [0.2.1] - 2026-11-18

### Added
- An update command. `gpython --update` gets the newest release from PyPI with the tool that
  installed gpython (uv, pipx or pip), and says which version you have after it.
  `gpython --update dev` gets the newest code from the main branch on GitHub;
  `gpython --update stable` goes back to the newest release. In gpython, `:update`,
  `:update dev` and `:update stable` do the same; start gpython again to use the new version.
  A gpython that runs from a source folder is not changed: the command says to use `git pull`.
- Two windows can show the same notebook. When a window shows `sc.py` and you ask for it again
  in another window (`:e sc.py` after a split, `:sp sc.py`, `:vs sc.py`, `:split v sc.py`), that
  window shows the same notebook, where before the cursor went back to the first window. The
  cells, the kernel and the undo steps are the same in the two windows: text that you type, a
  cell that you add, move or delete, and each output show in both. Each window has its own
  scroll position and cursor. `:q` closes one window and the notebook stays in the other. From
  the file explorer or the file finder, a notebook that is open gets the focus, as before.
- The mouse changes the size of the windows. Hold the line between two windows that are side by
  side, or the status line of the upper of two stacked windows, and move it.
- A scratchpad folder for new notebooks. `gpython --scratchpad DIR` remembers DIR for every
  later launch (`gpy --scratchpad .` is the folder you are in; a missing folder is made). After
  that, a new untitled notebook saved under a plain name, as in `:w save.py` or `ctrl+s`, goes
  to that folder, and the save prompt shows the folder. A name that says where it goes
  (`./save.py`, `../save.py`, `~/save.py`, `/tmp/save.py`) is used as typed. With no scratchpad
  folder the file goes to the folder gpython was started in, as before. `gpython --scratchpad ""`
  forgets the folder. The setting is `scratchpad` in `~/.config/gpython/config.toml`.
- Tab completion in the shell for `gpython` and `gpy`: `gpy --scra` + tab gives `--scratchpad`,
  `gpy --theme ` + tab lists the themes, and a file argument lists folders and notebooks.
  `gpython --install-completions` sets it up for your shell (bash, zsh, fish or PowerShell): it
  writes the script under `~/.config` and adds one line to the shell's start-up file. Run it
  again after an upgrade. `gpython --completions SHELL` prints the script to place by hand.
- The `:` prompt shows suggestions. `:s` lists each command that starts with `s`, with one line
  that says what it does. After a command and a space, the list shows what the command takes:
  the options of `:set` (and the values after `=`, as in `:set cursor=`), the themes for `:theme`,
  folders and notebooks for `:e` `:sp` `:vs` `:tabnew` `:w`, the open notebooks for `:b` and `:bd`,
  and the words of `:img` `:copy` `:uv` `:pip` `:tabmove` `:foldclose`. `tab` / `shift+tab`
  (or `down` / `up`, `ctrl+n` / `ctrl+p`) go through the rows and put the row in the prompt;
  `enter` runs the line. A click on a row does the same as `tab`. After a range (`:'<,'>`) the
  list shows the line commands (`s` `d` `j` `m` `t` `normal` ...).
- `:split v [file]` splits the window into left and right, and `:split h [file]` into top and
  bottom. The direction comes before the file name; `:sp` and `:vs` take it too.

### Fixed
- A file opened in an empty split no longer makes the other window scroll.
- A new cell at the end of a long notebook (more than 100 cells), added immediately after a
  jump with `G`, no longer stops gpython with an error.
- `asyncio.run(...)` works inside a cell, and so does a library that calls it for you. It used to
  fail with "asyncio.run() cannot be called from a running event loop". `loop.run_until_complete`,
  `asyncio.Runner` and `anyio.run` work too. Nothing to import or turn on, and cells that use
  `await` run as fast as before.
- Interrupt stops a cell that is waiting in `await`, such as `await asyncio.sleep(30)`. It used
  to keep running until something else happened in the kernel.

### Changed
- Undo is one timeline for the whole notebook. `u` undoes the newest change, in any cell (it goes
  there): text you typed, or a cell delete, move, add or markdown/code switch. `ctrl+r` does the
  change again, in the editor and in cell mode; `:undo` and `:redo` do the same. A deleted cell
  that comes back keeps its text history. The timeline holds 500 steps (was 50 cell actions).
- `ctrl+b` shows or hides the file explorer in every mode, also in vim normal mode (where it was
  page up). There is no FILES mode badge: in the explorer the badge says NORMAL, as in vim.
  `ctrl+h` and backspace go to the explorer only when it is shown; they do not open it.
- `i` on a selected cell opens its editor in normal mode, the same as `enter`; press `i` again to type.

## [0.2.0] - 2026-11-09

### Changed
- Running all cells is much faster: a run sends its cells to the kernel as one request and
  repaints a few times a second while results stream. 500 cells from launch to the last
  cell: 25.8 s before, 1.5 s now. A run of 1000 cells fed by one setup cell is ordered in
  milliseconds (was 1.3 s).
- The kernel starts a few milliseconds after launch, before the UI, so the first cell is
  ready about 0.2 s sooner.
- A notebook over 100 cells mounts only the cells around the viewport. Keys, cell moves and
  loading stay fast at 500 and 1000 cells; `:%s`, `:g` and other ex ranges still reach every
  cell.
- Text outputs of a cell share one widget: about 30 % less memory per printing cell.
- The bottom bar is a tmux-style status line: the mode badge (NORMAL, INSERT, CELL, …)
  on the left and modules on the right: clock, host, cpu, ram, gpu (README, "Bottom
  bar"). cpu and gpu are btop-style sparklines of the last samples, ram a block meter,
  coloured by load; `status.style = "tmux"` draws one level glyph instead. All on by
  default; `status.cpu = false` drops one, `status.layout` orders them,
  `status.clock_format` sets the clock. The kernel state, theme name and key hints are
  gone from it. `:set nostatusbar` hides it (`status_bar = false` in the config; `:set
  statusbar` brings it back). psutil is a new dependency. The kernel chip in the header
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
- Concurrent cells (`:set concurrent`, off by default): cells of a run that share no state
  run at the same time, on threads in the one kernel. A cell joins a group only if no other
  cell in it writes what it reads or writes; magics, top-level `await`, `input()` and
  `breakpoint()` run alone. `:set concurrency=N` caps a group, `:groups` (and
  `gpy --groups nb.py`) shows the groups and what each cell waits for. After a group,
  a shared name that changed marks its readers stale and the run falls back to one cell at
  a time. `threadpoolctl` is a new dependency.
- Outline panel (`ctrl+o` in cell mode, `:outline`): one node per cell with its imports, variables,
  functions and classes, the kernel's types once the cell ran, the cell's dependency edges,
  and enter to jump. It replaces the `f2` variables table.
- Variables panel (`ctrl+v`) and a `breakpoint()` debugger: loop views with iteration k of
  n, pointer strips over sequences, locals with their history, watches, the stack, time
  travel over recorded steps (`h` / `l`), a timeline (`T`) and post-mortem where a cell
  failed (`r d`, `:debug`). `debug_detail` (`auto`, `full`, `balanced`, `lean`) caps what is
  recorded. `input()` asks at the bottom of the screen.
- Plain `.py` notebooks: new files and scripts are saved without `# %%` lines; where cells
  start and end is kept in `~/.gpython/cells` (`GPYTHON_HOME` moves it). Files that already
  have `# %%` markers keep them. `:copy all` copies the whole notebook.
- `r r` reruns the cell run last; `r c` runs the current cell.
- `gpy --version` (or `-v`) and `:version` (`:ver`) show the installed version, read from the package metadata (the `version` in pyproject.toml).
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
- A removed cell's editor stayed in memory while its completion request waited for the kernel.
- A `breakpoint()` stop or `input()` prompt that reached gpython before the cell's begin
  marker was dropped, and the cell waited forever; the two travel on separate sockets.

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

[Unreleased]: https://github.com/igaurab/gpython/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/igaurab/gpython/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/igaurab/gpython/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/igaurab/gpython/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/igaurab/gpython/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/igaurab/gpython/releases/tag/v0.1.0
