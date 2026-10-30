# Plan 05 – File formats and CLI (owned by the peer session)

This plan is a record of what the **peer session** (owner of `graph.py`, `notebook.py`, `kernel.py`,
`__init__.py`) agreed to or was asked to deliver. Our UI plans code against these APIs. Do not
implement this plan from our side.

1. **`CellSpec`** in `notebook.py` (agreed): `CellSpec(source: str, kind: "code" | "markdown" = "code")`;
   `load(path) -> (list[CellSpec], uses_markers)`; `save(path, list[CellSpec], markers=True)`.
   Format: `# %% [markdown]` with `# `-prefixed lines (Jupytext percent); marker-less scripts store
   markdown cells as module-level triple-quoted strings. Code cells unchanged (`# %%`).
2. **`.ipynb` import/export** (requested): `load()` of a `.ipynb` reads `cells[*].cell_type in
   {code, markdown}` and joins `source`; outputs are dropped; `save()` to a `.ipynb` path writes nbformat 4
   JSON with empty outputs and `execution_count: null`, preserving the notebook `metadata` read at load.
   Lets users open their existing Jupyter notebooks (`gpython analysis.ipynb`).
3. **`--theme NAME`** flag (requested) in `__init__.py` → `GPython(path, vim=vim, theme=name)`; the UI
   side stores the choice in `~/.config/gpython/config.toml` (plan 02, `config.py`).
4. **Kernel**: nothing requested yet. Possible later: `kernel.execute` passing `silent=False,
   allow_stdin=False` (already default), and `inspect()` for a future hover/`K` docs key.
5. **Graph**: markdown cells are simply not passed to `build()`.

The peer announces each landing by message; the coordinating session then updates `app.py`
(one-line change in `GPython.compose` for item 1).
