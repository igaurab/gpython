# gpython – open items (updated 2026-10-30)

State: plans 01–07 implemented (07 = file explorer, splits, tabs; see `docs/plans/07-windows.md`),
204 tests green (`uv run python3 -m pytest -q`, ~110 s). Read `docs/plans/README.md` first.

## 1. Performance (rounds 2 and 3 done 2026-10-30; what is left is Textual-internal)

`uv run python3 scripts/bench.py`, headless, 60 cells, quiet machine (never while the test
suite runs: every number doubles). "before" = main cad579e. A run takes ~2.5 s since
2026-10-30, when the pilot's idle-poll tick was cut from 20 ms to 2 ms (`use_fast_idle_tick`
in the script): the poll's wakeups used to land inside the measured spans, so the keys and
moves rows got smaller without the app changing. Compare only against the "2 ms tick" column.

| metric | before | 20 ms tick | 2 ms tick |
|---|---|---|---|
| keys_30_net (CPU for 30 keystrokes minus harness floor) | 0.519 s | 0.14–0.19 s | 0.09–0.11 s |
| keys_30_proc (process time, no profiler) | 0.209 s | 0.09–0.18 s | 0.06 s |
| moves_30_proc (30 cell moves, process time) | 0.666 s | 0.50–0.52 s | 0.40–0.43 s |
| mount_60 / loaded_60 | 0.200 / 0.452 s | 0.21 / 0.40–0.43 s | 0.07–0.09 / 0.31–0.40 s |
| first_paint | – | 0.08–0.09 s | 0.07–0.09 s |
| stream_2000 | 0.107 s | 0.11 s | 0.03 s |
| run_trivial | 0.037 s | 0.030 s | 0.045 s |

What changed (see `docs/plans/07-windows.md` "Gotchas" and the memory note on Textual pitfalls):
- `VimTextArea.selection` reactive no longer repaints the whole editor on every cursor move;
  `edit()` and `_watch_selection()` repaint only the rows involved plus rows whose tree-sitter
  highlights changed (`tests/test_repaint.py` proves pixel equality with a full render).
- A wider line no longer repaints the editor (`_refresh_size`).
- Cell state colours (focus, selection, status) are painted inline through
  `styles.outline_left`; the `-focused/-selected/-status` classes are kept for tests but added with
  `update=False`, so a cell move no longer re-applies CSS to two subtrees or triggers a layout.
- `NotebookScreen.focused` has repaint=False: Textual repainted the whole screen on every focus change.
- Fixed-size `Static`s (status bar, window status lines, cell headers, tab bar) update with
  `layout=False`; the header kernel chip (width auto) only re-renders when its label changes.

Remaining cost per *scrolling* cell move (≈17 ms CPU) is inside Textual: `reflow_visible`
(≈3.5 ms), the compositor rebuilding its full map on the next `widget.size` lookup (≈4 ms, one
`_arrange_root` over all 60 cells) and one full-screen `_render_chops` (≈8 ms). Tried and
rejected: forcing `refresh(layout=True)` instead of a scroll (slower). Ideas not tried: fewer
widgets per cell (merge the header into the editor gutter), a virtualised cell list.

Verify in a REAL terminal (kitty/ghostty/iTerm2), not only headless: typing latency, scrolling a
150-cell marimo notebook, theme switching with the picker, splits with 3+ windows.

### Perf round 3 (done 2026-10-30)

Headless (`scripts/bench.py`, 60 cells) and a real pseudo-terminal (`scripts/ptybench.py`,
launch `./gpython bench.py`, answer the terminal queries, press `j` ten times):

| metric | before | after |
|---|---|---|
| import of `gpython.app` (`-X importtime`) | 160–195 ms | 150–165 ms |
| first paint after `GPython()` (headless `first_paint`) | 0.19 s (`mount_60`) | 0.08 s |
| cells mounted before the first paint | 12 | what fits the screen (4 for 45 rows) |
| `loaded_60` (every cell mounted) | 0.37 s | 0.30–0.36 s |
| `kernel_ready` | 0.57 s | 0.65–0.75 s (starts after the first frame) |
| pty: launch → first cell fully on screen | 0.39–0.42 s | 0.25–0.29 s |
| pty: `j` → paint | 10–16 ms (33 ms worst) | 1–9 ms (most under 5) |
| `./gpython --help` (launcher overhead) | 121 ms | 36–90 ms |

