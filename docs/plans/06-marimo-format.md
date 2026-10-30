# Plan 06 – Open and save marimo notebooks (owned by the peer session)

User ask (2026-10-29): "make sure I can open marimo-styled or ipynb notebooks with gpython and that
it's fully compatible. For now test on ~/leetcode/notebooks/". That directory holds 150 marimo files
(`__generated_with = "0.25.2"`), all shaped like this:

```python
import marimo

__generated_with = "0.25.2"
app = marimo.App(width="medium")


@app.cell(hide_code=True)
def _():
    import marimo as mo

    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.vstack([
        mo.md('## [1. Two Sum  (Easy)](https://leetcode.com/problems/two-sum/)\n\n*Study plan: ...*'),
        mo.Html('<p>You are given an array ...</p>'),
    ])
    return


@app.cell(hide_code=True)
def _():
    import sys as _sys, pathlib as _pathlib
    _sys.path[:0] = [str(p) for p in _pathlib.Path(__file__).resolve().parents if (p / "lc.py").exists()]
    from lc import run

    return (run,)


@app.class_definition
class Solution:
    def twoSum(self, nums: list[int], target: int) -> list[int]:
        pass


@app.cell
def _():
    TESTS = """
    Input: nums = [2,7,11,15], target = 9
    Output: [0,1]
    """
    return (TESTS,)


@app.cell
def _(TESTS, run):
    run(Solution, TESTS, any_order=True, ns=globals())
    return


if __name__ == "__main__":
    app.run()
```

Counts in the directory: 450 `@app.cell(hide_code=True)`, 344 `@app.cell`, 106 `@app.class_definition`.
`marimo` is **not** installed in gpython's environment (`import marimo` fails), so the kernel needs a shim.

## Part A – `notebook.py`: detect, parse, dump

Detection: the module imports `marimo` and has a top-level `app = marimo.App(...)` assignment (AST).
`load_cells()` returns `(cells, True)` and remembers per path (like `_ipynb_meta`) the **prelude**
(everything before the first `@app.` decorator: `import marimo`, `__generated_with`, `app = marimo.App(...)`
and blank lines) and the **footer** (`if __name__ == "__main__": app.run()`), so `save()` reproduces them
byte for byte.

`CellSpec` gains optional fields (defaults keep every existing caller working):
`name: str | None = None` (the function name when it is not `_`), `hide_code: bool = False`,
`decorator: str = "cell"` (`"cell" | "class_definition" | "function"`).

Parsing (`parse_marimo(text) -> list[CellSpec]`), walking `ast.parse(text).body` in order:

- `FunctionDef` decorated with `app.cell` or `app.cell(...)`: the cell source is the function body with the
  **trailing `return ...` statement removed** and dedented by 4 spaces (use `ast.get_source_segment`/
  line numbers, keep comments and blank lines inside the body, strip trailing blank lines). Read
  `hide_code=True` from the decorator keywords. If the function is named other than `_`, keep `name`.
  Bodies that are just `pass` become an empty cell.
- `ClassDef`/`FunctionDef` decorated with `app.class_definition` / `app.function`: the source is the whole
  class/function **without** the decorator line (`decorator="class_definition"`/`"function"`).
- Anything else at top level after the prelude (rare) becomes a code cell.
- Markdown convenience: a cell whose body is exactly one expression `mo.md("...")` / `mo.md('''...''')`
  with a constant string becomes `kind="markdown"` with that string (dedented via `textwrap.dedent`);
  on save it is written back as `mo.md(r"""...""")` inside an `@app.cell(hide_code=True)` function. Cells
  that call `mo.vstack([...])` or `mo.Html` stay code cells (the kernel shim renders them, Part B).

Dumping (`dump_marimo(cells, path) -> str`), must round-trip **every file in ~/leetcode/notebooks byte for
byte** when nothing was edited:

