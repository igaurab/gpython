# Concurrent cells — implementation plan

> For agentic workers: use superpowers:subagent-driven-development or superpowers:executing-plans,
> task by task. Steps are checkboxes.

**Goal:** `:set concurrent` runs independent cells of a run on threads in the one kernel.
**Architecture:** see the table in the spec. `effects.py` (analysis) → `schedule.py` (waves) →
`runs`/`pane` (orchestration) → `Kernel.run_batch` (transport) → `kernel_exec.py` in the kernel
(`CellExecutor`, `OutputRouter`, `WaveLimits`, `fingerprint`), stdlib only, loaded like `kernel_side`.
**Spec:** `docs/plans/14-concurrent-cells.md`. Branch `feature/concurrent-cells`.

## Global constraints

- No comments in code (CLAUDE.md). `uv run python3`, `uv run pytest -q`.
- Static tables in `src/gpython/effects.json`, loaded on first use (like `packages.json`).
- Nothing imported or computed when `concurrent` is off; `effects` imported inside the function
  that first needs it.
- Before touching `app.py` / `pane.py`: `uv run python3 scripts/bench.py` twice, keep numbers;
  again at the end. `first_paint`, `keys_30_net`, `moves_30_proc`, `loaded_60` must not regress.
- Wrong "dependent" is acceptable; wrong "independent" is a bug. Every doubt is a write.

## Review focus

- A function defined in one cell that mutates a global (`def f(): cache.append(1)`), called in
  another, must conflict with readers of `cache` (Task 1 test `test_function_mutating_global`).
- `b = a` in one cell, `b.append(1)` in another: the second writes `a` (Task 1 `test_alias_across_cells`).
- Module calls (`requests.get(url)`) must not write the module name, or IO cells never parallelise
  (Task 1 `test_module_call_is_not_a_write`).
- Interrupt (`r i`) during a wave stops every thread and leaves no cell "running" (Task 3).
- A failing cell in a wave: siblings finish, its descendants are skipped (Task 4).

---

### Task 1: Read/write sets (`effects.py`)

**Files:** create `src/gpython/effects.py`, `src/gpython/effects.json`; test `tests/test_effects.py`.

**Produces:**
- `@dataclass(frozen=True) class Access: reads: frozenset[str]; writes: frozenset[str]; exclusive: str`
  (`exclusive` is the reason, `""` when the cell may share a wave).
- `def accesses(cells: dict[str, str]) -> dict[str, Access]` — whole notebook, display order.
- `def conflict(a: Access, b: Access) -> bool` — Bernstein, or either exclusive.

Rules (per cell, top level; function bodies analysed when defined, applied when called):
- writes ⊇ defs, `del` names, attribute/subscript/aug-assign base names (`graph.mutations` targets).
- `x.m(...)`, `x` not an imported module: read `x` if `m` ∈ `pure_methods`, else write `x`.
- `f(args)`: builtin in `pure_builtins` → read args; notebook function F → F's reads/writes + write
  args; module function `mod.a.b(...)` → write args unless `mod.a.b` matches `pure_functions`, plus
  the effect of the longest prefix in `effect_calls`; anything else → write args. `out=` kwarg → write it.
- Effect names: `$fs`, `$random`, `$plt`, `$cwd`, `$env`, `$sys`. `open(p, "w"|"a"|"x"|"+")` writes
  `$fs`, other `open` reads it.
- Aliases `b = a`, `b = a.x`, `b = a[i]`: notebook-wide closure; a write to one writes all.
- exclusive: syntax error, magic or `!` line, top-level `await`, `exec`/`eval`/`globals`/`locals`/
  `vars`/`input`/`breakpoint`, `from m import *`, `setattr`/`delattr` on a module, calling a function
  whose body uses `global`/`nonlocal` for a name it does not also list in writes.
- `effects.json` keys: `pure_builtins`, `pure_methods`, `pure_functions` (glob patterns, e.g.
  `numpy.*` minus `numpy.random.*`), `effect_calls` (`{"random": "$random", "numpy.random.seed":
  "$random", "matplotlib.pyplot": "$plt", "os.chdir": "$cwd", "os.environ": "$env", "os": "$fs",
  "shutil": "$fs", "pathlib.Path.write_text": "$fs", "sys": "$sys"}`).

