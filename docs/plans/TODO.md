# gpython – open items (updated 2026-10-30)

State: plans 01–07 implemented (07 = file explorer, splits, tabs; see `docs/plans/07-windows.md`),
204 tests green (`uv run python3 -m pytest -q`, ~110 s). Read `docs/plans/README.md` first.

## 1. Performance (round 2 done 2026-10-30; what is left is Textual-internal)

`uv run python3 scripts/bench.py`, headless, 60 cells, quiet machine (never while the test
suite runs: every number doubles). "before" = main cad579e.

| metric | before | now |
|---|---|---|
| keys_30_net (CPU for 30 keystrokes minus harness floor) | 0.519 s | 0.14–0.19 s |
| keys_30_proc (process time, no profiler) | 0.209 s | 0.09–0.18 s |
| moves_30_proc (30 cell moves, process time) | 0.666 s | 0.50–0.52 s |
| mount_60 / loaded_60 | 0.200 / 0.452 s | 0.21 / 0.40–0.43 s |
| stream_2000 | 0.107 s | 0.11 s |
| run_trivial | 0.037 s | 0.030 s |

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

### Perf round 3 (profiled 2026-10-30, NOT started; do this in a fresh session)

Measured with `uv run python3 -X importtime -c "import gpython.app"` and
`uv run python3 scripts/bench.py --profile mount|moves|keys` (quiet machine, nothing else running):

1. Import before the first frame: 337 ms total. Eager imports that only some features need:
   `jupyter_client` 78 ms (kernel.py top level), `markdown_it` via `textual.widgets.Markdown`
   41 ms (markdown.py), `textual_image` + `PIL` 22 ms (outputs.py; its import also probes the
   terminal). Make them lazy: import inside `Kernel.start()`, inside `MarkdownView` when first
   composed, inside `OutputArea._add_image`. Check `textual_image` still works when imported
   after the app started (its comment says "must be imported before the app starts": test an
   image output in a real terminal; if it breaks, import it in `on_load` instead).
2. Start the kernels after the first frame: `GPython.on_load` boots them before compose; move
   to `call_after_refresh` in `on_mount` so the first paint is not delayed by kernel start
   (~100 ms CPU in `_boot_kernel`/`Kernel.start` during mount in the profile).
3. Mount: 12 `Screen._refresh_layout` calls during start-up at ~27 ms each (one per mount batch
   in `NotebookPane._mount_rest`, MOUNT_BATCH=10). Try larger batches after the first frame
   (e.g. 25) or `with self.app.batch_update():` around each batch; measure `loaded_60`.
   CSS `stylesheet.apply` is 0.24 s for 322 widgets: fewer widgets per cell would help (the
   header Static could become part of the TextArea gutter, a bigger change).
4. Real-terminal latency floor: Textual coalesces paints at `TEXTUAL_FPS` (default 60 →
   up to 16 ms between a key and its paint). Set `os.environ.setdefault("TEXTUAL_FPS", "120")`
   in `gpython/__init__.py:main()` BEFORE importing textual (constants read the env at import).
   Measure with a pty harness: write a key, time until output bytes arrive (see the pty smoke
   script idea in the 2026-10-30 session: `pty.fork` + `uv run --project ... gpython file.py`).
5. Cell moves: ~17 ms CPU per *scrolling* move, all inside Textual: `reflow_visible` (3.5 ms),
   the compositor rebuilding its full map on the next `widget.size` lookup (4 ms, one
   `_arrange_root` over all cells), one full-screen `_render_chops` (8 ms). Tried and rejected:
   `refresh(layout=True)` instead of a scroll (slower). Untried: a virtualised cell list (mount
   only cells near the viewport; big change), fewer widgets per cell.
6. Keystrokes are done: 3–6 ms CPU each (`tests/test_repaint.py` guards the partial repaints).

Rules: never bench while the test suite runs; run each bench twice; keep the table above updated.

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
