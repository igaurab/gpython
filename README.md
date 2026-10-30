# gpython

A Python notebook in the terminal, inspired by marimo: cells know which
variables they define and use, and runs follow that graph.

Running a cell first runs the stale cells it depends on (never run, edited
since, or their inputs changed), then the cell. What happens to the cells
*below* it, the ones that use its variables, is the **run mode** (`f3`, saved):

- **lazy** (the default): they are only marked stale. You run what you want,
  when you want: `r 6` runs cell c6, `f4` runs every stale cell.
- **reactive**: they re-run automatically, like marimo.

<img width="1372" height="1034" alt="image" src="https://github.com/user-attachments/assets/c93ed22f-cf15-4a26-9322-c62c940febf5" />


```sh
./gpython                    # empty untitled notebook
./gpython script.py          # a plain script is split into cells automatically
./gpython a.py b.py          # several files: one tab each (gt / gT switch)
./gpython notebooks/         # a directory: opens the file explorer there
./gpython --no-vim nb.py     # plain editing, no modal keys
./gpython --theme nord nb.py # colour theme for this run (ctrl+t picks one and remembers it)
```

Files: `.py` notebooks (plain scripts, `# %%` cells, Jupytext markdown cells),
marimo notebooks (`@app.cell`) and `.ipynb`; see [Files](#files).

## Keys

Three modes, shown in the top bar: **INSERT** (typing), **NORMAL** (vim keys in
the editor) and **CELL** (Jupyter's command mode: the whole cell is selected and
keys act on cells). `escape` goes INSERT → NORMAL → CELL; `enter` or `i` goes back
into the editor. Clicking a cell's header selects it. `f1` (or `?` in cell mode)
shows every key; the list lives in `src/gpython/keymap.py`.

Everywhere:

| Key | Action |
|---|---|
| `shift+enter` | Run cell (markdown: render it), then go to the cell below (a new cell only on the last one) |
| `ctrl+enter` / `ctrl+shift+enter` / `ctrl+e` | Run cell (markdown: render it), stay |
| `alt+enter` | Run cell and insert a new cell below |
| `f5` / `f4` | Run all / run stale cells |
| `f6` | Python environment: switch venv/interpreter, new uv or python venv, install packages |
| `ctrl+k` | Interrupt running code |
| `ctrl+n` / `ctrl+shift+n` | New cell below / above |
| `ctrl+d` | Delete cell (cells that used its variables are marked stale, or re-run when reactive; `u` in cell mode undoes) |
| `alt+↑/↓`, `ctrl+↑/↓` | Previous / next cell |
| `ctrl+home` / `ctrl+end` | First / last cell |
| `alt+shift+↑/↓` | Move the cell up / down |
| `ctrl+s` / `:w` | Save (asks for a name if untitled) |
| `ctrl+b` | Toggle the file explorer (see [Files, windows and tabs](#files-windows-and-tabs)) |
| `ctrl+w` … | Window commands: `s` split, `v` vsplit, `h j k l` move, `q` close, `o` only, `T` to tab |
| `f1` | Help |
| `f2` | Toggle variables panel (hidden at start) |
| `f3` | Toggle run mode, saved: lazy (default, cells that use the variables are only marked stale) / reactive (they re-run) |
| `ctrl+p` | Command palette: run all, restart kernel, … |
| `ctrl+q` / `:q` | Quit (asks when there are unsaved changes; `:q!` does not) |

Vim normal mode (in the editor):

| Key | Action |
|---|---|
| `escape` | Leave the editor: cell mode |
| `J` / `K` | Next / previous cell (`3J` jumps three) |
| `j` / `k` | Down / up a line; past the edge moves to the next cell |
| `gg` / `G` / `3G` | First / last / third line of this cell |
| `dd` | Delete line, like vim (`3dd` three lines; `u` undoes). To delete the cell: `esc`, then `dd` |
| `gJ` | Join lines (vim's `J`) |
| `/` | Jump: `/3` third cell (as numbered in the headers), `/text` next cell containing text |
| `gt` / `gT` / `2gt` | Next / previous / second tab |
| `:` | Ex command |

Also `h j k l w b e 0 ^ $`, counts, `i a I A o O`, `x X s S dw d$ dj D cc cw C yy p P u ctrl+r`.

Cell mode:

| Key | Action |
|---|---|
| `j` / `k` (`3j`) | Select next / previous cell |
| `gg` / `G` / `3G` | Select first / last / third cell |
| `enter` / `i` | Edit the cell (normal / insert mode); on markdown, show its source |
| `a` / `b` | Insert a cell above / below |
| `dd` | Delete cell (`3dd` three; `u` brings them back) |
| `u` (or `z`) | Undo the last cell action: delete, move, insert, markdown/code switch |
| `yy` / `x` / `p` / `P` | Copy / cut / paste below / paste above |
| `J` / `K` | Move the cell down / up |
| `m` / `y` | Markdown / code cell |
| `h` | Hide / show the cell's code (marimo `hide_code`) |
| `o` / `O` | Collapse output / clear output |
| `l` | Toggle line numbers |
| `gt` / `gT` | Next / previous tab |
| `/`, `:`, `?` | Jump, ex command, help |

Ex commands: `:w [file]`, `:wa`, `:q`, `:q!`, `:wq`, `:qa`, `:wqa`, `:e file`,
`:sp [file]`, `:vs [file]`, `:only`, `:tabnew [file]`, `:tabclose`, `:tabn`,
`:tabp`, `:ls`, `:Ex`, `:run`, `:runabove`, `:runbelow`, `:stale`, `:restart`,
`:clear`, `:set vim|novim|lazy|nolazy|nu|nonu`, `:vars`, `:md`, `:code`,
`:theme [name]`, `:N` (go to cell N), `:help`. `:set novim`, line numbers, the
run mode and the theme are remembered in `~/.config/gpython/config.toml`.

Cell headers read `✓ c3 [4] 0.42s  defines x · uses y`: display position,
execution count (like Jupyter's `In [4]`) and run time.

Editing: Enter indents after `:` or an open bracket and dedents after
`return`/`pass`/…; backspace in leading whitespace removes one indent level;
Tab inserts four spaces.

`shift+enter` and `ctrl+enter` need a terminal that reports them (Ghostty,
Kitty, WezTerm, iTerm2). In Terminal.app use `ctrl+e` and `ctrl+n`.

## Markdown cells

A markdown cell shows rendered text (headings, lists, code, links) instead of an
editor, and it never runs.

- Make one: `ctrl+n` for a new cell, then `escape escape` (cell mode; one `escape` with `--no-vim`) and `m`. Or type
  `:md` in any cell. `y` or `:code` turns it back into code. Turning a code cell
  into markdown removes its variables, so the cells that used them are marked
  stale (reactive mode re-runs them, and they fail).
- Edit: select the cell and press `enter` (or `i`), or double-click it.
- Render: `escape escape` (back to cell mode), `shift+enter` (render and go to
  the next cell), `ctrl+enter` (render and stay), or just move to another cell.
- Files: in `.py` notebooks a markdown cell is `# %% [markdown]` followed by
  lines starting with `# ` (the Jupytext format). In plain scripts without
  markers, a top-level `"""text"""` string is a markdown cell. `.ipynb`
  markdown cells load and save as markdown cells.

A code cell whose last line is a bare string (`"""note"""`) does not print
that string as its result.

## Files, windows and tabs

gpython opens several notebooks at once, the way vim opens several files:
each notebook is a *window*, windows can be split, and tabs group windows.
Every notebook has its own kernel (its own variables, its own `__file__`).

**File explorer** (`ctrl+b`, `:Ex`, `:e` with no name). A tree of the
notebook's folder on the left: folders and `.py`/`.ipynb` files, hidden files
and other extensions behind `.`. Keys inside it:

| Key | Action |
|---|---|
| `j` / `k`, `gg` / `G` | Move |
| `h` / `l` | Collapse (or go to the parent) / expand a folder; `l` on a file opens it |
| `enter` / `o` / `t` | Open the file in its own tab (a folder expands); a blank untitled window is reused |
| `e` | Open the file in the current window (like `:e`) |
| `s` / `v` | Open in a horizontal / vertical split |
| `-` | Go up: the parent folder becomes the root |
| `C` | Make the folder under the cursor the root |
| `%` | New notebook in the folder under the cursor |
| `/`, `n` | Search file names, repeat |
| `.` | Show hidden and non-notebook files |
| `R` | Reload |
| `q`, `escape`, `ctrl+b` | Hide the explorer |

`ctrl+b` toggles the explorer from anywhere. The explorer stays open after a
file is opened; `ctrl+w h` from the leftmost window (or `ctrl+w w`) goes back to
it. A file that is already open is shown (its tab focused) instead of being
opened twice. Clicking a file opens it too.

**Windows** (`ctrl+w` then a key, as in vim; not in insert mode, where
`ctrl+w` deletes a word):

| Key | Action |
|---|---|
| `ctrl+w s` / `:sp [file]` | Split: a new window below (an untitled notebook, or `file`) |
| `ctrl+w v` / `:vs [file]` | Vertical split: a new window to the right |
| `ctrl+w h` `j` `k` `l` | Focus the window to the left / below / above / right |
| `ctrl+w w` / `ctrl+w p` | Next / previous window (the explorer counts as the first) |
| `ctrl+w q` / `:q` | Close this window (asks if unsaved; closing the last one quits) |
| `ctrl+w o` / `:only` | Keep only this window |
| `ctrl+w T` | Move this window to a new tab |
| `ctrl+w =`, `+` `-` `<` `>` | Equalize / resize |

When a tab has several windows each shows a status line with its file name,
dirty mark and kernel state; the active window's line is highlighted.

**Tabs**: `:tabnew [file]`, `gt` / `gT` / `2gt` (normal and cell mode),
`:tabn`, `:tabp`, `:tabclose`, `:tabonly`. The tab line appears above the
notebook once there are two tabs (or a hidden buffer, see below); click a tab
to switch. `./gpython a.py b.py` opens one tab per file.

**Buffers** (as in vim): every opened notebook is a buffer with its own
kernel, numbered in opening order. Closing a window (`:q`, `ctrl+w q`,
`:tabclose`) only hides its notebook: variables and outputs stay, and the tab
line lists hidden buffers (click one to show it).

| Command | Action |
|---|---|
| `:ls` / `:buffers` | List buffers: `%a` current, `a` shown, `h` hidden, `+` unsaved |
| `:b N` / `:b name` | Show that buffer in the current window (a partial, unique name works) |
| `:bn` / `:bp` | Next / previous buffer in the current window |
| `:bd [N]` | Delete the buffer and stop its kernel (asks if unsaved; `:bd!` does not) |
| `:e file` | Open a file in the current window (refuses if unsaved; `:e! file` discards) |

`:w` saves the current notebook, `:wa` saves every buffer, `:q` on the last
window quits (asking about any unsaved buffer), `:qa` quits, `:wqa` saves
everything and quits. Hidden buffers keep their kernel process alive; `:bd`
frees it.

## Themes

`ctrl+t` opens a theme picker with live preview: move through the list and
the whole notebook (editor syntax colours included) changes as you go;
`enter` keeps the theme and writes it to `~/.config/gpython/config.toml`,
`escape` restores the one you had. Every later session starts in the saved
theme. `:theme name` does the same without the picker and is saved too;
`--theme name` applies to that run only; `:theme` alone shows the current one.

The default theme, `terminal` (also `:theme default`), uses your terminal's
own colours and background, so gpython looks like whatever colorscheme the
terminal has. Syntax colours are the terminal's 16 named colours.

Themes: terminal, catppuccin (mocha, macchiato, frappe, latte), gruvbox (dark, light),
material (oceanic, palenight, darker, lighter), nord, dracula, tokyo-night,
solarized (dark, light), monokai, plus Textual's built-in themes.

## Python environments and packages

Each notebook's kernel runs in a Python environment. The header chip shows
which one (`Python 3.12.4 · .venv ▾`); the bottom bar shows the kernel state
with a dot (green ready, yellow starting or installing, red failed) and the
live output of a running install.

At start a notebook uses the environment it last used; otherwise the nearest
`.venv` / `venv` (next to the file, up to the project root) that has
ipykernel; otherwise gpython's own interpreter. A project venv without
ipykernel is pointed out once.

`f6` (or a click on the header chip) opens the environment picker:

- **Switch**: every venv, uv env and conda env next to the notebook and in
  `~/.virtualenvs`, `~/.venvs`, conda's `envs`, plus uv's managed Pythons and
  `python3.x` on `PATH`. Type to filter, or type a path to a venv or a python.
  Picking a venv without ipykernel installs it first. Picking a bare
  interpreter offers to make a venv from it. Switching starts a fresh kernel
  (cells become stale) and is remembered for that notebook.
- **New uv env**: `uv venv <folder> [--python 3.12]`, then ipykernel, then
  switch. Any Python version uv can download works.
- **New venv**: the same with `python -m venv` and pip, no uv needed.
- **Install packages**: `uv pip install` into the kernel's environment (pip
  without uv). New imports work without restarting the kernel.
- **Add to project**: `uv add`, writing to `pyproject.toml` and syncing into
  the kernel's environment.

The same as commands: `:env` (picker) or `:env name|path`, `:venv [folder]
[python]`, `:uv venv [folder] [python]`, `:pip numpy pandas`, `:pip list`,
`:pip uninstall x`, `:uv add httpx`, `:uv <anything>` (runs in the project).
Inside cells, `%uv pip install x` and `%uv add x` target the kernel's own
interpreter, and `%pip` uses uv in environments that have no pip.

## Rules (same as marimo)

- A name may be defined in only one cell.
- Names starting with `_` are private to their cell.
- In-place changes (`xs.append(1)`, `d["k"] = v`) at a cell's top level are
  tracked: readers below run after the mutator, and running the mutator first
  re-runs the cell that created the object so it starts fresh.

## Files

Notebooks are plain `.py` files. `# %%` markers (VS Code / Jupytext) are
honoured; files without markers are split at top-level statements and saved
back without markers, so a script stays a script. `__file__` and the working
directory match the notebook, like running it as a script.

**marimo notebooks** (`import marimo as mo` … `@app.cell` functions) open as
cells and save back byte-identical when nothing changed: cell names, the
`hide_code=True` decorator argument (such cells open with their code hidden;
`h` in cell mode shows it) and `mo.md(...)` cells are kept. In the
kernel a small `marimo` shim provides `mo.md`, `mo.Html`, `mo.hstack`,
`mo.vstack` and `mo.ui` placeholders so such notebooks run. Not kept: other
decorator arguments and comments after a cell's `return`.

**Jupyter notebooks** (`.ipynb`) open with their code and markdown cells,
their stored outputs and execution counts, and save back as `.ipynb` with the
current outputs (notebook metadata preserved).

## Layout

`graph.py` dependency analysis · `kernel.py` IPython kernel in a subprocess
via jupyter_client · `notebook.py` file formats (`# %%`, scripts, marimo,
`.ipynb`) · `vim.py` modal editor and completion popup · `cell.py` one cell ·
`pane.py` one notebook window · `windows.py` tabs and split layout ·
`explorer.py` file tree · `themes.py` / `themepicker.py` / `config.py` ·
`outputs.py` rich outputs · `keymap.py` the key table (help and this README) ·
`app.py` + `app.tcss` the Textual app.

```sh
uv run pytest -q
```