- [ ] Write tests (`accesses` on small notebooks; `conflict` between named cells):
  - `test_independent_reads_share` — `a = 1` / `print(a)` / `print(a + 1)`: cells 2, 3 no conflict.
  - `test_mutating_method_conflicts` — `xs.append(1)` vs `print(xs)`: conflict; `xs.copy()` vs `print(xs)`: none.
  - `test_subscript_write_conflicts` — `d["k"] = 1` vs `len(d)`: conflict.
  - `test_module_call_is_not_a_write` — `import requests` / `r1 = requests.get(u)` / `r2 = requests.get(u)`: no conflict.
  - `test_effect_names` — `random.seed(1)` vs `random.random()`: conflict; `plt.plot(x)` vs `plt.plot(y)`: conflict.
  - `test_function_mutating_global` — `cache = []` / `def f(): cache.append(1)` / `f()` / `print(cache)`: f() conflicts with print.
  - `test_alias_across_cells` — `b = a` / `b.append(1)` / `print(a)`: conflict.
  - `test_open_write_vs_read` — `open("x","w")` vs `open("y")`: conflict on `$fs`; two reads: none.
  - `test_exclusive_reasons` — `%time 1`, `await x`, `exec("1")`, `input()`, `breakpoint()`,
    `from os import *`: each `exclusive != ""`.
  - `test_unknown_call_writes_args` — `g(df)` with unknown `g` vs `print(df)`: conflict.
- [ ] Run `uv run pytest tests/test_effects.py -q -n 0` → fails (no module).
- [ ] Implement; memoise per-cell facts by source (`lru_cache`, as `graph._analyzed`).
- [ ] Tests pass; commit `effects: conservative read/write sets per cell`.

### Task 2: Waves

**Files:** create `src/gpython/schedule.py`; test `tests/test_schedule.py`.

**Consumes:** `effects.Access`, `effects.conflict`.
**Produces:** `schedule.waves(order: list[str], access: dict[str, Access], parents: dict[str, set[str]], cap: int) -> list[list[str]]`

Algorithm: for each cell c in `order` (already topological), `w(c) = 1 + max(w(p))` over earlier
cells p that are parents of c or `conflict`; exclusive cells conflict with all, so sit alone. If wave
`w(c)` holds `cap` cells, use the next with room. Cells inside a wave keep `order`.

- [ ] Tests: `test_waves_parallel_siblings` (`a=1`, `b=a`, `c=a` → `[[1],[2,3]]`),
  `test_waves_exclusive_alone`, `test_waves_cap` (5 independent, cap 2 → sizes 2,2,1),
  `test_waves_conflict_keeps_source_order` (`xs.append(1)` then `print(xs)` → separate, in order).
- [ ] Fail → implement → pass → commit `schedule: waves`.

### Task 3: Kernel runs a wave on threads

**Files:** create `src/gpython/kernel_exec.py` (loaded as `_gpython_exec` by `kernel._helpers`, like
`kernel_side`); modify `src/gpython/kernel.py` (`_gp_run` becomes a call to
`_gpython_exec.CellExecutor(shell, globals(), _gp_stdout, _gp_stderr).run(waves)`, `Kernel.run_batch`);
test `tests/test_kernel_exec.py` (in process, fake streams, like `tests/test_debugger.py`) and
`tests/test_concurrent.py` (real kernel).

`kernel_exec.py`: `class CellExecutor` (`async run(waves) -> None`; one-cell waves = today's `_gp_run`
body moved verbatim), `class OutputRouter` (context manager: wraps `sys.stdout`/`stderr`/
`display_pub.publish` for a wave, emits `switch`), `class WaveLimits` (context manager: pool of
`len(wave)` threads, BLAS limits, shut down on exit), `def fingerprint(namespace, names) -> dict[str, str]` (Task 5).

**Consumes:** nothing. **Produces:**
`Kernel.run_batch(waves: list[list[tuple[str, str, list[str]]]], on_event, on_output, on_input=None) -> bool`
(each sequential run is waves of one; returns False when any begun cell did not end).

- One-cell wave: today's path unchanged.
- Multi-cell wave, in `_gp_run`: pop names; `shell.compile.cache(code)` for the filename; exec on a
  `ThreadPoolExecutor(max_workers=len(wave))` created for the run's first multi-cell wave and shut
  down when `_gp_run` returns (no idle threads between runs); last expression evaluated and shown
  with `display`; errors via `shell.showtraceback()`.
- BLAS: if `"numpy" in sys.modules`, `threadpoolctl.threadpool_limits(max(1, cores // len(wave)))`
  for the wave (imported lazily; skipped if `threadpoolctl` is missing).
- Output routing: during a wave `sys.stdout`/`sys.stderr` and `shell.display_pub.publish` are
  wrapped; a `ContextVar` holds the worker's cell id; under one lock a writer whose cid differs from
  the last writer first emits `mark(cid, "switch")` and flushes. `begin`/`end` marks also take the lock.
- `run_batch`: `switch` sets the current cell and is not forwarded; `clean` = no begun-but-not-ended cell.
- Interrupt: the main thread waits with `concurrent.futures.wait` under `signal.default_int_handler`;
  on `KeyboardInterrupt`, `ctypes.pythonapi.PyThreadState_SetAsyncExc(ident, KeyboardInterrupt)` per
  live worker, wait, mark unfinished cells `end ok=False`.
- After a wave with a failed cell, stop (as today after a failed cell).

