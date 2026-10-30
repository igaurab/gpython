# gpython

A reactive Python notebook in the terminal, inspired by marimo.
Run a cell and every cell that uses its variables re-runs automatically.

```sh
./gpython                 # empty untitled notebook
./gpython script.py       # a plain script is split into cells automatically
./gpython --no-vim nb.py  # plain editing, no modal keys
```

## Keys

| Key | Action |
|---|---|
| `shift+enter` | Run cell and open a new cell below |
| `ctrl+enter` / `ctrl+e` | Run cell in place (and everything downstream) |
| `ctrl+n` | New cell below (starts in insert mode) |
| `ctrl+d` | Delete cell (dependents re-run) |
| `alt+↑/↓` | Previous / next cell (`j`/`k` past the edge also work) |
| `ctrl+s` / `:w` | Save (asks for a name if untitled) |
| `ctrl+k` | Interrupt running code |
| `f2` | Toggle variables panel |
| `f3` | Toggle lazy mode (dependents only marked stale) |
| `f4` | Run stale cells |
| `tab` | Complete (from the kernel); matches also pop up as you type |
| `ctrl+p` | Command palette: run all, restart kernel, … |
| `:q` / `ctrl+q` | Quit |

The vim mode is global (one mode for all cells) and shown in the status line.
Normal mode: `h j k l w b e 0 ^ $ gg G`, counts, `i a I A o O`,
`x X s S dd dw d$ D cc cw C yy p P J u ctrl+r`. `escape` or `ctrl+[` leaves
insert mode. Ex commands: `:w [file]`, `:q`, `:wq`, `:run`, `:restart`.

Editing: Enter indents after `:` or an open bracket and dedents after
`return`/`pass`/…; backspace in leading whitespace removes one indent level;
Tab inserts four spaces.

`shift+enter` and `ctrl+enter` need a terminal that reports them (Ghostty,
Kitty, WezTerm, iTerm2). In Terminal.app use `ctrl+e` and `ctrl+n`.

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
