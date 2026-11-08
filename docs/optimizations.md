# What makes gpython fast, against JupyterLab and marimo

How the three tools are built, what gpython does that the other two do not, what it
learned from them, and the numbers at each step. Measurements come from
`scripts/warmbench.py`, `scripts/kernelbench.py`, `scripts/membench.py`, `scripts/bench.py`
and `scripts/ptybench.py` on a MacBook (macOS, Python 3.13, ipykernel 7.4, marimo 0.25.2,
JupyterLab 4). The first round is in issue #10 and PR #11; the rounds after it are
2026-11-08, commits `286c060`, `72cb7ea`, `d3adc76` and the virtual cell list.

## The three shapes

```
JupyterLab:  browser ──WebSocket──> jupyter_server ──ZMQ──> ipykernel
marimo:      browser ──WebSocket──> marimo server  ──pipe──> marimo kernel process
gpy:         terminal ──> gpy UI process ──ZMQ──> ipykernel        (no server, no browser)
```

gpython is one Python process that paints the terminal and talks ZMQ to a stock
ipykernel. There is no web server, no browser, no JavaScript bundle. The UI is Textual,
a Python terminal framework, so every cell is a Python widget and every repaint is Python
code. That is the whole trade: start-up and memory are cheap, but the UI's own work per
frame is paid in Python, and it has to be kept small by design.

## The numbers, round by round

From launch to the first and the last of N trivial cells having run (`warmbench`, mean of
3 runs; "ready" is gpy's first frame, or the server answering HTTP; the browser is not
counted for the other two). The rounds are:

- **round 1**: PR #11, the first measurement;
- **round 2**: one kernel request per run and paced repaints (`286c060`);
- **round 3**: kernel spawned before the UI, one widget per output, virtual cell list
  (`72cb7ea`, `d3adc76` and after).

| gpy cells | ready | first cell r1 → r2 → r3 | all cells r1 → r2 → r3 | memory r1 → r2 → r3 |
|---:|---:|---|---|---|
| 1 | 0.25 s | 0.75 → 0.77 → **0.59 s** | 0.75 → 0.77 → **0.59 s** | 122 → 127 → **127 MB** |
| 10 | 0.26 s | 0.78 → 0.81 → **0.62 s** | 0.89 → 0.82 → **0.63 s** | 129 → 131 → **131 MB** |
| 100 | 0.27 s | 0.82 → 0.82 → **0.64 s** | 2.23 → 0.92 → **0.74 s** | 149 → 145 → **144 MB** |
| 500 | 0.28 s | 1.17 → 1.01 → **0.72 s** | 25.8 → 1.51 → **1.24 s** | 245 → 203 → **164 MB** |

The other two, same benchmark, same day as round 3 (round 1 in brackets):

| tool | cells | ready | first cell | all cells | memory |
|---|---:|---:|---:|---:|---:|
| JupyterLab | 1 | 1.16 s (0.99) | 1.44 s (1.29) | 1.44 s (1.29) | 150 MB (149) |
| marimo | 1 | 0.47 s (0.44) | 0.79 s (0.75) | 0.79 s (0.75) | 152 MB (145) |
| JupyterLab | 10 | 1.15 s (0.99) | 1.39 s (1.23) | 1.42 s (1.25) | 151 MB (149) |
| marimo | 10 | 0.47 s (0.42) | 0.79 s (0.74) | 0.80 s (0.77) | 152 MB (145) |
| JupyterLab | 100 | 1.12 s (1.00) | 1.40 s (1.23) | 1.67 s (1.47) | 153 MB (152) |
| marimo | 100 | 0.48 s (0.44) | 0.89 s (0.86) | 0.95 s (1.12) | 157 MB (154) |
| JupyterLab | 500 | 1.15 s (1.00) | 1.40 s (1.24) | 2.79 s (2.42) | 169 MB (166) |
| marimo | 500 | 0.53 s (0.49) | 1.46 s (1.43) | 1.72 s (2.77) | 182 MB (183) |

After round 3 gpython is first on every row: on screen 2× sooner than marimo and 4× sooner
than JupyterLab, the first result 0.2 s sooner than marimo at every size, 500 cells run
0.5 s sooner than marimo and 1.5 s sooner than JupyterLab, with the least memory at every
size even before the other two add a browser tab.

Headless (`scripts/bench.py`, 2 ms tick), what the UI itself costs, round 1 → round 3:

