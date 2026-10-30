# gpython

A reactive Python notebook in the terminal, inspired by marimo.
Run a cell and every cell that uses its variables re-runs automatically.

```sh
uv run gpython notebook.py
```

| Key | Action |
|---|---|
| `ctrl+r` | Run cell (and everything downstream) |
| `ctrl+n` | New cell below |
| `ctrl+d` | Delete cell (dependents re-run) |
| `alt+↑/↓` or `ctrl+↑/↓` | Previous / next cell |
| `ctrl+s` | Save |
| `ctrl+k` | Interrupt running code |
| `f2` | Toggle variables panel |
| `ctrl+p` | Command palette: run all, restart kernel, save |
| `ctrl+q` | Quit |

Rules (same as marimo):
- A name may be defined in only one cell.
- Names starting with `_` are private to their cell.
- Mutating another cell's object (`x.append(1)`) is not tracked; reassign instead.

Notebooks are plain `.py` files with `# %%` cell markers, so they diff cleanly
and open in VS Code / Jupytext.

Layout: `graph.py` (dependency analysis), `kernel.py` (IPython kernel in a
subprocess via jupyter_client), `notebook.py` (file format), `app.py` (Textual UI).