What changed:
- `jupyter_client` is imported on the first `Kernel.start()`, not at import (`Kernel.manager`
  is None until then); `textual.widgets.Markdown` is imported when a `MarkdownView` composes.
  `textual_image` must stay eager: it queries the terminal and that only works before Textual
  owns stdin (headless it costs ~14 ms with PIL).
- `GPython.after_first_frame(callback)`: during start-up Textual runs everything inside a
  `batch_update`, where `call_after_refresh` fires *before* the first paint (the screen's idle
  skips the refresh check while a batch is open). `Ready` arrives once the batch is over, so a
  `call_after_refresh` from `on_ready` lands after the first frame. The kernel boot and the
  cells below the fold (`NotebookPane._mount_rest`) wait for it; the boot was in `on_load`.
- `NotebookPane._initial_count()` composes only as many cells as fill `app.size.height`
  (at least 4) before the first frame instead of a fixed 12.
- Each `_mount_rest` batch is wrapped in `app.batch_update()` (one layout pass per batch);
  MOUNT_BATCH stays 10 so keys get through between batches. Measured: batch 10 without
  0.39–0.42 s, with 0.32–0.34 s, batch 56 with 0.30–0.32 s (not worth losing the key gaps).
- `TEXTUAL_FPS=120` is set in `gpython.main()` before textual is imported.
- `./gpython` execs `.venv/bin/gpython` directly when the env exists (`uv run` cost ~85 ms per
  launch); run `uv sync` after changing dependencies.

Left on the table: Textual's own import (~100 ms: `textual.app`, css, compositor, command
palette), tree-sitter's Python query compile (15 ms, once), and bash start-up in the launcher
(~35 ms; a symlink straight to `.venv/bin/gpython` avoids it).

### Perf round 4 (2026-10-31): focus changes no longer re-apply CSS; what the bench really measures

Traced every message during 30 cell moves (headless, 60 cells). Findings, in order of size:

- 243 of the ~290 messages per move were `Callback`s posted by the pilot's `_wait_for_screen`
  (one to every widget on each `pilot.pause()`), each costing a message dispatch plus an
  `inspect.signature` call. That is harness cost, not app cost: 30 pauses alone are
  0.16 s of process time. `bench.py` now reports `pause_30_floor_cpu` and
  `moves_30_net = moves_30_proc - pause_30_floor_cpu`, the app's own cost of 30 moves.
- Textual's `Widget.watch_has_focus` calls `update_node_styles()` on every focus change,
  re-applying the stylesheet to the widget and all its children (and, for a TextArea, its
  eight component classes). No rule in `app.tcss` depends on `Cell:focus` or on the editor's
  `:focus` (the cell's TextArea has `border: none` either way; the focus colour is painted via
  `outline_left`), so `Cell` and `VimTextArea` override `watch_has_focus` to skip it. The
  `Cell TextArea:focus { border: none }` rule was dead and is gone.
- Every scrolling move ends in `render_full_update` (the compositor marks the whole screen
  dirty when the visible map moves). Non-scrolling moves are partial updates. This is Textual's
  design; fewer widgets per cell is still the only lever.
- A title/sub-title change (`HeaderTitle.set_title` → `Static.update` with layout) forces one
  full reflow and repaint. It happens once per dirty/clean toggle, not per key.

| metric (2 ms tick) | before | after |
|---|---|---|
| moves_30_proc | 0.41–0.50 s | 0.36–0.39 s |
| moves_30_net (new) | – | 0.20–0.22 s (≈7 ms CPU per move) |
| pause_30_floor_cpu (new) | – | 0.16 s |
| keys_30_proc / keys_30_net | 0.05–0.06 / 0.10–0.15 s | 0.05 / 0.13 s (unchanged) |
| first_paint / loaded_60 | 0.07–0.08 / 0.31–0.38 s | 0.07 / 0.31–0.33 s (unchanged) |