| cells | loaded | 30 keystrokes (net CPU) | 30 cell moves (CPU) | first paint |
|---:|---|---|---|---|
| 60 | 0.24 → 0.24 s | 0.07 → 0.07 s | 0.30 → 0.29 s | 0.06 → 0.06 s |
| 500 | 1.19 → 0.31 s | 0.08 → 0.10 s | 1.39 → 0.34 s | 0.07 → 0.06 s |
| 1000 | 1.37 → 0.37 s | 0.27 → 0.11 s | 2.91 → 0.34 s | 0.07 → 0.07 s |

A real pseudo-terminal (`scripts/ptybench.py`): first frame 0.25–0.28 s before and after
every round; `j` paints in 2–5 ms once warm.

## What gpython does that the others do not

**Nothing runs before the first frame.** The app composes the first screen of cells and
paints it, then mounts the remaining cells in batches of ten and reads stored outputs, all
through `GPython.after_first_frame`. Libraries are imported where they are first used,
never at module level, so `import gpython.app` costs about 60 ms. That is why "ready" is
flat at 0.27 s from 1 to 500 cells, while JupyterLab boots a server for a second and
marimo for half a second before either sends a page.

**The kernel is spawned before the UI exists** (round 3, `src/gpython/prespawn.py`).
Until round 2 the kernel started after the first frame, so its ~0.5 s boot followed the
0.25 s of imports and first paint, and the first result landed at 0.77 s. Now `main()`
spawns ipykernel a few milliseconds after start, with no jupyter_client or textual import
(a connection file with free ports and a key, one `Popen`, about 3 ms), and the pane's
`Kernel.start` adopts the process through a jupyter_client `LocalProvisioner` when its env
and file match, so interrupt, restart and shutdown work as before; an unclaimed spawn is
killed at exit. First cell: 0.77 → 0.59 s at 1 cell, 0.82 → 0.64 s at 100, 1.01 → 0.72 s
at 500; the first frame did not move (0.27 s in a pty). marimo and JupyterLab start their
kernel with the server, which is why their first cell used to tie or beat gpython's.

**One process for the UI.** JupyterLab and marimo each need a server process plus a
browser tab that the tables above do not even count. gpython's UI process is 60–70 MB
with Textual.

**Painting is kept off the hot path.** Textual re-arranges every widget on the screen
whenever any widget changes size (`Compositor.reflow` walks the full tree, not only the
visible part). gpython therefore avoids anything that changes layout on a keystroke or a
cell move: the header is painted into the cell's top border row instead of being a
widget, status changes are CSS classes applied without a restyle, the footer is a static
widget. A keystroke costs about 2 ms of CPU and a cell move 6–10 ms (the budget is 10 ms
per keystroke; `scripts/bench.py` enforces it).

**A whole run is one kernel request** (round 2, `Kernel.run_batch`, `src/gpython/runs.py`).
The run loop used to send two requests per cell, one to drop the names the cell redefines
and one to run it, and wait for each reply: ipykernel costs about 2.4 ms per request
whatever the code, so 500 cells paid 2.5 s before any UI work. Now the pane sends the cells
of a run as one list to a kernel-side helper that runs them in order with IPython's own
`run_cell_async`, drops the names first, and brackets each cell with a marker line written
into the kernel's stdout stream, so outputs are attributed to the right cell. The marker
goes into the stream rather than out as a message of its own because publishing a message
makes ipykernel flush stdout first, a synchronous hand-off to its I/O thread that costs
0.5 ms, four times the cell itself (0.12 ms); a line in the stream costs nothing and is
ordered with the cell's output by construction. 500 trivial cells: 2.5 s → 0.48 s with
message markers → 0.07 s with stream markers. A failing cell ends the batch; the pane marks
its dependants as skipped and sends the rest as a new batch, so run-all keeps the
semantics it had, and the helper restores Python's normal interrupt handler around each
blocking cell, since ipykernel swaps it for "cancel the coroutine" during an async
request. This is what marimo's kernel does natively (its runner executes the whole graph
in-process, `marimo/_runtime/runner/cell_runner.py`), and what JupyterLab approximates
by queueing every request at once on the shell channel.

