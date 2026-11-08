# What makes gpython fast, against JupyterLab and marimo

How the three tools are built, what gpython does that the other two do not, what it
learned from them, and the numbers. The measurements are from `scripts/warmbench.py`,
`scripts/kernelbench.py`, `scripts/membench.py` and `scripts/bench.py` on a MacBook
(macOS, Python 3.13, ipykernel 7.4, marimo 0.25.2, JupyterLab 4); see issue #10 and
PR #11 for the first round and the headless profiles behind this page.

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

## Numbers (from launch, mean of 3 runs; browser not counted for the other two)

| tool | cells | ready | first cell | all cells | memory |
|---|---:|---:|---:|---:|---:|
| gpy | 1 | **0.25 s** | 0.77 s | 0.77 s | **127 MB** |
| JupyterLab | 1 | 1.12 s | 1.35 s | 1.35 s | 150 MB |
| marimo | 1 | 0.44 s | 0.76 s | 0.76 s | 151 MB |
| gpy | 10 | **0.26 s** | 0.81 s | 0.82 s | **131 MB** |
| JupyterLab | 10 | 1.11 s | 1.35 s | 1.37 s | 150 MB |
| marimo | 10 | 0.45 s | 0.78 s | 0.79 s | 152 MB |
| gpy | 100 | **0.25 s** | 0.82 s | **0.92 s** | **145 MB** |
| JupyterLab | 100 | 1.12 s | 1.36 s | 1.64 s | 152 MB |
| marimo | 100 | 0.46 s | 0.87 s | 0.92 s | 157 MB |
| gpy | 500 | **0.27 s** | 0.88 s | **1.40 s** | 168 MB |
| JupyterLab | 500 | 1.12 s | 1.40 s | 2.81 s | 169 MB |
| marimo | 500 | 0.52 s | 1.44 s | 1.69 s | 182 MB |

"ready" is gpy's first frame, or the server answering HTTP. "all cells" is when the
last of N trivial cells has run, measured by the kernel itself. Before the batched run
loop below, gpy's 500-cell row was 25.8 s and its 100-cell row 2.2 s.

## What gpython does that the others do not

**Nothing runs before the first frame.** The app composes the first screen of cells and
paints it, then starts the kernel, mounts the remaining cells in batches of ten and reads
stored outputs, all through `GPython.after_first_frame`. Libraries are imported where they
are first used, never at module level, so `import gpython.app` costs about 60 ms. That is
why "ready" is flat at 0.3 s from 1 to 500 cells, while JupyterLab boots a server for a
second and marimo for half a second before either sends a page.

**One process for the UI.** JupyterLab and marimo each need a server process plus a
browser tab that the tables above do not even count. gpython's UI process is 60–70 MB
with Textual, so the whole thing with a kernel is the smallest of the three up to about
100 cells.

**Painting is kept off the hot path.** Textual re-arranges every widget on the screen
whenever any widget changes size (`Compositor.reflow` walks the full tree, not only the
visible part). gpython therefore avoids anything that changes layout on a keystroke or a
cell move: the header is painted into the cell's top border row instead of being a
widget, status changes are CSS classes applied without a restyle, the footer is a static
widget, the per-cell note under a collapsed output is the only widget that comes and
goes. A keystroke costs about 2 ms of CPU and a cell move 6–10 ms at 60 cells (the
budget is 10 ms per keystroke, `scripts/bench.py` enforces it).

**A whole run is one kernel request** (`Kernel.run_batch`, `src/gpython/runs.py`). The
run loop used to send two requests per cell, one to drop the names the cell redefines and
one to run it, and wait for each reply: ipykernel costs about 2.4 ms per request whatever
the code, so 500 cells paid 2.5 s before any UI work. Now the pane sends the cells of a
run as one list to a kernel-side helper that runs them in order with IPython's own
`run_cell_async`, drops the names first, and brackets each cell with a marker line
written into the kernel's stdout stream, so outputs are attributed to the right cell.
The marker goes into the stream rather than out as a message of its own because
publishing a message makes ipykernel flush stdout first, a synchronous hand-off to its
I/O thread that costs 0.5 ms, four times the cell itself (0.12 ms); a line in the stream
costs nothing and is ordered with the cell's output by construction. 500 trivial cells
take 0.07 s in the kernel. A failing cell ends the batch; the pane marks its dependants
as skipped and sends the rest as a new batch, so run-all keeps the semantics it had, and
the helper restores Python's normal interrupt handler around each blocking cell, since
ipykernel swaps it for "cancel the coroutine" during an async request. This is what
marimo's kernel does natively (its runner executes the whole graph in-process,
`marimo/_runtime/runner/cell_runner.py`), and what JupyterLab approximates by queueing
every request at once on the shell channel.

