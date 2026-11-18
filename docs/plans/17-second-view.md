# 17 – A second window on the same notebook; mouse-resized splits (2026-11-18)

Why: `:vsplit`, then `:e sc.py` in the new window jumped back to the window that already
showed `sc.py`. The user chose vim's model: both windows show the same notebook.

## Model

- `pane.Buffer`: what belongs to the notebook. Path, markers, dirty, `states`, kernel (and its
  boot, lock, state, label), run counter, undo/redo timeline, the kernel's values, `views`
  (the panes showing it), `runner` (the pane whose run or debugger holds the kernel).
- `NotebookPane(path)` makes a Buffer; `NotebookPane(view_of=pane)` shares one. The shared
  attributes are properties on the pane (`cell.delegated("buffer", name)`), so `pane.kernel`,
  `pane.dirty`, `pane.states` read the same as before from every module and test.
- Per window: scroll, mounted window of cells (`_window`), focus, outline and variables
  panels, search, cell-mode counts, the debugger's drawing.
- `CellState.widgets`: one `Cell` per window that has the cell mounted. `state.widget` is the
  first; a pane finds its own with `pane.widget_of(state)` (`Cell(state=…, owner=pane)`).
  Every `_show_*` hook goes to all widgets, so status, outputs, labels, folds reach both.
- Markdown `editing` is per widget: one window edits, the other stays rendered and renders
  the new text 0.3 s after it changes.

## Text and undo

- The editors of one cell share one `EditHistory` (`state.history` always points at it).
- `VimTextArea.edit` tells its cell (`Cell.edit_done`); the cell's other editors repeat the
  edit with `mirror_edit` (recorded nowhere, own vim state kept, cursor keeps its offset).
- `undo` / `redo` pop the shared history once; the other editors apply the same batch
  (`mirror_history_step`) and keep their cursor.
- A cell with one widget pays one length check per edit: the bench did not move.

## Structure changes

`_insert`, `_remove_cells`, `_move_to`, `set_cell_kind` end with `_tell_views`. Each other
view runs `_follow_structure` under its window lock: widgets of removed or kind-changed cells
go, kept ones stay or move, missing ones are mounted; up to MOUNT_BUDGET it mounts everything,
above it a window around its first visible cell, then `_update_window` takes over.
`_following` holds that view's `_update_window` back until it has run.

## Windows

- `open_file`: a file shown in another window, asked for "here" or in a split, gets a view
  (`:e`, `:sp f`, `:vs f`, `:split v f`). In a tab (explorer enter, finder) it still jumps.
- Closing a window (or replacing its pane) whose notebook another window shows removes the
  pane; the kernel stops with the last one. A hidden buffer is therefore always the only pane
  of its notebook. `:bd` closes every view. `Workspace.notebooks()`: one pane per notebook
  (save all, quit prompt, kernel shutdown).
- A view closed while its run holds the kernel hides and is removed when the run ends
  (removing it would cancel the run's worker).
- `Workspace._drop` clears the focus first: Textual handed it to a cell of the next window
  and scrolled that window.

## Mouse

`windows.borders` lists the lines between a branch's children: the gap column of
side-by-side windows, the status line of the upper of two stacked ones. Mouse down on one
captures the mouse; moves call `drag_border`, which sets the weights of that branch to the
cell sizes (each side keeps MIN_SIZE cells).

## Limits

- `:ls` lists a notebook once per window.
- The debugger panel shows only in the window that started the run.
- A cell's stored cursor (while unmounted) is shared between windows.
- Cells added above another window's viewport shift its content.

## Result

`tests/test_second_view.py`. bench (before, before, after, after): first_paint 0.066 0.063
0.063 0.063; keys_30_net 0.075 0.076 0.077 0.076; moves_30_proc 0.319 0.318 0.315 0.316;
loaded_60 0.255 0.246 0.257 0.252.

## Unresolved

- Should a bare `:vsplit` / `ctrl+w v` show the same notebook (vim) and not a blank one?
