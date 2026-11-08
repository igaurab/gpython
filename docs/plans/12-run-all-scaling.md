# 12 – Run-all scaling

## Outcome (2026-11-08, branch perf/run-all-scaling)
| run-all, end to end (`warmbench.py`, gpy) | 100 cells | 500 cells |
|---|---|---|
| before | 2.23 s | 25.8 s |
| after | **1.48 s** | **4.23 s** |

`bench.py runall_200` went from 2.33 to 1.51 s (CPU 1.77 → 0.89 s). An interleaved A/B shows no change in `first_paint`, `keys_30_net`, `moves_30_proc` or `loaded_60`. Run-all is now linear.

Done:
- **Step 2:** off-screen cells defer their outputs **and** their header and bar paint. A repaint of a widget that isn't visible makes `Compositor.update_widgets` throw away its full map, so the next `region` lookup rebuilds it over every widget. That cost as much as the layout passes did.
- **`_keep_focus_visible`** runs once at the end for deferred cells instead of once per cell (`scroll_visible` was 4 s of the 9 s at 300 cells).
- **Step 4:** `Kernel.send_delete`.

Not needed: steps 3 and 5. Still open: step 6 (memory: 187 MB at 500 cells).

## Problem (measured 2026-11-07, #10)
| run-all, launch to last cell | 100 | 500 cells |
|---|---|---|
| gpy | 2.2 s | **25.8 s** |
| JupyterLab (no browser) | 1.5 s | 2.4 s |
| marimo (no browser) | 1.1 s | 2.8 s |

Headless, `run_cells` only: 25 → 38 → 71 ms per cell at 100 / 250 / 500 cells, so the total is O(n²).

## Cause
- 75 % of the time goes to `Screen._refresh_layout`: a full arrange of every widget in the notebook, about 86 ms per pass at 250 cells. Off-screen cells are included.
- Every cell triggers one: it mounts an `OutputArea` and a `Static` (`await_mount` asks for layout), `outputs._flush` calls `Static.update` (layout by default), and the size change cascades to `Cell` and `VerticalScroll`.
- Proof: holding `app.batch_update()` across the whole run makes it linear at **7.3 ms per cell (500 cells: 35.7 → 3.6 s)**.
- The protocol floor is 2.3 ms per cell (500 sequential executes over raw jupyter_client take 1.17 s). gpy makes two round trips per cell (`kernel.delete` and `execute`).

## Steps
1. **Metric.** Add `runall_100` and `runall_500` to `bench.py`, using a fake kernel that answers at once so the number is the UI's cost alone. Keep a real-kernel run in `warmbench.py`.
2. **Off-screen cells don't touch layout during a run.** `Cell.add_output` still writes `_log`. It mounts or updates the `OutputArea` only when the cell overlaps the viewport. Hidden cells get a `dirty_outputs` flag and are drawn (a) when they scroll into view and (b) all together inside one `batch_update` when the run ends. The on-screen cell keeps streaming live.
3. **Stream flush without layout.** In `outputs._flush`, use `Static.update(..., layout=False)` when the block's line count is unchanged.
4. **One round trip per cell.** Send the `_gp_delete` request without waiting for it (`_send` without `_collect`; the kernel runs requests in order), or skip it when the cell defines nothing it defined before. Expected: about 7 → about 5 ms per cell.
5. **Fallback if step 2 is not enough.** Slice the run into `batch_update` windows of 100 ms or more (one layout per window). The cost: up to 100 ms of paint lag for keys pressed during a run-all.
6. **Later, separate plan.** Build widgets only for visible cells (virtualised list). This makes layout O(visible) everywhere: start-up, memory (245 MB at 500 cells, against 166 MB for JupyterLab) and scrolling.

## Targets
- Run-all of 500 cells in 3 s or less with a real kernel (on par with JupyterLab and marimo). 100 cells in 1 s or less.
- `first_paint`, `keys_30_net`, `moves_30_proc` and `loaded_60` unchanged. Run `bench.py` twice before and once after, per CLAUDE.md.
- Tests: run-all output correct for off-screen cells after scrolling; `:w` saves outputs of cells that were never drawn (`outputs_snapshot` reads `_log`, so this should already hold).

## Unresolved questions
- Is a cell partly on screen "visible"? Use the viewport plus one screen of margin?
- Should the view follow the running cell during run-all (Jupyter doesn't by default)? Following it would put every cell on screen at some point and cancel out step 2.
- Do step 6 now instead of steps 2–5? It's bigger but fixes memory and start-up too.
- Can step 4 change behaviour when a cell fails between the delete and the execute?
