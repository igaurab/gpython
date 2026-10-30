# Plan 04 – Markdown cells and rich outputs

Read `docs/plans/README.md` first. Plans 01–03 are done: `outputs.py`, `themes.py`, `config.py`,
`keymap.py`, `help.py`, CELL mode with `m`/`y` stubs and `:md`/`:code` stubs, `GPython.set_cell_kind` stub.

## The peer's `CellSpec` has landed (commit 21aebc2)

`notebook.py` now provides (read the file; do not edit it):

```python
@dataclass
class CellSpec:
    source: str
    kind: Literal["code", "markdown"] = "code"

def load_cells(path) -> tuple[list[CellSpec], bool]      # (cells, uses_markers); handles # %%, [markdown]/[md], plain scripts, .ipynb
def save(path, cells: list[CellSpec | str], markers: bool = True) -> None   # str = code cell
def dump(cells, markers=True) -> str
def parse(text) -> list[CellSpec]
```

File format: `# %% [markdown]` + lines prefixed `# ` (Jupytext percent format); for marker-less
scripts a module-level triple-quoted string statement is a markdown cell; `.ipynb` is read and written
(nbformat 4, outputs dropped). `CellSpec` is a frozen dataclass.

`GPython.compose` already calls `load_cells()` (plan 01) but ignores `kind`; this plan passes
`Cell(spec.source, kind=spec.kind)` and saves `[CellSpec(c.source, c.kind) for c in cells]`.
`graph.analyze` blanks `%magic`/`!shell`/`name?` lines so `.ipynb` cells with magics do not error.

The user's concrete question that triggered this plan: a cell containing `### Hello` – "how do I render
this block as markdown?" The answer after this plan: press `escape` to CELL mode and `m`, or `:md`.

## Cell model

- `Cell(source, kind="code"|"markdown", mode=...)`; `Cell.kind` attribute; CSS class `-markdown`.
- Compose: header `Static`, `VimTextArea` (language `"python"` for code, `"markdown"` for markdown),
  `MarkdownView` (new, in `src/gpython/markdown.py`: a thin wrapper around `textual.widgets.Markdown`
  with `display` toggling and a placeholder for empty text: `*empty markdown cell – press enter to edit*`),
  `OutputArea`.
- Markdown cells have **two views**: *rendered* (MarkdownView shown, editor hidden) and *editing*
  (editor shown, rendered hidden). Default after load: rendered. Code cells never show MarkdownView.
- Enter editing: `enter`/`i` in CELL mode, double-click on the rendered view, or `shift+enter`… no –
  `shift+enter` on a markdown cell **renders** it and moves on (Jupyter). Leave editing: `escape` from
  NORMAL (→ CELL mode, render), `shift+enter`/`ctrl+enter` (render, stay/advance), focus moving to another
  cell (`on_descendant_blur` with the editor losing focus → render).
- Rendering uses `await markdown_view.update(text)`; debounce not needed (only on leave).
- Header for markdown cells: `¶ c3  MARKDOWN` chip instead of `[n]`; no `defines/uses` info.

## Docstring cells must not echo their text (user ask, screenshot of a leetcode file)

Running a cell that is just a `"""..."""` string printed the whole string's repr as an
`execute_result`. Two layers fix this:

1. `notebook.load_cells()` already returns module-level string statements of plain scripts as
   `kind="markdown"`; once `Cell(spec.source, kind=spec.kind)` is honoured, such a cell is rendered as
   markdown and never sent to the kernel. `split_script` keeps saving it back as a triple-quoted string.
2. For **code** cells whose last top-level statement is a bare string constant (`ast.Expr(ast.Constant(str))`,
   i.e. a docstring-like expression), suppress the `execute_result` message: compute
   `Cell.echo_suppressed` from the AST right before running (in `run_cells`, `try: ast.parse(source)` and
   ignore syntax errors) and have `Cell.add_output` drop `execute_result` while it is set. Streams,
   display_data and errors still show. Test: a cell `"""doc"""` run as code shows no output; a cell
   `x = 1\n"""note"""` shows nothing; a cell `"""doc"""\nx` still shows the value of `x`.