- [ ] Tests (real kernel, as `tests/test_kernel.py`):
  - `test_wave_runs_concurrently` — 4 cells `time.sleep(0.5)` in one wave: elapsed < 1.2 s, all `ok`.
  - `test_wave_outputs_routed` — 3 cells printing their name 50 times with `sleep(0.001)`: each cell's
    collected stream is exactly its own 50 lines.
  - `test_wave_display_routed` — `display(n)` and a last expression per cell land in that cell.
  - `test_wave_error_isolated` — `1/0` in one of three: that one `ok=False` with an `error` output; others `ok`.
  - `test_wave_interrupt` — 2 cells `while True: pass`; `k.interrupt()` after 0.3 s: `run_batch`
    returns, both `end ok=False`, kernel still runs `1+1`.
  - `test_sequential_unchanged` — waves of one behave exactly as before (existing kernel tests pass).
  - `test_no_threads_left` — after a run with a wave, `threading.active_count()` in the kernel equals its value before.
  - `test_blas_limits_restored` — with numpy loaded, `threadpool_info()` before and after a wave match.
- [ ] Fail → implement → pass; full `uv run pytest -q` green → commit `kernel: run a wave on threads`.

### Task 4: Pane schedules waves; `:set concurrent`

**Files:** modify `src/gpython/runs.py` (`BatchRun.items`), `src/gpython/pane.py` (`run_cells`),
`src/gpython/app.py` (`_ex_set`, set option lists), `README.md` (run mode section, `:set` list);
test `tests/test_concurrent.py`.

**Consumes:** `effects.accesses`, `schedule.waves`, `Kernel.run_batch(waves, …)`.
**Produces:** `GPython.concurrent: bool` from config key `"concurrent"` (default `False`);
`GPython.concurrency: int` from `"concurrency"` (default `min(32, os.cpu_count() + 4)`, `1` = sequential),
passed as `cap` to `schedule.waves`;
`BatchRun.items(batch) -> list[list[tuple]]` (waves when `app.concurrent`, else one cell per wave).

- Orthogonal to lazy/reactive: those decide which cells run, this decides how.
- `:set concurrency=N` saved likewise.
- Interrupt during a wave: status shows "interrupting…" until every thread has stopped.
- `:set concurrent` / `:set noconcurrent`, saved with `config.save`; notify
  "Concurrent: independent cells run at the same time. :set noconcurrent to run one by one."
- The debug cell stays alone (already in `next_batch`).

- [ ] Bench twice, save numbers to the scratchpad.
- [ ] Tests (UI, as `tests/test_run_mode.py`):
  - `test_concurrent_off_by_default`.
  - `test_concurrent_run_all` — setup + 4 `sleep(0.5)` cells: run-all < 1.5 s with `:set concurrent`, > 2 s without.
  - `test_failed_cell_skips_descendants` — wave `[1/0, ok]`, a child of the failing cell is `skipped`, the other's child runs.
  - `test_setting_saved` — `config.load()["concurrent"] is True` after `:set concurrent`.
- [ ] Fail → implement → pass; full suite green; bench again, compare → commit `concurrent run mode`.

### Task 5: Verify after a wave

**Files:** modify `src/gpython/kernel_exec.py` (`fingerprint`, used by `CellExecutor`), `src/gpython/runs.py`; test `tests/test_concurrent.py`.

**Produces:** `kernel_exec.fingerprint(namespace, names) -> dict[str, str]`: `id:type:len`, plus `crc32` of
the buffer for objects exposing `nbytes` ≤ 16 MB and of `ids` of items for list/dict/set ≤ 10 000.
`_gp_run` fingerprints the wave's shared reads (reads of one cell not written by any cell of the wave)
before and after; differences emit `mark(None, "race", names=[…], cells=[…])`; `BatchRun` marks those
cells stale and notifies "Concurrent write to x detected: these cells were marked stale". Setting
`concurrent_verify`, default on.

- [ ] Test `test_race_detected` — force a wave through `run_batch` with two cells the analysis would
  reject (`xs.append(1)` via `getattr(xs, "app"+"end")(1)` and `print(len(xs))`): race event with `xs`.
- [ ] Fail → implement → pass → commit `concurrent: verify shared reads after a wave`.

---

## Decisions (rules: correctness first, opt-in and free when off, YAGNI, stdlib defaults, measure)

- Threads in the one kernel; processes would split the namespace. Revisit with free-threaded Python.
- Blocking C calls finish before the interrupt lands (Jupyter has the same limit); `r k` restarts.
- Cap `min(32, cpu_count + 4)` (the `ThreadPoolExecutor` default), `:set concurrency=N`.
- `concurrent_verify` on; if it costs > 5 % of a 1000-cell run, make it cheaper, not off.
- Execution counts in begin order (already what `BatchRun.begin` does); kernel history skipped.
- Later: per-cell `# %% [exclusive]`, status-bar wave counter, async waves via `asyncio.gather`.
- Paper evaluation: its own plan after Task 5.

## Unresolved questions

- None blocking; revisit the cap and verify cost after the Task 4/5 benchmarks.
