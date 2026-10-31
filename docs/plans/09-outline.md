# 09 – Outline panel: cells, their definitions and dependency edges (tagbar for the notebook)

`ctrl+o` (`:outline`, `:tags`, `:vars`) opens a panel on the right, the mirror of the file explorer:
one node per cell, expandable to the names it defines, with the dependency edges of the current
cell in a footer. It replaces the flat f2 variables table (f2 is retired); the live types and values move into
the tree once a cell has run.

## Behaviour

- Node per cell: `c2  defines np, xs` (the header's info text; a markdown cell shows its first
  heading). Children, from the AST (`outline.symbols`): imports, variables (also tuple targets
  and annotated ones), functions with their signature (`scale(x: float) -> float`, `async` marked),
  classes with their bases. Private names are listed too. After a run the variables carry the
  kernel's type and short repr (`xs  list  [1, 2, 3]`), the data `refresh_vars` already fetches.
- Follows the cursor: the tree's cursor moves to the current cell's node, or to the function or
  class whose lines hold the editor's row (`VimTextArea.SelectionChanged`, idempotent per row),
  without taking the focus. Textual draws the blurred cursor, so no extra marker is needed; the
  first placement waits for the tree's first render, which assigns node lines. The footer shows
  `← uses c1, c2   → used by c4` from `graph.parents/children`.
- `enter` on a symbol selects the cell's editor at that line (normal mode); on a cell node it
  selects the cell (cell mode). `j k` move, `h l` collapse / expand, `q` hides the panel and
  returns to the notebook, `escape` returns without hiding. `ctrl+w l` (and `ctrl+l`) from the
  rightmost window focuses the panel, `ctrl+w h` from the panel goes back.
- Rebuilt in `refresh_info` (runs, cell add / delete / move / kind change) and, while visible,
  0.4 s after the last edit. Values cleared on kernel restart or env switch.
- Mounted on first toggle (a hidden panel would cost widgets at start-up); hidden panels cost
  nothing per keystroke or cell move. The focus border sits on the Tree (`Tree:focus`), not on
  the panel via `:focus-within`, which made Textual restyle the subtree on every focus change.

## Files

- `outline.py` (new): `Symbol`, `symbols(source, kind)`, `Outline(Vertical)` with `tree`,
  `edges`, `rebuild`, `follow`, `set_values`, messages `Jump(cid, line)` and `Closed(hide)`.
- `pane.py`: `_outline` replaces `_vars`; `toggle_outline`, `outline_visible`, `focus_outline`,
  `_values`, debounce in `_edited`. `app.py`: action, ex names, window focus. `app.tcss`: `#outline`.
- `keymap.py`: the `ctrl+o` entry, an `outline` mode; README; tests `tests/test_outline.py`, the three
  tests that used `#vars`.

## Unresolved

- Live values for names defined inside functions or classes: not shown (only top-level names).
- Very large notebooks: the tree is rebuilt whole; fine to a few hundred cells, a virtual tree
  is not planned.
