# 12 – Virtual cell list (2026-11-08)

Why: every keystroke and cell move costs in proportion to the mounted widgets (500 cells: 5 ms
per key, 25 ms per move, 1.2 s to load; 1000: 16 / 34 ms, 1.4 s). Budget: 10 ms per key, 20 ms
per move. Design decided in `TODO.md` §1 "Next: a virtual cell list".

## State and widget

- `cell.CellState`: the notebook's cell. Everything that was data on the `Cell` widget: cid,
  kind, spec, label, source (the editor's text while mounted, its own copy while not), cursor,
  editor undo history, status, run_status, ran_source, last_defs, inputs_stale, missing, info,
  exec_count, duration, the outputs log, echo_suppressed, code_hidden, folded, editing,
  collapsed, measured height, `widget` (the mounted `Cell` or None).
- All data operations live on the state (`set_status`, `begin_run`, `end_run`, `add_output`,
  `show_error`, `clear_output`, `restore_run`, `set_label`, `set_info`, `set_code_hidden`,
  `set_folded`, ...). Each one updates the widget through a `_show_*` hook when there is one.
- `Cell(Vertical)` is the view of one state: `Cell(state=...)` (or `Cell(source, kind, spec)`,
  which makes its own state). Delegating properties keep `cell.source`, `cell.status`,
  `cell.exec_count`, `cell.add_output(...)` and the rest working, so the ~40 call sites and the
  tests do not change. `on_mount` paints the state: classes, hidden editor, rendered markdown,
  outputs replayed from the log, running ticker, cursor and undo history.
- Unmounting (`CellState.sleep`): keeps text, cursor, undo history and the measured height. Lost:
  vim marks and jump list of that editor, visual selection, hints under outputs (not saved
  anyway), the markdown view's render cache (rebuilt on remount), decoded output images
  (re-decoded from the log on remount).

## Window and budget (`pane.py`)

- `pane.states` is the notebook from `__init__` on; `pane.cells()` is still the mounted widgets,
  in order; `pane._window = (lo, hi)` says which states they are: exactly `states[lo:hi]`.
- `MOUNT_BUDGET = 100`. A notebook with at most that many cells has every cell mounted once
  loaded and never mounts or unmounts on scroll: unchanged behaviour, the 60-cell bench cannot move.
- Over the budget the window is `MOUNT_BUDGET` states around the viewport. `_wanted_window`
  centres it on the visible cells; `_update_window` (after each paint that scrolled, through a
  `scroll_y` watcher) moves it one `MOUNT_BATCH` (10) at a time under one `batch_update`,
  mounting on one side and dropping on the other, with `WINDOW_STEP` (8 cells) of hysteresis.
  Start-up is the same loop growing the window from `_initial_count()` toward the budget; `loaded`
  is set when it is full.
- Scroll stays put: prepended cells shift `scroll_y` by their remembered (or estimated) height
  before the paint, and the first unchanged cell is re-anchored after it.
- Jumps (`G`, `gg`, search, `:N`, run-by-number, outline, undo, debugger stops) go through
  `select_state`: a mounted target is focused as before; otherwise `_reveal` mounts a small
  window (`JUMP_WINDOW` cells) around the target now, focuses it after the refresh, and the
  window grows to the budget behind it.
- The focused cell is never dropped silently: when the window has to leave it (mouse-wheel far
  away) the focus is cleared first, so Textual does not scroll back to a neighbour.

## What each pane operation does over the budget

| operation | iterates |
|---|---|
| `run_cells`, `BatchRun`, `run_all`, `run_stale`, `run_relative`, `run_number` | states (no `loaded.wait()`: F5 right after opening runs at once) |
| `write_file`, `all_specs`, `copy_text`, `_outputs_snapshot`, sidecar store | states |
| `relabel`, `refresh_info`, graph, outline, variables owners, `_kernel_reset`, `clear_outputs`, fold all | states |
| search (`_go_to_match`), `goto_cell`, `focus_cell`, `focus_edge`, `G` | states, then `select_state` |
| `_insert`, `_remove_cells`, `_move_to`, undo | states + the window (a cell inserted or moved outside the window is revealed) |
| `select`, `cell_command`, ex ranges, `:img`, debugger highlight | widgets (ex `%` ranges cover mounted cells only, see open questions) |

Undo history: kept (`TextArea.history` is moved to the new editor). Markdown: re-rendered on
remount. Images: re-decoded from the log.

## Tests (`tests/test_virtual_cells.py`, 300 cells)

window moves on `G`, `gg`, `/`, `:250`, `r 250`; a run over every cell updates unmounted cells
and their outputs show once mounted; save round-trips the whole file; mounted widgets stay
bounded after jumping around; a notebook under the budget never unmounts.

## Result (headless, 2 ms tick; before → after)

| metric | 60 cells | 500 cells | 1000 cells |
|---|---|---|---|
| loaded | 0.24 → 0.24 s | 0.95 → 0.31 s | 1.39 → 0.37 s |
| keys_30_net | 0.07 → 0.075 s | 0.08 → 0.13 s | 0.27 → 0.11 s |
| moves_30_proc | 0.31 → 0.28 s | 1.39 → 0.35 s | 2.91 → 0.35 s |
| first_paint | 0.06 → 0.06 s | 0.07 → 0.065 s | 0.07 → 0.07 s |

150 `j` moves through 500 cells cost 9.6 ms CPU per move with the window shifting every eight
cells (6.2 ms at 60 cells). warmbench, 500 cells from launch: last cell 1.51 → 1.40 s, memory
203 → 168 MB. The `keys_30_net` at 500 is the cost of 100 mounted cells (two reflows during
the 30 keys arrange 100 cells instead of 60); `keys_30_proc` is 0.045 s at every size.

## Open questions

- Ex commands over `%` (`:%s`, `:g`) only see mounted cells of a notebook over the budget.
- `MOUNT_BUDGET` 100 and `WINDOW_STEP` 8 are guesses; tune in a real terminal (the shift of
  eight cells is one hiccup of ~25 ms every eight moves).
- A window step that changes the top calls `Screen._refresh_layout()` (private) so the
  re-anchoring scroll lands before the paint; check it on Textual upgrades.
