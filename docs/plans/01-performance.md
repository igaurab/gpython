# Plan 01 – Performance: make gpython feel instant

Read `docs/plans/README.md` first (ownership rules, verified facts, baseline numbers).

## Goal

Every interaction must feel immediate: typing, moving between cells, running cells,
streaming output, and opening a notebook with 100+ cells. The work is measured, not guessed:
you create `scripts/bench.py` first, record the baseline, fix the known hot spots below,
profile for anything left, and report the before/after table.

Targets (headless, 60 cells × 10 lines, terminal size 140×45, Apple Silicon):

| Metric | Baseline | Target |
|---|---|---|
| App mount with 60 cells (time until `run_test` enters the body) | 2.20 s | ≤ 0.60 s |
| Time to first frame with 200 cells | not measured | ≤ 0.80 s |
| CPU per keystroke in a cell (cProfile, excluding event-loop waits) | ≈17 ms | ≤ 6 ms |
| 30 cell-to-cell moves via `action_focus_cell` | 5.27 s | ≤ 0.60 s |
| 2 000 `add_output("stream", …)` chunks into one cell, then one `pilot.pause()` | 5.33 s | ≤ 0.25 s |
| Run trivial cell end-to-end (`run_cells`, kernel ready) | 42 ms | ≤ 40 ms (no regression) |

## Step 0 – benchmark script (do this first)

Create `scripts/bench.py` (runnable with `uv run python3 scripts/bench.py [--cells N] [--json]`).
It must:

1. Build a temp notebook with N cells (default 60), each `v{i} = {i}\nprint(v{i})` followed by 8
   comment lines, using `# %%` markers.
2. Measure, in this order, printing one row per metric as `name<TAB>seconds`:
   - `import_app`: `import gpython.app` time (fresh interpreter not required; measure once at top).
   - `mount_N`: wall time from `GPython(path)` to inside `async with app.run_test(size=(140, 45))`.
   - `first_pause`: one `await pilot.pause()` right after mount.
   - `keys_30_cpu`: focus cell 0, enter insert mode (`i`), wrap `await pilot.press(*30 letters)` in
     `cProfile`; report **total tottime excluding** `select.kqueue`/`select.select`/`_run_once`
     waiting (sum `tottime` of all functions minus the `{method 'control' of 'select.kqueue' objects}`
     and `{method 'select' ...}` rows). Also report `keys_30_wall`.
   - `moves_30`: 30 × (`app.action_focus_cell(1)` then `await pilot.pause()`).
   - `stream_2000`: 2 000 calls `cell.add_output("stream", {"name": "stdout", "text": f"line {i}\n"})`
     on one cell followed by one `await pilot.pause()`; then assert the cell's `output_text` ends with
     `"line 1999\n"` (correctness guard for the batching you will add).
   - `kernel_ready`: `await app.kernel.ready.wait()` measured from app construction (overlaps the above;
     report the absolute wall time since construction when it became ready).
   - `run_trivial`: `await app.run_cells([cells[1].cid])`.
   - `run_print_3000`: set a cell's text to `for i in range(3000): print('x', i)` and `run_cells` it.
3. `--json` prints a dict instead. Exit code 0.
4. Record the baseline table at the top of your final report **before** changing any source.

Set `TEXTUAL_HEADLESS`-independent: `run_test` is already headless.

## Step 1 – stream output batching (fixes `stream_2000`)

Problem: `Cell._add_text` re-renders the whole accumulated stdout with `Text.from_ansi` on **every**
chunk and mutates the widget each time → O(n²) and one layout per chunk. tqdm progress bars and
`print` in loops with `sleep` produce hundreds of chunks per second.

Implement an `OutputArea` (new module `src/gpython/outputs.py`, moving output logic out of `app.py`):

- `class OutputArea(Vertical)` replaces the output `Vertical` inside `Cell`. API used by `Cell`/`App`:
  `clear()`, `add(kind: str, content: dict)` (same `kind`/`content` as `Kernel.execute` callbacks),
  `show_error(message: str)`, `text` property (all text output concatenated – `Cell.output_text` must
  return this), `has_output` property.
