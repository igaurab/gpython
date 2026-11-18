# gpython

A Python notebook in the terminal, inspired by marimo: cells know which
variables they define and use, and runs follow that graph.

Running a cell first runs the stale cells it depends on, then the cell. The cells
below it that use its variables are only marked stale (**lazy**, the default) or
re-run automatically (**reactive**, like marimo); `f3` switches. See
[Running cells](https://github.com/igaurab/gpython/wiki/Running-cells).

<img width="1372" height="1034" alt="image" src="https://github.com/user-attachments/assets/c93ed22f-cf15-4a26-9322-c62c940febf5" />

## Performance

![gpy vs JupyterLab vs marimo: warm-up, run-all scaling, memory](docs/benchmarks/warmup_scaling.png)

On a MacBook (Apple M3 Max), mean of 3 runs:
- **On screen:** gpy in 0.3 s, against 1.1 s for JupyterLab's server and 0.4 s for marimo's.
- **Run all 500 cells:** gpy in 1.2 s, against 2.6 s for JupyterLab and 2.9 s for marimo.
- **Memory:** gpy uses the least at every notebook size.

JupyterLab and marimo are timed without their browser, so their real numbers are higher. `./scripts/eval.sh` reruns the whole comparison and redraws the chart. Its files and conditions are in [`docs/benchmarks/`](docs/benchmarks/), and how gpython got here is in [`docs/optimizations.md`](docs/optimizations.md).

## Install

The package on PyPI is called `gpynb`; the command it installs is `gpython`,
with `gpy` as a short alias.
Needs Python 3.13 or newer.

```sh
uv tool install gpynb        # installs the `gpython` command globally
uv tool upgrade gpynb        # later, to update
uvx --from gpynb gpython     # or run it once without installing anything
pipx install gpynb           # if you use pipx instead of uv
```

From a checkout, `./gpython` runs the working copy (it creates the venv on
first use); `uv run gpython` does the same. To use the working copy from any
directory without root, `uv tool install --editable .` puts `gpython` and `gpy`
in `~/.local/bin` (`uv tool update-shell` adds it to `PATH`); `gpy` alone opens
an untitled scratch notebook.

## Usage

```sh
gpython                    # empty untitled notebook
gpython script.py          # a plain script is split into cells automatically
gpython a.py b.py          # several files: one tab each (gt / gT switch)
gpython notebooks/         # a directory: opens the file explorer there
gpython --no-vim nb.py     # plain editing, no modal keys
gpython --theme nord nb.py # colour theme for this run (ctrl+t picks one and remembers it)
```

Files: `.py` notebooks (plain scripts, `# %%` cells, Jupytext markdown cells),
marimo notebooks (`@app.cell`) and `.ipynb`; see [File formats](https://github.com/igaurab/gpython/wiki/File-formats).

## First keys

Three modes, shown in the top bar: **INSERT** (typing), **NORMAL** (vim keys in the
editor) and **CELL** (the whole cell is selected and keys act on cells). `escape` goes
INSERT → NORMAL → CELL; `enter` or `i` goes back into the editor.

| Key | Action |
|---|---|
| `shift+enter` | Run the cell and go to the next one |
| `ctrl+enter` | Run the cell, stay |
| `f5` / `f4` | Run all / run the stale cells |
| `ctrl+k` | Interrupt |
| `a` / `b` / `dd` (cell mode) | New cell above / below, delete the cell |
| `ctrl+b` | File explorer |
| `ctrl+s` / `:w` | Save |
| `ctrl+q` / `:q` | Quit |
| `f1` | Every key |

## Documentation

The full documentation is in the [wiki](https://github.com/igaurab/gpython/wiki):

- [Running cells](https://github.com/igaurab/gpython/wiki/Running-cells): run modes, the `r …` run keys, concurrent cells, the rules
- [Keys](https://github.com/igaurab/gpython/wiki/Keys) and [Vim](https://github.com/igaurab/gpython/wiki/Vim): every key and ex command
- [Variables and the debugger](https://github.com/igaurab/gpython/wiki/Variables-and-the-debugger)
- [Markdown cells](https://github.com/igaurab/gpython/wiki/Markdown-cells)
- [Files, windows and tabs](https://github.com/igaurab/gpython/wiki/Files-windows-and-tabs): explorer, outline, splits, buffers
- [Python environments](https://github.com/igaurab/gpython/wiki/Python-environments): venvs, uv, installing packages
- [Images](https://github.com/igaurab/gpython/wiki/Images): figures in the terminal
- [Themes and status bar](https://github.com/igaurab/gpython/wiki/Themes-and-status-bar)
- [File formats](https://github.com/igaurab/gpython/wiki/File-formats): scripts, `# %%`, marimo, `.ipynb`
- [Development](https://github.com/igaurab/gpython/wiki/Development): source layout, tests, releases
