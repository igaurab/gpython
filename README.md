# gpython

A reactive Python notebook in the terminal, inspired by marimo.
Run a cell and every cell that uses its variables re-runs automatically.

```sh
./gpython                 # empty untitled notebook
./gpython script.py       # a plain script is split into cells automatically
./gpython --no-vim nb.py  # plain editing, no modal keys
```

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
| `/`, `:`, `?` | Jump, ex command, help |

Ex commands: `:w [file]`, `:q`, `:q!`, `:wq`, `:run`, `:runabove`, `:runbelow`,
`:stale`, `:restart`, `:clear`, `:set vim|novim|lazy|nolazy|nu|nonu`, `:vars`,
`:md`, `:code`, `:N` (go to cell N), `:help`. `:set novim` and line numbers are
remembered in `~/.config/gpython/config.toml`.

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

## Layout

`graph.py` dependency analysis · `kernel.py` IPython kernel in a subprocess
via jupyter_client · `notebook.py` file format · `vim.py` modal editor and
completion popup · `app.py` + `app.tcss` Textual UI.

```sh
uv run pytest -q
```
