# 10 – Variables panel and the breakpoint() debugger

`ctrl+v` (`:vars`, `:variables`) opens the variables panel in the right column of a notebook
window, under the outline when both are open (`SideColumn` in `variables.py`). It is built on
first use; hidden it costs nothing. While a cell is paused in the debugger the same panel shows
the debugger, and the column widens to 82 columns (max 60 %) until the run ends.

## Variables mode

- Rows from `Kernel.variables` → `_gp_vars` → `kernel_side.variables`: `[name, type, value,
  shape/len/dtype, category]`. Category (module, class, function, data) hides the first three
  unless `a`. The defining cell is `NotebookPane._owners` (the outline's ownership rule).
- `enter` fetches `_gp_full(name)` (text capped at 200 000 chars, tables at 1000 × 50; data on
  a GPU is described, never copied). `g` jumps to the binding line, `d` runs `_gp_delete` and
  marks the defining cells stale, `/` filters by name or type.

## Transport

- `Kernel._execute` sends `execute_request` itself so the metadata carries `cellId`; ipykernel
  passes it to IPython's `pre_run_cell`, where `kernel_side.install` maps the cell's code
  filename (`compile.get_code_name(raw_cell)`, `/tmp/ipykernel_*/<hash>.py`) to the cell id.
- `allow_stdin=True` for cell runs; a task reads the stdin channel while the run lasts. An
  `input_request` whose prompt starts with `MARKER` is a debugger stop (JSON after the marker),
  anything else is `input()` (bottom prompt, escape interrupts). Answers go back with
  `client.input`.
- A restart bumps `Kernel._generation`; a run waiting on the old process sees it and returns,
  and the restart opens fresh channels so no reply of the old process is read by the new one.

## Debugger (kernel side, `kernel_side.Debugger`)

- A subclass of the standard library's `pdb.Pdb` (ipykernel replaces `pdb.Pdb` with IPython's,
  so the class is found in that MRO). `sys.breakpointhook` points at it. Its `prompt` is a
  property: every prompt is the stop's payload. Private commands: `_gp_next N`, `_gp_end`,
  `_gp_goto DEPTH N`, `_gp_until EXPR`, `_gp_until_rec EXPR`, `_gp_full EXPR`, `_gp_watch`,
  `_gp_unwatch`, `_gp_history BEFORE`, `_gp_frame N`.
- IPython runs each top-level statement as its own code object, so stepping off a statement's
  frame steps into the next one; `post_run_cell` (`run_ended`) removes the hook and the trace.
- n / s / r use pdb's own trace. The run-until modes drop the trace: on Python 3.12+ a
  `sys.monitoring` tool of its own (`LineHook`, a free id among 3–5) gets LINE events, returns
  `DISABLE` for code that is not a cell's, and RAISE events stop at an exception; on arrival it
  frees the tool, puts pdb's trace back on the frame chain and calls `interaction` in place.
  Older Pythons keep the trace and check the goal in `dispatch_line`. When nothing is being
  stepped, waited for or recorded, no hook and no trace is installed.
- `:stop if` compiles the expression once and evaluates it on the cells' lines only; it records
  nothing on the way (`B` / `_gp_until_rec` records).
- Loop positions: the AST gives each cell's loops (`CellInfo`); a header line event counts an
  iteration (a fresh entry resets to 1). Without counts, `range` and `enumerate` targets give
  the position. n comes from `len()` of the iterable when that is cheap and safe
  (`safe_value`: names, attributes, ints, arithmetic, `len`, `range`, `enumerate`, `zip`,
  `reversed`, `.items()`). On a header line the loop shows the iteration that just ended.
- Pointers: `seq[i]`, `seq[j + 1]`, `seq[lo:hi]` and `len(seq) - k` in the AST give `seq` its
  index expressions; `dp[i][j]` and `a[i, j]` give grid cells. `for x in seq` points at `k - 1`.
  Warnings (past the end, before the start, counting from the end, two pointers crossed) are
  computed in gpython (`debugview.seq_warnings`).