- prelude as loaded (default for new files: `import marimo\n\n__generated_with = "0.25.2"\napp = marimo.App(width="medium")\n`),
  then for each cell two blank lines and:
  - `decorator == "cell"`: `@app.cell` or `@app.cell(hide_code=True)`, then `def <name or _>(<refs>):`, the body
    indented 4 spaces (blank lines stay empty, no trailing spaces), a blank line, then `    return (<defs>,)`
    for one def, `    return <d1>, <d2>` for several, `    return` for none. `refs` = names the cell **uses** that
    some other cell **defines** (use `graph.analyze` per cell; sort as marimo does: order of first appearance in
    the body is what marimo emits – check against the files; if the originals use another order, match it; the
    byte-for-byte test will tell). `defs` = the cell's public defs (`graph.analyze`), sorted alphabetically
    unless the files show otherwise. Empty cells (`""`) are written as `def _():\n    return` – check how marimo
    writes them in the corpus if any exist.
  - `decorator == "class_definition"` / `"function"`: `@app.class_definition` / `@app.function` then the source.
  - markdown cells: `@app.cell(hide_code=True)\ndef _(mo):\n    mo.md(r"""<text>""")\n    return` (parameter `mo`
    only if a cell defines `mo`; otherwise `def _():`).
- footer as loaded (default `\n\nif __name__ == "__main__":\n    app.run()\n`).

`save(path, cells, markers)` uses the marimo dumper when the file was loaded as marimo (remembered per path)
or when a new file's first cell is `import marimo`.

Tests (`tests/test_marimo.py`): parse the example above → 7 cells with the expected sources/flags;
`dump(parse(text)) == text` for the example; a **corpus test** that, when `~/leetcode/notebooks` exists,
loads every `*.py` there and asserts `dump_marimo(cells) == original` for each (skip the test if the directory
is missing so CI elsewhere passes); edited cell → refs/defs recomputed correctly (add a cell using `TESTS`
and see it appear as a parameter).

## Part B – `kernel.py`: a `marimo` shim so these notebooks run

Add to `_HELPERS` (only when `import marimo` fails in the kernel): register a module `marimo` in
`sys.modules` providing:

- `md(text)` → object with `_repr_markdown_()` returning the text (so gpython shows it as markdown).
- `Html(text)` → object with `_repr_html_()` returning the html and `_repr_markdown_()` returning the html
  converted to markdown-ish text (strip tags; `<code>` → backticks, `<strong>` → `**`, `<em>` → `*`,
  `<li>` → `- `, `<p>`/`<br>` → blank line). gpython's `outputs.py` already prefers `text/markdown` over
  `text/html`, so the leetcode problem statements will render readably.
- `vstack(items, **kw)` / `hstack(items, **kw)` → object whose `_repr_markdown_()` joins the children's
  markdown (`\n\n` for vstack, `  ` for hstack), each child via its `_repr_markdown_` if present else `str()`.
- `App(**kw)` with `.cell`, `.class_definition`, `.function` decorators that return the function/class
  unchanged and `.run()` as a no-op, so `app = marimo.App()` lines in the prelude do no harm if a user runs them.
- `ui` namespace stub raising a clear `NotImplementedError("marimo.ui widgets are not supported in gpython")`.

Test (`tests/test_kernel.py`): execute `import marimo as mo; mo.vstack([mo.md('# T'), mo.Html('<p>x <code>y</code></p>')])`
→ an `execute_result` whose data has `text/markdown` containing `# T` and `` `y` ``.

## Part C – `graph.py`

Nothing new: `analyze()` already gives defs/refs. Confirm that `_sys`/`_pathlib` (underscore names) are
private, so the `sys.path` cell exports only `run` – matches marimo's `return (run,)`.

## Part D – UI (coordinating session, app.py; not the peer)

- `hide_code=True` cells: show the code normally (gpython has no hidden-code mode yet) but keep the flag so it
  is saved back; later plan may collapse them.
- `Cell(spec.source, kind=spec.kind)`; on save pass full `CellSpec(source, kind, name, hide_code, decorator)`
  copied from the cell (store the loaded spec fields on the `Cell`).

## Acceptance

`gpython ~/leetcode/notebooks/0001-two-sum.py` opens 7 cells; running all shows the problem statement as
rendered markdown, the `Solution` class cell runs, the `TESTS` cell has no echo, the `run(...)` cell prints
results; `:w` leaves the file byte-identical when nothing was edited; editing the `Solution` body and saving
produces a file marimo can still open (`def _` cells, decorators, prelude and footer intact).