- Stream handling: keep `self._stream_kind`, `self._stream_parts: list[str]`, `self._stream_widget:
  Static | None`, `self._flush_timer: Timer | None`. On `add("stream", …)`: if same kind as the open
  stream, append text to `_stream_parts`; else finalize the previous stream (flush now) and start a new
  one. Schedule a flush with `self.set_timer(1/30, self._flush)` only if no timer is pending. `_flush`
  joins parts, applies carriage-return semantics (for each line, keep only the text after the last
  `\r` – progress bars render as a single updating line), trims to the **last** `MAX_STREAM_CHARS`
  (50 000) characters cut at a line boundary with a leading `… (N earlier characters hidden)` note,
  and calls `widget.update(Text.from_ansi(...))`. The `text` property must include unflushed parts, so
  tests and `output_text` are correct without waiting (join parts on read).
- Non-stream outputs (`execute_result`, `display_data`, `error`, `clear_output`) flush any pending
  stream first so ordering is preserved, then mount their widget as today (`Static`, `Image`).
- On `clear()` cancel the pending timer. On `_flush` when the widget is no longer mounted, do nothing.
- Avoid `Static` default `height: auto` re-measuring per chunk: the single flush already fixes this.
- Keep `Cell.output_text` working synchronously right after `add_output` (existing tests rely on it).

Also move `_render(kind, text) -> Text` into `outputs.py`.

## Step 2 – cheap focus changes (fixes `moves_30`)

Profile showed each move spends most time in `stylesheet.apply`/`update_nodes` (hundreds of nodes
restyled because `Cell:focus-within` and `TextArea:focus` invalidate the tree) plus the `scroll_visible`
animation timers.

- Replace the `Cell:focus-within` selector in `app.tcss` with a class `Cell.-focused`. In `Cell`,
  handle `events.DescendantFocus` / `events.DescendantBlur` (or `on_descendant_focus`/`on_descendant_blur`)
  to `set_class(True/False, "-focused")`. Only the two affected cells restyle.
- `Cell TextArea:focus { border: none; }` already exists; keep border `none` in both states so the
  focus pseudo-class causes no layout change.
- In `action_focus_cell` and `action_add_cell` call `scroll_visible(animate=False)` (or
  `immediate=True`). Also `editor.scroll_cursor_visible()` if the method exists; otherwise skip.
- `Cell.set_status`: replace the loop of 7 `set_class` calls with a single
  `self.set_classes(...)`-style update or `remove_class(*others)` + `add_class(new)` and early-return when
  the status is unchanged. `_render_header` only when the header text actually changes (cache last Text
  string).
- Check `Footer` cost: run the moves benchmark with `Footer` temporarily removed. The app already has a
  bottom `Static#status` line (`update_status`). If Footer accounts for > 30 % of the move time, remove
  `Footer` and append the 6 most important keys to the status line as dim text
  (`ctrl+enter run · shift+enter run+new · ctrl+n new · ctrl+d delete · ctrl+s save · ctrl+p palette`).
  If Footer is cheap, keep it. Either way `update_status()` keeps its signature.

## Step 3 – fast startup (fixes `mount_N`, first frame)

Profile `mount_60` with cProfile (`run_test` construction until the body). Expected culprits:

1. `VimTextArea.on_mount` builds (`dataclasses.replace`) and registers a `TextAreaTheme` per instance →
   build the normal-mode variant **once** at module level and only `register_theme` it per instance.
   Introduce `VimTextArea.set_editor_theme(insert: TextAreaTheme, normal: TextAreaTheme)` that
   registers both and remembers their names; `_apply_mode()` uses the remembered names instead of the
   `"monokai"`/`"monokai-normal"` literals (default pair = monokai). Plan 02 builds on this hook.
2. Tree-sitter language/highlight query loading per `TextArea`. Check whether Textual caches the
   `Language`/`Query` (look at `textual/widgets/_text_area.py` and `textual/document/_syntax_aware_document.py`).
   If it does not, add a module-level cache (e.g. memoize `textual.widgets.text_area` helper that builds
   the language, by monkeypatching is NOT allowed – instead construct `VimTextArea` with a shared
   `language` object if the API accepts one, else leave it and rely on step 3.3).