## Graph / execution integration

- `GPython.graph()` builds only from **code** cells: `build({c.cid: c.source for c in self.cells() if c.kind == "code"})`.
- `run_cells` ignores markdown ids in `roots`; `action_run_cell` on a markdown cell = render + (advance
  if requested) and does **not** touch the kernel.
- `set_cell_kind(cell, kind)` (replace plan 03 stub):
  - code → markdown: remember `downstream = graph.descendants([cid]) - {cid}` and `defs = cell.last_defs`;
    switch kind, set editor language to markdown, clear outputs, status `idle`; then `await
    kernel.delete(defs)` and `run_cells(sorted(downstream))` like `action_delete_cell` does, so dependents
    re-run (they will now fail with NameError, which is the correct marimo behaviour).
  - markdown → code: switch language to python, status `idle`, `refresh_info()`.
  - `dirty = True`; keep focus/selection on the same cell.
- `action_run_stale` only considers code cells. `refresh_info` skips markdown cells (`set_info` clears).
- Save: `notebook.save(path, [CellSpec(c.source, c.kind) for c in cells], markers)`.
- `split_script`-loaded (marker-less) files: a docstring cell arrives as `kind="markdown"` from the peer;
  nothing special on our side.

## Keys / commands (wire the plan 03 stubs)

- CELL mode `m` → markdown, `y` → code; `:md`, `:code`; palette commands "Cell → Markdown", "Cell → Code".
- `shift+enter` / `ctrl+enter` / `alt+enter` on markdown cells behave as described above.
- Help screen and README key table updated.

## Rich outputs (in `outputs.py`)

Extend `OutputArea.add` for `execute_result` / `display_data` with a MIME priority list:

```python
MIME_PRIORITY = ("image/png", "text/markdown", "application/json", "text/html", "text/latex", "text/plain")
```

- `image/png` – existing `Image` path.
- `text/markdown` – mount a `textual.widgets.Markdown` (reuse the `MarkdownView` wrapper).
- `application/json` – `Static(rich.json.JSON(json.dumps(data)))` (the payload is already a parsed object).
- `text/html` – if `text/plain` exists use it (pandas DataFrames give a good plain repr); else strip tags
  with `html.parser` into plain text and show it with a dim note `[html rendered as text]`.
- `text/latex` – show as plain text with a dim note.
- `text/plain` – existing.
- Unknown-only payloads keep the existing `[… output not supported yet]` note.
- `clear_output(wait=True)` semantics: if `content.get("wait")`, defer the clear until the next output
  arrives (Jupyter behaviour used by progress displays).

## Tests (`tests/test_markdown.py`, new; `tests/test_outputs.py` extended)

1. Load a markers file containing a `# %% [markdown]` cell (only when `CellSpec` exists; otherwise build
   the app and call `set_cell_kind`): the cell has `kind == "markdown"`, the editor is hidden, the
   `MarkdownView` is shown and contains the heading text.
2. CELL `enter` shows the editor (NORMAL), typing after `i` changes the text, `escape escape` re-renders.
3. `m` on a code cell that defines `x` (after running it, kernel test) deletes `x` and re-runs the dependent
   which now errors with NameError; `y` turns it back into code; `shift+enter` then runs it again successfully.
4. Markdown cells are absent from `app.graph().order`.
5. Save roundtrip with `CellSpec` (skip test if missing): kinds survive `save` → `load`.
6. Outputs: `text/markdown` display_data mounts a Markdown widget; `application/json` renders; `text/html`
   with `text/plain` prefers plain; `clear_output(wait=True)` keeps the old output until the next one.

Bench within 10 % (paste table). Existing tests green.

## Report

Files changed, tests, bench, whether `CellSpec` was available, README updates, requests for the peer
(e.g. `.ipynb` import of markdown cells if not yet done).
