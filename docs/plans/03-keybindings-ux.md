# Plan 03 – Jupyter/marimo keybindings and cell UX

Read `docs/plans/README.md` first. Plans 01 and 02 are done (outputs.py, themes.py, config.py,
`-focused` cell class, `app.loaded`). Important existing facts: the vim mode is **global**
(`VimTextArea.mode` class attribute, re-applied on focus); the bottom `Static#status` line is updated by
`GPython.update_status()`; ex commands live in the `GPython.ex_commands()` table that also feeds the
command palette – add rows there, never a separate `match`.

## Goal

The notebook should feel like Jupyter (command mode + edit mode, run keys) with vim inside the
editor. Three modes, always visible in the header/status bar:

- **INSERT** – typing in a cell (vim insert mode).
- **NORMAL** – vim normal mode inside the cell editor (existing).
- **CELL** – Jupyter-style command mode: the *cell* is selected, keys act on cells.
  Entered with `escape` from NORMAL (or by clicking a cell header). `enter`/`i` go back to the editor.

## Key map (final; put this table in `README.md` too)

Run keys (work in every mode; `priority=True` on the App):

| Key | Action | Notes |
|---|---|---|
| `shift+enter` | **Jupyter semantics (user's latest ask):** run the cell, then focus the cell below (cursor at (0,0), scroll visible); only when the current cell is the **last** one create a new cell below and enter INSERT mode | replaces the "always create" behaviour of `action_run_and_add`; keep that method name as the implementation |
| `ctrl+enter`, `ctrl+shift+enter`, `ctrl+e` | run cell, stay | existing + the user's explicit ask for `ctrl+shift+enter`; `ctrl+e` is the fallback for terminals without the kitty protocol |
| `alt+enter` | run cell and **insert** a new cell below (INSERT mode) | Jupyter's alt+enter |
| `ctrl+n` | insert new cell below (no run) | unchanged, explicit insert |
| `f5` | run all | also palette / `:run` |
| `f4` | run stale | existing |
| `ctrl+k` | interrupt | existing |

Editing keys (every mode): `ctrl+n` new cell below (INSERT), `ctrl+shift+n` new cell above, `ctrl+d`
delete cell (with undo), `ctrl+s` save, `alt+up`/`alt+down` and `ctrl+up`/`ctrl+down` move focus,
`alt+shift+up`/`alt+shift+down` **move the cell** up/down, `ctrl+t` theme, `f1` help, `f2` variables,
`f3` lazy toggle, `ctrl+p` palette, `ctrl+q` quit (asks if unsaved).

CELL mode keys (handled by `Cell` when it has focus itself, i.e. `Cell.can_focus = True` and the
editor is **not** focused):

| Key | Action |
|---|---|
| `j`/`down`, `k`/`up` | select next/previous cell (scroll into view, `animate=False`) |
| `g g`, `G` | first / last cell |
| `enter`, `i` | edit: focus editor in NORMAL mode (`i` → INSERT directly) |
| `a` / `b` | insert cell above / below and edit it (INSERT) |
| `d d` | delete cell (same as `ctrl+d`) |
| `z` | undo last cell deletion (restores at its old index with source, kind, outputs) |
| `y y` / `x` / `p` / `P` | copy / cut / paste below / paste above cell (app-level cell clipboard, separate from the vim register) |
| `shift+j` / `shift+k` | move cell down / up |
| `m` / `y` | change cell to markdown / code — **plan 04 implements the switch; here only call `self.app.set_cell_kind(cell, "markdown"/"code")` which this plan stubs to `notify("markdown cells arrive in plan 04")`** |
| `o` | toggle output collapse (collapsed shows 3 lines + "… N more lines" in dim) |
| `shift+o` | clear this cell's output |
| `l` | toggle line numbers for all editors (persist in config as `line_numbers`) |
| `/` | focus the variables panel filter? **no** – out of scope; ignore |
| `?` | help screen |
| `:` | ex prompt (same as in NORMAL) |

Implement counts for `j`/`k` (`3j`) and the two-key sequences (`gg`, `dd`, `yy`) with a `pending` attribute
like `vim.py` does. Unknown keys do nothing (consume printable keys so they never leak into the editor).

Escape chain: INSERT —esc→ NORMAL —esc→ CELL —esc→ (stay in CELL, clear pending). This means
`VimTextArea` must, in normal mode on `escape` with nothing pending, post a new message
`VimTextArea.LeaveEditor` which `Cell` handles by `self.focus()` (cell gets focus, editor blurs).
When `--no-vim` (`VimTextArea.enabled is False`), `escape` in the editor goes straight to CELL mode.

Visuals:
- `Cell.-selected` (CELL mode) border `thick $accent` plus a `reverse` header background;
  `-focused` (editor focused) keeps the current look. Only one cell is ever `-selected`.
- Header line: `● c3 [4] 0.42s  defines x · uses y` → `[n]` is the **execution counter**
  (`GPython.exec_counter` incremented per cell run, like Jupyter `In [n]`), and the duration appears when
  a run took ≥ 0.1 s (`time.perf_counter()` around `kernel.execute`). While running show `●  running…`
  with elapsed seconds updated once per second via `set_interval(1, …)` (stop it when done).
- Mode chip: plan 01 added `Static#mode` inside the top `Header`, right after the header icon (user ask),
  showing `NORMAL` (bold yellow) / `INSERT` (bold green). Extend it with `CELL` (bold $accent / cyan) when
  the focused widget is a `Cell` itself; expose `GPython.ui_mode` property returning `"insert"|"normal"|"cell"`.
  `update_status()` updates both the chip and the bottom `#status` line (file name + `•` when dirty,
  `reactive|lazy`, kernel state, theme name). Cell headers do **not** show the mode.

## Direct user requests (relayed by the peer; must be in this plan)

0. **Cell labels are display positions, not creation ids.** `Cell.cid` (`c1, c2, …` from a global counter)
   stays the internal, unique, stable graph key, but the header must show the **1-based display index**
   (`[3]` style, like Jupyter's `In [3]` slot, or `c3` – pick `c3` so `/3` reads naturally). Add
   `Cell.label: str` and `Cell.set_label(n)`; `GPython.relabel()` renumbers every cell after mount, delete,
   move, undo-restore and progressive mount (one pass over `cells()`). Today a cell inserted with `ctrl+n`
   between c1 and c2 shows as `c5` – that is the bug the user screenshotted.

1. **Variables panel hidden by default.** `#vars` starts with `display = False` (set in `compose` or CSS);
   `f2` and `:vars` toggle it. Status line unaffected.
2. **`/` jump prompt** in NORMAL and CELL mode: `/` opens `Prompt("/")`. Input `N` (digits): focus the
   **N-th cell by display position** (1-based, i.e. the label shown in the header; never the internal
   cid). Non-numeric text: jump to the next cell (after the current, wrapping) whose source
   contains the text (case-insensitive); notify "not found" otherwise. Scroll it visible
   (`animate=False`), keep the current UI mode (NORMAL → focus its editor; CELL → select it). The `:N`
   ex command does the same as `/N`. `VimTextArea` must post a new `SearchRequested` message on `/` in
   normal mode (like `:` posts `ExCommand`).
3. **`J` / `K` in vim NORMAL mode jump cells (user ask).** In `VimTextArea._normal`: `J` posts
   `EdgeReached(self, +1)` and `K` posts `EdgeReached(self, -1)` (counts repeat it, `3J`); the app's
   `_edge` handler focuses the neighbour, puts the cursor on row 0 / last row and scrolls it visible
   (`animate=False`). Lowercase `j`/`k` stay per-line and still hop at the edge. vim's join-lines moves
   from `J` to `gJ` (pending `g` + `J`). Update `tests/test_vim.py::test_join` to press `g`, `J`, add a test
   that `J`/`K` post `EdgeReached` with the right step, and update the README key table.
4. **`gg` / `G` stay cell-level (user revision).** In vim NORMAL mode `gg`, `G`, `3G`, `3gg` keep their
   vim meaning inside the current cell's editor (first / last / N-th line). File-level navigation uses
   `ctrl+home` (first cell) and `ctrl+end` (last cell) in every mode, and in CELL mode `gg` / `G` select the
   first / last cell. `tests/test_vim.py::test_motions` stays unchanged.
5. **Markdown cells** – full implementation is plan 04; this plan only guarantees the hooks exist
   (`set_cell_kind` stub, `m`/`y`, `:md`/`:code` rows) so plan 04 is a drop-in.

## Dirty tracking and quitting

- `GPython.dirty: bool`; set on any `VimTextArea.Changed`, cell add/delete/move/kind change; cleared on
  save. Show `•` in the status bar and window sub-title.
- `action_quit` (ctrl+q) and `:q`: if dirty, push `Prompt("Unsaved changes. Quit without saving? (y/n) ")`
  and quit only on `y`. `:q!` quits unconditionally; `:wq` saves first (untitled → ask for a name, existing
  behaviour).
- `:w` on a plain script (`markers is False`) keeps it a plain script (existing).

## Undo delete & clipboard

- `GPython._deleted: list[CellSnapshot]` (max 50) where `CellSnapshot = (index, source, kind, mode,
  outputs_snapshot)`; `outputs_snapshot` can be the list of `(kind, content)` messages the cell received
  so `OutputArea.replay(snapshot)` restores them. Restore mounts a new `Cell` at `min(index, len(cells))`,
  replays outputs, marks status `stale` if it had run, re-runs nothing automatically (user presses run).
- Cell clipboard `GPython._cell_clipboard: CellSnapshot | None` for `yy`/`x`/`p`/`P`.
- Cut (`x`) behaves as delete (dependents re-run) plus copy.

## Ex commands (add rows to `ex_commands()`)

`:w [file]`, `:q`, `:q!`, `:wq`/`:x`, `:run` (all), `:runabove`, `:runbelow` (relative to current cell,
dependency order within the subset via `graph.sort`), `:restart`, `:clear` (all outputs), `:theme [name]`,
`:set vim` / `:set novim` (toggle `VimTextArea.enabled` for all editors + persist `vim` in config),
`:set lazy` / `:set nolazy`, `:set nu` / `:set nonu` (line numbers), `:help`, `:md` / `:code` (stub → plan 04),
`:N` (integer → same as `/N`: N-th cell by display position). Unknown → error notify.

## Help screen

`src/gpython/help.py`: `HelpScreen(ModalScreen)` with a scrollable `Static`/`Markdown` built from one
source of truth: a `KEYMAP: list[tuple[str, str, str]]` (mode, key, description) defined in
`src/gpython/keymap.py`. `app.py` BINDINGS, the Cell command-mode handler and the README table are all
generated from/consistent with `KEYMAP` (at minimum, a test asserts every App `Binding.key` appears in
`KEYMAP`). `escape`/`q`/`f1`/`?` close it.

## Mouse

- Clicking a cell's header selects the cell (CELL mode); clicking the editor focuses it (existing).
- Clicking the `[n]`/status icon area is the same as the header.

## Implementation notes

- `Cell.can_focus = True`; in `Cell.on_focus` → `add_class("-selected")` and `self.app.update_status()`;
  `on_blur` → remove the class and `update_status()` again. `GPython.current_cell()` must return the cell when the
  Cell itself is focused (it already walks up from `self.focused`).
- `action_run_cell(advance: str = "stay")` with `advance in {"stay", "next", "insert"}`; bind the three
  run variants to it with arguments: `Binding("shift+enter", "run_cell('next')", ...)`,
  `Binding("alt+enter", "run_cell('insert')", ...)`; `action_run_and_add` becomes a thin alias for
  `run_cell('next')` so existing tests keep passing (update their expectations where they assumed a new
  cell is always created).
  After `run_cells` is **scheduled** (not awaited), move selection: for `next`, select the next cell in
  CELL mode if the current mode was CELL, else focus its editor in NORMAL mode; create an empty cell if
  none exists. For `insert`, mount a new cell below and focus it in INSERT mode.
- Moving a cell: `container.move_child(cell, before=prev)` / `after=next`; then `invalidate_cells()`,
  `refresh_info()`, keep the selection on the moved cell, `scroll_visible(animate=False)`, `dirty=True`.
- `run_above/below`: ids = cells before/after the current (inclusive for below), `run_cells(ids, downstream=False)`.
- Keep `tests/test_vim.py` passing: `escape` in NORMAL with nothing pending now posts `LeaveEditor`; the
  test app there has no `Cell`, so the message is simply unhandled – fine.
- Persist `vim` and `line_numbers` via `config.save` on change; read them in `GPython.__init__`
  (constructor args win over config).

## Tests (`tests/test_commands.py`, new; keep `tests/test_app.py` green)

Use a 3-cell notebook without waiting for the kernel where possible (most of these are pure UI):

1. `escape` from INSERT → NORMAL → CELL: `app.focused is cell`, class `-selected` present on exactly one cell.
2. CELL `j`/`k`/`gg`/`G`/`3j` move the selection; `enter` focuses the editor in NORMAL; `i` in INSERT.
3. `a`/`b` insert above/below and the new cell is in INSERT mode; `ctrl+shift+n` inserts above.
4. `dd` deletes, `z` restores at the same index with the same source; `yy`+`p` duplicates below; `x`+`P` moves.
5. `shift+j`/`shift+k` and `alt+shift+down/up` reorder; `[c.source for c in app.cells()]` reflects it.
6. `o` toggles `-collapsed` class on the cell's `OutputArea`; `shift+o` clears outputs.
7. `shift+enter` on the last cell creates a new cell and selects it; `alt+enter` inserts and enters INSERT;
   `ctrl+shift+enter` runs in place (kernel test: output appears, focus unchanged).
8. Dirty: typing sets `app.dirty`; `ctrl+s` clears it; `:q` with dirty shows the Prompt and `n` keeps the app
   running; `:q!` exits.
9. `:3` selects the third cell; `:runbelow` runs only cells at/after the current one (kernel test).
10. `f1` opens `HelpScreen`; `escape` closes it. Every App binding key is listed in `KEYMAP`.
11. `/3` focuses the third cell by display order (after deleting the second cell, `/2` means the former
    third one; labels in headers renumber accordingly); inserting with `ctrl+n` between cells 1 and 2 shows
    the new cell as `c2` and the old second as `c3`; `/print` jumps to the next cell containing `print`;
    the variables panel starts hidden and `f2` shows it.
11b. `shift+enter` on a cell with a cell below runs it and focuses the next cell without creating one;
    on the last cell it creates a new cell in INSERT mode.
12. `:set nu`/`:set nonu` toggles `show_line_numbers` on all editors and writes `line_numbers` to the config
    (XDG_CONFIG_HOME = tmp_path).

Run `uv run python3 scripts/bench.py` – no metric regresses > 10 %; paste the table.

## Report

Files changed, tests summary, bench table, README key table updated, requests for peer-owned files
(none expected).