3. **Progressive mounting.** Compose only the first `INITIAL_CELLS = 12` cells in `compose()`; store the
   remaining sources on the app. In `on_mount`, after the first screen refresh
   (`self.call_after_refresh(self._mount_rest)`), mount the rest in batches of 10 with an `await`
   between batches so the first frame appears immediately. Expose `self.loaded: asyncio.Event` set when
   all cells are mounted. `cells()` keeps returning document order. `refresh_info()` runs once after the
   last batch. Update `tests/test_app.py` to `await app.loaded.wait()` where it reads `app.cells()` right
   after mount (the kernel wait already takes 0.5 s, but make it explicit). The bench's `mount_N` measures
   the first frame; add `loaded_N` = time until `app.loaded` is set.
4. Start the kernel as early as possible: kick off `self.run_worker(self._start_kernel(), group="kernel")`
   in `on_load` (runs before `compose`) instead of `on_mount`, so the 0.5 s kernel boot overlaps widget
   mounting. `Kernel.start` must not touch widgets (it does not).
5. Lazy-import `PIL` (already lazy) and keep `textual_image.widget` imported at module import time (it
   must probe the terminal before the App starts – do not move it).

## Step 4 – keystroke cost (fixes `keys_30_cpu`)

Profile the 30-key press with cProfile sorted by `tottime` and by `cumtime`. Known contributors:

- The vim `mode` is a class attribute and `_apply_mode()` runs on every focus; it sets `self.theme`
  (cheap if unchanged? verify – if setting `theme` to the same name re-renders, guard it).
- Each edit in a `height: auto` TextArea triggers a full-screen layout (`_refresh_layout` →
  `_arrange_root` over ~200 widgets). Mitigations, in order, keeping whichever measurably helps:
  a. `Cell` and `.cell-output` and `#cells` should have no `margin`/`padding` that forces re-measure of
     neighbours; keep `height: auto` on `Cell` (needed).
  b. Make `VimTextArea` only request a layout when its **line count** changed (height actually
     changes); for same-height edits call `self.refresh()` only. Textual's TextArea calls
     `self._rewrap_and_refresh_virtual_size()` → `refresh(layout=True)`?? Inspect
     `textual/widgets/_text_area.py` (`_refresh_size`, `_on_text_area_changed`, `watch_*`). If an
     override is possible without copying large private methods, override the narrowest method and
     guard on `line_count` change. If not possible cleanly, document why and skip.
  c. `Cell._mark_stale` reads `self.source` (joins the document) on every keystroke; compare with
     `self.editor.document.line_count` and a cheap hash only when `ran_source is not None`.
  d. Ensure the header `Static` is not re-rendered on each keystroke (only on status change).
- Verify with the bench; report CPU per keystroke before/after.

## Step 5 – misc

- `GPython.cells()` is called many times per action (`current_cell`, `graph`, `refresh_info`, …). Cache
  the list on the `#cells` container: invalidate on mount/remove/move (`on_mount`/`on_unmount` of `Cell`
  can call `self.app.invalidate_cells()`). Keep the method name `cells()`.
- `refresh_vars` after every run: skip if the set of defined names and the run's touched names are
  unchanged? No – values change. Keep, it is 3 ms.
- Remove dead code and keep `app.py` readable: after this plan `app.py` should contain `Prompt`,
  `Cell`, `GPython`; outputs live in `outputs.py`, status bar (if added) in `statusbar.py`.

## Tests

- `tests/test_outputs.py` (new): stream chunks accumulate (`text` correct before and after flush),
  carriage returns collapse to the last segment per line, trimming to `MAX_STREAM_CHARS` keeps the tail
  and adds the hidden-characters note, `clear_output` cancels pending flush, stdout→stderr→stdout makes
  three widgets, `execute_result` after a stream preserves order. Drive with a tiny `App` in `run_test`.
- `tests/test_app.py`: keep all existing tests; add `test_progressive_mount_order(tmp_path)`: 40-cell
  file, `await app.loaded.wait()`, `[c.source for c in app.cells()]` equals the file order; and
  `test_focus_class_moves`: after `action_focus_cell(1)` exactly one `Cell` has class `-focused`.
- All tests green: `uv run python3 -m pytest -q`.

## Report

Before/after table from `scripts/bench.py`, the cProfile top-10 for keystrokes after your changes,
list of files changed, anything you could not reach and why.