## Record format

A stop's `now` is a whole record: `{cell, file, line, func, loops: [{line, header, kind, vars,
k, n}], seqs: [{name, type, len, start, items, ptrs}], grids: [{name, rows, cols, row0, col0,
cells, ptrs}], maps: [{name, kind, len, items}], vars: [[name, short]]}`.

The recorder (`kernel_side.Recorder`) stores entries:
`{i, cell, line, func, loops, key, vars: {name: short}, seqs: {name: view}, grids, maps, gone}`.
A keyframe (`key`) holds every name; a delta holds the names whose summary changed and the names
that went away. "Changed" is the summary string; ints and strings whose `id()` did not change
are not summarised again. Objects are never deep-compared or copied.

- Caps: bytes (the JSON size of each entry) and count. The oldest entries are dropped first and
  the next one absorbs them (`merge`), so the oldest entry is always a keyframe.
- Thinning: every line of the last 3 iterations of the innermost loop, one end-of-iteration
  entry for older ones, every 10th iteration beyond 100, every 100th beyond 1000 (aggressive:
  1, 20, 200). Dropped entries are merged into the next kept one. A pass runs when the entries
  have doubled since the last one, so the cost stays linear.
- Batches: the kernel can only speak while paused, so history goes out with each stop (the
  newest 300 new entries, from a keyframe) and on `_gp_history BEFORE` (300 older entries from a
  keyframe, with the index of the entry before the batch so gaps are known).
- gpython (`debugview.DebugSession`) mirrors the entries, rebuilds a whole record by walking
  from the keyframe before it (`full`), fetches gaps for `h` and the timeline, and keeps each
  local's values at the stops for the history column.

## Presets and settings (config keys, `:set`)

| key | full (default) | balanced | lean |
|---|---|---|---|
| `debug_history` (bytes) | 200 MB | 20 MB | 5 MB |
| `debug_events` | 2 000 000 | 200 000 | 50 000 |
| `debug_thinning` | off | on | aggressive |
| deltas | off (every entry a keyframe) | on, keyframe every 100 | watched names and loops only |
| `debug_repr_chars` | 2000 | 80 | 40 |
| `debug_strip_items` | 500 | 40 | 20 |
| `debug_grid_size` | 50 | 20 | 10 |
| `debug_stats` | cpu | watched | watched |

`debug_detail` picks the preset; each `debug_<limit>` overrides it (`resolve`). gpython sends
the keys at kernel start and on `:set` (`_gp_configure`); a session reads them when it starts.
`:set debug_<key>?` shows the value and whether it came from the preset or an override. When
the full preset's cap is hit the footer says "history full (200 MB): oldest steps dropped ·
:set debug_detail=balanced for long runs".

## Heavy values

numpy, pandas, torch, jax and friends are told apart by the type's module; nothing is imported.
A summary is type, shape, dtype and device, read from metadata. Stats (mean, min, max, NaN
count) only for watched names, or with `debug_stats=cpu` for data on the CPU of at most 100 000
elements; a failing stat is skipped. Strips and grids over arrays only on the CPU. A repr is
capped in length; an object whose `len()` is over 100 000 is shown by its type and length; a
type whose repr once took over 10 ms is not repr'd again.

## Measured (in process, 100 000 iterations of a two-line loop, Apple M-series)

| mode | time | kept |
|---|---|---|
| no debugger | 6 ms | – |
| stop if, not recording (sys.monitoring) | 130 ms | – |
| full, recording | 2.8 s | 200 004 steps, 48 MB |
| balanced, recording | 3.8 s | 1 586 steps, 0.4 MB |
| lean, recording | 3.0 s | 1 922 steps, 0.4 MB |
| no debugger, after those sessions | 6 ms | – |

## Follow-ups

- `debug_detail = auto`: full when the cell's loops are small (known totals under ~10 000
  iterations), balanced otherwise.
- Recording during `s` into library code records nothing (only cells' frames).
- Breakpoints by line (`b 12`) are pdb's, typed with `e`; they are not drawn in the editor.
