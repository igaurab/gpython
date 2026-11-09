# 14 — Concurrent cells: independent cells run at the same time

Run mode `concurrent` (beside lazy and reactive): a run-all, a stale run or a reactive re-run sends
cells in waves; the cells of one wave share no state and run on threads in the one kernel.

## Architecture

| Layer | Process | Module |
|---|---|---|
| analysis (pure) | gpython | `effects.py`: sources → `Access(reads, writes, exclusive)` |
| policy (pure) | gpython | `schedule.py`: `waves(order, access, parents, cap)` |
| orchestration | gpython | `pane.run_cells`, `runs.BatchRun` |
| transport | gpython | `Kernel.run_batch(waves, …)`, stdout marker protocol + `switch` |
| mechanism | kernel | `kernel_exec.py` as `_gpython_exec` (stdlib only, like `kernel_side`): `CellExecutor`, `OutputRouter`, `WaveLimits`, `fingerprint`; `_gp_run` delegates to it |

## Rule

Bernstein's conditions (1966). Cells A, B may share a wave iff
R(A) ∩ W(B) = W(A) ∩ R(B) = W(A) ∩ W(B) = ∅. Reads of shared objects are free; any write
(rebind, in-place change, external effect) serialises. Wrong "dependent" costs speed; wrong
"independent" is a race, so every doubt counts as a write.

## What exists

- `graph.analyze`: defs / refs per cell (symtable). `graph.mutations`: top-level `x[..] =`, `x.a =`,
  `del x[..]`, `x.<_MUTATORS>()`; `build` already adds mutator → later-reader edges.
- `Kernel.run_batch` / `runs.BatchRun`: cells go in batches, one `execute_request` each, events per cell.
- The debugger has none of this: it records values at stops, not read/write sets. Reusable from it:
  `kernel_side` cheap summaries (fingerprints) and the `sys.monitoring` plumbing.

## Write set W(cell), conservative (`graph.effects`)

- defs (assign, import, `del`, `global`), plus `mutations` widened:
  - any method call on a shared name unless the method is on a pure allowlist (`copy`, `head`, `sum`,
    `mean`, `keys`, `items`, `get`, `str.*`, …), in place of the `_MUTATORS` denylist;
  - a shared name passed to a call not on a pure-function list (`len`, `print`, `sum`, `sorted`,
    `np.mean`, …);
  - aliases: `b = a` / `b = a[0]` makes later writes to `b` writes to `a`.
- Effect pseudo-names, written when touched: `$fs` (`open(..., "w"|"a")`, `Path.write_*`, `os`,
  `shutil`), `$random`, `$plt`, `$cwd`, `$env`, `$sys`, `$stdin`. Reading files is `R($fs)`.
- A cell is **exclusive** (runs alone) if: calls a function defined in the notebook that writes a
  global, uses `global`/`exec`/`eval`/`globals()`/magics/`!shell`, `input()`, `breakpoint()`, has a
  syntax error, or anything unclassified. Exclusive-cell count is a reported metric.
- Lives in `graph.py`, memoised like `_analyzed`; no cost unless the mode is on.

## Scheduler (`runs.py`)

- Waves = topological levels of the run's subgraph, filtered pairwise by the rule; exclusive cells
  form a wave of one. Wave cap = `concurrency` setting (default `os.cpu_count()`).
- Failure in a wave: siblings finish, descendants skip (as today).

## Kernel side (`kernel_side.run_wave`)

- One `execute_request` per wave; `ThreadPoolExecutor` runs each cell's compiled code in the shared
  namespace; top-level `await` cells run on one event loop with `asyncio.gather`.
- Output routing: `sys.stdout`/`stderr`/`display` replaced once by a proxy that writes to the
  stream of the cell in a `contextvars.ContextVar`; events carry the cell id (as `cellId` does now).
- Interrupt cancels the wave (`KeyboardInterrupt` to the main thread; worker threads checked via
  `sys.monitoring` or left to finish, see questions).
- Optional verify: fingerprint (id, len, buffer hash for arrays) of every shared name read in the
  wave, before and after; a change marks the wave's cells stale with a "concurrent write detected"
  note and makes them exclusive next time.

## UI

- Running cells of a wave show their bars together (existing per-cell status, no new widgets).
- Later: status bar `wave 3/7 · 4 running`; per-cell override `# %% [exclusive]`.
- Debugger: arming it makes the run sequential.

## Evaluation (paper)

- Workbench problems as N independent cells after one setup cell: N1/N2 (network), D1/D2 (disk)
  expect ~min(N, cap)× speedup; C1/C2 (BLAS/FFT, GIL released) partial; C3 (pure Python) none on
  CPython, measured again on free-threaded 3.13t/3.14t.
- Analysis precision on real notebooks: parallelism in principle (true read/write sets from a
  traced run) vs what the conservative analysis allowed; races found by verify (target 0).
- Overhead with the mode on and nothing parallel: must be within noise of lazy.
- Bench rules of CLAUDE.md apply (`first_paint`, `keys_30_net`, `moves_30_proc`, `loaded_60`).

## Prior art (related work)

- Vizier (Deo, Glavic, Kennedy, TaPP 2022): static + runtime-refined dataflow, parallel cells,
  separate processes and artifacts per shared object. Closest prior work.
- marimo issue #1103 "Parallel Execution": proposed, not shipped as far as found.
- ipyparallel DAG dependencies, IPython background jobs, Datalore / Databricks requests.

## Decided

- Threads in the one kernel. `:set concurrent` is orthogonal to lazy / reactive.
- Pure lists and effect calls in `effects.json`.
- Interrupt: async `KeyboardInterrupt` into each worker thread.
- Top-level `await` and magic cells are exclusive in v1.
- Outputs live per cell, so finish order does not affect the file.
- Tasks: `14-concurrent-cells-tasks.md`.

## Unresolved questions

- See the tasks file.