**Paints are paced while a run streams results** (round 2, `src/gpython/pacing.py`).
Every cell that finishes changes the layout, Textual repaints at up to 60 fps, and each
repaint at 500 cells re-arranged 1,500 widgets (70 ms headless, more in a real terminal).
Measured before the fix: 500 cells took 14.5 s headless with 144 reflows and 4.8 s with
19. The pacer holds repaints during a run and releases one at a time; the gap before the
next is four times what the last paint took, between 30 and 400 ms, so a small notebook
still paints almost every frame. A key or a click releases the next paint at once, and the
hold only ever starts right after a paint, so nothing the user does is delayed and
callbacks that wait for a refresh still see current regions. marimo gets the same effect
for free from the browser, whose layout engine only re-lays-out the subtree that changed,
and from React memoising each cell; its kernel also batches stdout into 10 ms windows
(`marimo/_messaging/console_output_worker.py`), which gpython's output area does too
(`FLUSH_INTERVAL`).

**One widget per run of text output** (round 3, `OutputArea` and `TextBlocks` in
`src/gpython/outputs.py`). A memory profile put every Textual widget at 35–40 kB and
0.3–0.5 ms to mount, and a cell that printed a line and echoed a result mounted three:
the output container and one `Static` per block. Consecutive text blocks (streams,
results, tracebacks, hints) now share one `TextBlocks` widget that paints them joined;
images, markdown and JSON stay widgets of their own, in order. 500 run cells: 123 → 88 kB
each in the UI process.

**Only the cells near the viewport are widgets** (round 3, `docs/plans/12-virtual-cells.md`).
A notebook with more than 100 cells keeps a window of 100 around the viewport mounted;
the rest are `CellState` objects holding the source, cursor, undo history, status, run
numbers and the outputs log, about 1 kB each. The window follows scrolling one batch of
ten at a time under one `batch_update`, jumps (`G`, search, `:N`, run-by-number) move it
first, and runs work on states, so F5 right after opening a 500-cell notebook starts at
once instead of waiting for the mount. Loaded: 1.19 → 0.31 s at 500 cells, 1.37 → 0.37 s
at 1000; a cell move 45 → 11 ms at 500 cells; memory at 500 run cells 203 → 164 MB. Under
100 cells nothing changes, which is what keeps the 60-cell bench identical.

**Outputs are streamed into existing widgets.** Stream text appends to one block with a
flush timer; 2,000 `print` lines cost about 20 ms. Tracebacks and results share the
block; images are one widget each.

## What was measured and not kept

- Pacing the paints during the initial mount: no gain; the cost is widget creation, not
  reflows (the mount profile is flat across Textual's stylesheet apply, reactives and
  document wrapping).
- Starting the run while cells still mount (round 2, before the virtual list): the first
  cell came 0.18 s sooner but the last cell 0.14 s later, because the run's own UI work
  competed with mounting. The virtual cell list made this moot.
- Importing jupyter_client in a thread, mount batches of 20 or 30, a smaller per-cell
  header widget: see `docs/plans/TODO.md` §1 for the earlier rounds.

## What the others still do better, and what is left

- **Idle CPU.** gpython sits at 0.14–0.5 % against JupyterLab's 0.03 % (issue #10); the
  cause has not been found yet.
- **Ex ranges over `%`** (`:%s`, `:g`) see only the mounted window of a notebook over
  100 cells (open question in `docs/plans/12-virtual-cells.md`).
- **A window shift is a ~25 ms hiccup** every eight cell moves in a long notebook, not
  yet tried in a real terminal.
- **Memory floor.** 127 MB with one cell: Python 11 MB, Textual 22, gpython 6,
  jupyter_client 8, the kernel 60. The kernel-client import could move to the kernel-boot
  worker (measured slower for kernel-ready in an earlier round), and IPython 9 tees every
  cell's stdout into `history_manager.outputs` unbounded, which a big print leaves in the
  kernel for good.

## How to measure

```
uv run python3 scripts/bench.py                       # headless: first paint, keys, moves, 60-cell load
uv run python3 scripts/bench.py --cells 500           # the same at 500 cells
uv run python3 scripts/ptybench.py                    # a real launch in a pseudo-terminal
uv run python3 scripts/warmbench.py --tools gpy       # launch → first/last cell, real pty
uv run --with jupyterlab --with marimo python3 scripts/warmbench.py --runs 3
uv run --with jupyterlab --with marimo python3 scripts/membench.py
uv run python3 scripts/kernelbench.py
uv run python3 scripts/memprof.py --who               # where the bytes go, what stays alive
```

Never run a benchmark while the tests run.
