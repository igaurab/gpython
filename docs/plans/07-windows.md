# Plan 07: file explorer, windows (splits) and tabs

Status: implemented (2026-10-30).

gpython opens several notebooks at once with vim's model: a **window** shows one
notebook, windows can be split, **tabs** group windows, and a **file explorer**
sidebar opens files into any of them.

## Architecture

- `cell.py` – `Cell` moved out of app.py unchanged; `Cell.pane` finds its window.
- `pane.py` – `NotebookPane(Vertical)`: everything that used to be per-app state is
  per window: `path`, `kernel` (one kernel per notebook), cells box, `#vars` table,
  `dirty`, `exec_counter`, undo stack, `loaded`, progressive mount, cell mode
  (`cell_command`), run/insert/delete/move/undo/kind, save, restart. A per-window
  status line (`.pane-status`) shows only when the tab has several windows
  (vim's `laststatus=1`); the active window's line is highlighted (`-active`).
- `windows.py` – the split tree (`Leaf`/`Branch` with weights) per `Tab`, kept
  **outside the DOM**: `Workspace` holds every pane as a direct child and a custom
  `WindowLayout` places the current tab's panes at rectangles computed from the
  tree. Splitting or closing therefore never re-mounts a pane (Textual cannot
  reparent widgets), so cells, outputs and kernels stay intact. Other tabs' panes
  are `display: none`. `TabBar` is the tabline (hidden with one tab; click to switch).
- `explorer.py` – `Explorer(DirectoryTree)` with vim keys, text icons, notebook
  filter (`.py`/`.ipynb` + folders; `.` shows all), `Open(path, how)` messages.
  It is mounted on first use because DirectoryTree's loader is a never-ending
  worker that would block `app.workers.wait_for_complete()` in tests and the bench.
- `app.py` – global state only (theme, vim mode, status bar, prompts, ex table).
  Delegating properties keep the old test API: `app.kernel/path/dirty/loaded/
  cells()/current_cell()/run_cells()` all mean "the active pane". `ctrl+w` opens a
  `KeyCapture` modal that waits for the second key; in insert mode `ctrl+w`
  deletes a word as in vim.

## Keys and commands

See `keymap.py` ("files" mode and the ctrl+w rows) and README "Files, windows and
tabs". `ctrl+b` toggles the explorer; `ctrl+w h` / `ctrl+w w` focus it from a window.

## Gotchas found while building

- Names on widget subclasses must not shadow Textual internals: `Widget._set_dirty`
  (repaint) and `MessagePump._closing` (shutdown flag) both bit us; the latter made
  `remove()` wait forever. The check in `tests/test_windows.py` style: compare
  `dir(base)` with the attributes a class sets.
- A tab's root is replaced wholesale by `split_leaf`/`remove_leaf`; always assign the
  return value back to `tab.root`.
- Opening a file that is already open jumps to it (a second instance would need a
  second kernel on the same file).
