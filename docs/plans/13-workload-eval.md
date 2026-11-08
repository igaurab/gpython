# 13 — Workload evaluation: CPU-bound and IO-bound notebooks, 1 to 1000 cells

Branch `eval/workload-scaling`. For the arXiv paper.

## Why

warmbench uses trivial cells, so it says nothing about notebooks that do real work. There is no
standard notebook benchmark (pyperformance measures CPython, identical in all three tools), so
the evaluation is justified by a cost model, not by taste:

    T(N) = start + Σᵢ (work_i + dispatch_i + render(output_i))

`work` is the user's code (the same interpreter in every tool: a control, not a comparison).
`dispatch` is the tool's own cost per cell. Each workload varies one term and holds the others
fixed: one short print per cell keeps `render` constant, real CPU or IO work varies `work`, N varies
the sum.

## Hypotheses

- H1 gpy's cost per cell is the lowest of the three and stays flat up to 1000 cells.
- H2 Under CPU and IO work the tools converge: the overhead share falls toward
  `gap / (gap + work)`, so gpy costs nothing on real work.
- H3 (later) Differences concentrate in output rendering.
- H4 (later) gpy stays interactive during heavy runs.

## Design

- Problems (about 10 ms of work per cell), `scripts/workbench.py` `PROBLEMS`, one entry each:
  C1 matmul 1200², C2 rfft 1.5M, C3 pure-Python loop, D1 6 MB write+fsync+read,
  D2 100 × 4 KB files, N1 4 sync GETs from a local server (64 KB, 2 ms delay), N2 the same with
  top-level `await asyncio.gather`.
- Scale: N ∈ {1, 10, 100, 500, 1000} work cells after one setup cell. Breadth (more problems) is
  the next scale axis; a problem is one registry entry.
- Per cell the kernel writes its own start/end. Metrics: `gap_ms` (end of cell i → start of
  cell i+1), `work_ms`, launch → last cell, memory.
- Controls: one interpreter for all three kernels (checked per run via `sys.prefix`), BLAS pinned to
  one thread, server outside every tool's process tree, tool order shuffled per run, one
  discarded warm-up launch per tool, 5 runs, `workmeta.json` with machine and load.
- Run: `./scripts/workeval.sh` (about 45 min). Figures: `workchart.py`.

## Results (load 4.2 on 14 cores, 5 runs)

At 1000 cells gpy costs 0.47–0.71 ms per cell, marimo 0.81–2.39, JupyterLab 2.23–3.47, and gpy reaches the
last cell first on all seven problems (C1: 10.75 s, JupyterLab 12.71, marimo 13.90). The sweep found that
`Graph.sort` was roughly cubic for one cell feeding many (1.3 s to order 1000 cells); a heap fixed it and
gpy's launch-to-first-cell at 1000 cells went from 2.54 s to 1.21 s. Memory: gpy below marimo, above
JupyterLab without its browser.

## Unresolved

- Replay of real notebooks (KGTorrent sample) for external validity?
- Cite corpus statistics (Pimentel 2019, Rule 2018, KGTorrent 2021) for the N range: figures to verify.
- Rerun on an idle machine (load ≈ 0) for the paper numbers; this run had load ≈ 4 on 14 cores.
- gpy's launch-to-first-cell still grows 0.83 → 1.21 s from 100 to 1000 cells; profile it.
- H3 output workloads and H4 responsiveness: next round.
