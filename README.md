# gpython

A reactive Python notebook in the terminal, inspired by marimo.
Run a cell and every cell that uses its variables re-runs automatically.

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
| `ctrl+k` | Interrupt running code |
| `ctrl+n` / `ctrl+shift+n` | New cell below / above |
| `ctrl+d` | Delete cell (dependents re-run; `u` in cell mode undoes) |
| `alt+↑/↓`, `ctrl+↑/↓` | Previous / next cell |
| `ctrl+home` / `ctrl+end` | First / last cell |
| `alt+shift+↑/↓` | Move the cell up / down |
| `ctrl+s` / `:w` | Save (asks for a name if untitled) |
| `ctrl+b` | Toggle the file explorer (see [Files, windows and tabs](#files-windows-and-tabs)) |
| `ctrl+w` … | Window commands: `s` split, `v` vsplit, `h j k l` move, `q` close, `o` only, `T` to tab |
| `f1` | Help |
| `f2` | Toggle variables panel (hidden at start) |
| `f3` | Toggle lazy mode (dependents only marked stale) |
| `ctrl+p` | Command palette: run all, restart kernel, … |
| `ctrl+q` / `:q` | Quit (asks when there are unsaved changes; `:q!` does not) |

Vim normal mode (in the editor):

| Key | Action |
|---|---|
| `escape` | Leave the editor: cell mode |
| `J` / `K` | Next / previous cell (`3J` jumps three) |
| `j` / `k` | Down / up a line; past the edge moves to the next cell |
| `gg` / `G` / `3G` | First / last / third line of this cell |
| `dd` | Delete this cell (`3dd` three cells; `0D` clears just the line) |
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
| `dd` | Delete cell |
| `u` (or `z`) | Undo the last cell action: delete, move, insert, markdown/code switch |
| `yy` / `x` / `p` / `P` | Copy / cut / paste below / paste above |
| `J` / `K` | Move the cell down / up |
| `m` / `y` | Markdown / code cell |
| `o` / `O` | Collapse output / clear output |
| `l` | Toggle line numbers |
| `gt` / `gT` | Next / previous tab |
| `/`, `:`, `?` | Jump, ex command, help |

Ex commands: `:w [file]`, `:wa`, `:q`, `:q!`, `:wq`, `:qa`, `:wqa`, `:e file`,
`:sp [file]`, `:vs [file]`, `:only`, `:tabnew [file]`, `:tabclose`, `:tabn`,
`:tabp`, `:ls`, `:Ex`, `:run`, `:runabove`, `:runbelow`, `:stale`, `:restart`,
`:clear`, `:set vim|novim|lazy|nolazy|nu|nonu`, `:vars`, `:md`, `:code`,
`:theme [name]`, `:N` (go to cell N), `:help`. `:set novim`, line numbers and
the theme are remembered in `~/.config/gpython/config.toml`.

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
  into markdown removes its variables, so the cells that used them re-run (and fail).
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
| `enter` / `o` | Open the file in the current window (a folder expands) |
| `s` / `v` | Open in a horizontal / vertical split |
| `t` | Open in a new tab |
| `-` | Go up: the parent folder becomes the root |
| `C` | Make the folder under the cursor the root |
| `%` | New notebook in the folder under the cursor |
| `/`, `n` | Search file names, repeat |
| `.` | Show hidden and non-notebook files |
| `R` | Reload |
| `q`, `escape`, `ctrl+b` | Hide the explorer |

`ctrl+b` toggles the explorer from anywhere. The explorer stays open after a
file is opened; `ctrl+w h` from the leftmost window (or `ctrl+w w`) goes back to
it. A file that is already open is shown instead of being opened twice.
Clicking a file opens it too.

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
`:tabn`, `:tabp`, `:tabclose`, `:tabonly`, `:ls` lists everything that is
open. The tab line appears above the notebook once there are two tabs; click
a tab to switch. `./gpython a.py b.py` opens one tab per file.

`:e file` opens a file in the current window (it refuses if the notebook
there has unsaved changes; `:e! file` discards them). `:w` saves the current
notebook, `:wa` saves every notebook, `:qa` quits (asks if anything is
unsaved), `:wqa` saves everything and quits.

## Themes

`ctrl+t` opens a theme picker with live preview: move through the list and
the whole notebook (editor syntax colours included) changes as you go;
`enter` keeps the theme and writes it to `~/.config/gpython/config.toml`,
`escape` restores the one you had. `:theme name` and `--theme name` do the
same without the picker; `:theme` alone shows the current one.

Themes: catppuccin (mocha, macchiato, frappe, latte), gruvbox (dark, light),
material (oceanic, palenight, darker, lighter), nord, dracula, tokyo-night,
solarized (dark, light), monokai, plus Textual's built-in themes.

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
`hide_code=True` decorator argument and `mo.md(...)` cells are kept. In the
kernel a small `marimo` shim provides `mo.md`, `mo.Html`, `mo.hstack`,
`mo.vstack` and `mo.ui` placeholders so such notebooks run. Not kept: other
decorator arguments and comments after a cell's `return`.

**Jupyter notebooks** (`.ipynb`) open with their code and markdown cells and
save back as `.ipynb` (cell ids and metadata preserved, outputs are not
stored).

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