Second half of round 4 (same day), from `--profile mount` (CSS apply was 0.22 s of a 0.88 s
profiled mount, half of it `_process_component_classes`) and the import profile:

- `Cell` mounts its `OutputArea` on the first output (`output_box`): most cells never have
  one, every widget costs ~1 ms of CSS and layout at start-up, and marimo files save none.
- `VimTextArea._get_component_classes()` returns nothing: TextArea's eight component classes
  (`text-area--cursor`, `--gutter`, ...) were only fallbacks for theme fields that every
  gpython theme sets, yet Textual ran a full stylesheet match for each of them per editor
  at mount and on every theme change. `get_component_rich_style` returns an empty style for
  the lookups TextArea still makes. (`inherit_component_classes=False` from the docstring does
  not exist in Textual 8.2.)
- Rejected after measuring: importing `jupyter_client` in a thread (`kernel_ready` 0.60-0.65 →
  0.68-0.78 s, `loaded_60` unchanged); `MOUNT_BATCH` 20 or 30 (no change).
- Measured, not done: dropping the per-cell header `Static` (60 widgets) gives `loaded_60`
  0.27 → 0.245 s and `keys_30_net` 0.085 → 0.075 s, `moves_30_net` unchanged. It needs the
  header drawn without a widget (e.g. the cell's top border title) and changes the look, so
  it is the user's call.
- Import time (145 ms with `-X importtime`, ~80 ms real) is spread over 346 modules: Textual
  ~100 ms, textual_image + PIL ~25 ms (must stay eager for the terminal query). No single
  lever left there.

| metric (2 ms tick) | start of round 4 | end |
|---|---|---|
| loaded_60 | 0.31–0.38 s | 0.27–0.29 s |
| keys_30_net | 0.10–0.15 s | 0.08–0.09 s |
| moves_30_proc / moves_30_net | 0.41–0.50 / – | 0.34–0.37 / 0.21–0.24 s (machine drift ±0.03) |
| first_paint | 0.07–0.08 s | 0.07 s |
| pty first_frame / j → paint | 0.28–0.29 s / 3–15 ms | same (the spread is the machine) |

## 2. Acceptance in a real terminal

- `./gpython ~/leetcode/notebooks/0001-two-sum.py`: run all (f5), problem statement renders as
  markdown, `:w` leaves the file byte-identical (headless check passes; confirm on screen).
- `./gpython ~/leetcode/problems/02-two-pointers/0015-3sum.py`: first cell shows as rendered
  markdown, `enter` edits it, `escape escape` renders.
- `ctrl+t` picker: preview while moving, `escape` restores, `enter` persists to
  `~/.config/gpython/config.toml`.
- Keys that need the kitty keyboard protocol (`shift+enter`, `ctrl+enter`, `ctrl+shift+enter`,
  `alt+enter`): confirm in the user's terminal; `ctrl+e` is the fallback.

## 3. README polish

Short sections for: themes (list + picker + config file), markdown cells (exists, review), marimo and
`.ipynb` support (what round-trips, what is lost: decorator kwargs other than `hide_code`, comments
after `return`), the `--theme` / `--no-vim` flags, the key table (generated from `keymap.py`).

## 4. Small follow-ups

- `tests/test_commands.py::test_shift_enter_keeps_the_viewport` was flaky; fixed by
  `GPython._keep_focus_visible` and passed 6/6 afterwards. Keep an eye on it.
- Window resizing (`ctrl+w + - < >`) changes weights by 10 % steps; no mouse drag of the
  separator yet.
- `:split` with no file opens an untitled notebook (vim shows the same buffer twice; we
  cannot, one kernel per window).
- Done 2026-10-30: marimo `hide_code=True` cells open with the code hidden (`h` in cell mode,
  `:hide` / `:show`; saved back as `hide_code`); `.ipynb` stored outputs and execution counts load,
  display and save (`CellSpec.outputs`, `cell.outputs_from_ipynb` / `outputs_to_ipynb`).