**Paints are paced while a run streams results** (`src/gpython/pacing.py`). Every cell
that finishes changes the layout, Textual repaints at up to 60 fps, and each repaint at
500 cells re-arranges 1,500 widgets (70 ms headless, more in a real terminal). Measured:
500 cells took 14.5 s headless with 144 reflows and 4.8 s with 19. The pacer holds
repaints during a run and releases one at a time; the gap before the next is four
times what the last paint took, between 30 and 400 ms, so a small notebook still paints
almost every frame. A key or a click releases the next paint at once, and the hold only
ever starts right after a paint, so nothing the user does is delayed and callbacks that
wait for a refresh still see current regions. marimo gets the same effect for free from
the browser, whose layout engine only re-lays-out the subtree that changed, and from
React memoising each cell; its kernel also batches stdout into 10 ms windows
(`marimo/_messaging/console_output_worker.py`), which gpython's output area does too
(`FLUSH_INTERVAL`).

**Outputs are streamed into existing widgets.** Stream text appends to one `Static` per
stream with a flush timer; 2,000 `print` lines cost about 40 ms. Tracebacks, results and
images are one widget each.

**Cells are mounted in batches after the first frame**, ten at a time under
`batch_update`, so a 500-cell notebook is interactive at 0.3 s and fully mounted at
about 1 s.

## What the others do better, and what is planned

**Memory per cell.** At 500 cells gpython used 203 MB against 169 (JupyterLab) and 182
(marimo), both without their browser tab. The kernel is not the cause (6 kB per cell).
Each mounted cell is a `Cell` widget with a `TextArea` editor (about 73 kB) and, once it
has run, an output container with one `Static` per block (about 35–40 kB per widget):
Textual's per-widget message queues, style objects, layout caches and render caches. Two
fixes: fold the text blocks of an output into one `Static` (measured −30 MB at 500
cells, planned) and the virtual cell list (`docs/plans/12-virtual-cells.md`, done): a
notebook over 100 cells keeps only a window of 100 cells around the viewport as widgets,
the rest are plain `CellState` objects, which is 168 MB at 500 cells and makes keystroke
and move cost independent of the notebook's size (a cell move costs 9–10 ms at 500 cells
against 6 ms at 60, with the window shifting every eight cells; it was 45 ms).

**First cell.** gpython starts the kernel after the first frame, so the first result
lands about 0.5 s after the screen is up; marimo and JupyterLab start theirs with the
server. Starting the kernel alongside the first frame is on the list in #10.

**Load time.** Mounting 500 cells took about a second (1 ms of CSS and layout per
widget, three widgets per cell). With the virtual cell list a 500-cell notebook is loaded
in 0.31 s and a 1000-cell one in 0.37 s (`loaded_N` in `scripts/bench.py`), since only the
first hundred cells are mounted; pacing the paints during the mount was tried and gained
nothing, because the cost is the widgets, not the reflows.

## How to measure

```
uv run python3 scripts/bench.py                       # headless: first paint, keys, moves, 60-cell load
uv run python3 scripts/bench.py --cells 500           # the same at 500 cells
uv run python3 scripts/warmbench.py --tools gpy       # launch → first/last cell, real pty
uv run --with jupyterlab --with marimo python3 scripts/warmbench.py --runs 3
uv run --with jupyterlab --with marimo python3 scripts/membench.py
uv run python3 scripts/kernelbench.py
```

Never run a benchmark while the tests run.
