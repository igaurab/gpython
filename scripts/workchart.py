"""Draw workbench.py's results for docs/benchmarks (or a paper).

    uv run --with matplotlib python3 scripts/workchart.py RESULTS.json OUT_STEM

Writes OUT_STEM.png (300 dpi) and OUT_STEM.pdf, two panels:
  (a) the tool's own cost per cell (mean idle gap between two work cells) for each problem at
      the largest N, mean ± 1 sample std over the runs;
  (b) the share of launch-to-last-cell time that is not the cells' own work, against N, one
      line per tool per domain (CPU solid, IO dashed).
and OUT_STEM_problems.png/.pdf: launch until the last cell against N, one panel per problem,
with the cells' own work (N × mean work time) as a grey reference line.
Colours and hatches are benchchart.py's (Okabe–Ito, colour-blind safe).
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

from benchchart import COLOR, HATCH, TOOLS, setup

LABEL = {"gpy": "gpy", "lab": "JupyterLab", "marimo": "marimo"}
MARKER = {"gpy": "o", "lab": "s", "marimo": "^"}


def overhead_share(run, n):
    return 1 - n * run["work_ms"] / 1000 / run["last"]


def draw(rows, out):
    import matplotlib
    import matplotlib.pyplot as plt
    import numpy as np

    setup(matplotlib)
    tools = [t for t, _ in TOOLS if any(r["tool"] == t for r in rows)]
    problems = list(dict.fromkeys(r["problem"] for r in rows))
    titles = {r["problem"]: r["title"] for r in rows}
    domain = {r["problem"]: r["domain"] for r in rows}
    sizes = sorted({r["cells"] for r in rows})
    top = sizes[-1]

    def row(problem, tool, n):
        return next(r for r in rows if r["problem"] == problem and r["tool"] == tool and r["cells"] == n)

    fig, (left, right) = plt.subplots(1, 2, figsize=(7.2, 2.8), constrained_layout=True,
                                      gridspec_kw={"width_ratios": [1.5, 1]})
    width = 0.8 / len(tools)
    x = np.arange(len(problems))
    for i, tool in enumerate(tools):
        means = [row(p, tool, top)["gap_ms"] for p in problems]
        stds = [row(p, tool, top)["gap_ms_std"] for p in problems]
        left.bar(x + (i - (len(tools) - 1) / 2) * width, means, width * 0.92, yerr=stds, capsize=1.5,
                 color=COLOR[tool], hatch=HATCH[tool], edgecolor="black", linewidth=0.6, label=LABEL[tool],
                 error_kw={"elinewidth": 0.7, "capthick": 0.7})
    left.set_xticks(x, problems)
    left.set_ylabel("ms between cells")
    left.set_title(f"(a) Tool cost per cell, {top} cells")
    left.yaxis.grid(True, color="#d9d9d9", linewidth=0.5)
    left.set_axisbelow(True)
    left.legend(frameon=False, loc="upper left")

    for tool in tools:
        for dom, style in (("cpu", "-"), ("io", "--")):
            keys = [p for p in problems if domain[p] == dom]
            if not keys:
                continue
            means, stds = [], []
            for n in sizes:
                per_run = [statistics.mean(overhead_share(row(p, tool, n)["runs"][k], n) for p in keys)
                           for k in range(len(row(keys[0], tool, n)["runs"]))]
                means.append(100 * statistics.mean(per_run))
                stds.append(100 * statistics.stdev(per_run) if len(per_run) > 1 else 0)
            right.errorbar(sizes, means, yerr=stds, color=COLOR[tool], linestyle=style, marker=MARKER[tool],
                           markersize=3.5, linewidth=1.2, capsize=1.5, elinewidth=0.7,
                           label=f"{LABEL[tool]} {dom.upper()}")
    right.set_xscale("log")
    right.set_ylim(0, 100)
    right.set_xlabel("cells (N)")
    right.set_ylabel("% of time not in cell work")
    right.set_title("(b) Overhead share vs N")
    right.yaxis.grid(True, color="#d9d9d9", linewidth=0.5)
    right.legend(frameon=False, fontsize=6.5, ncol=1, loc="lower left")
    for ext in ("png", "pdf"):
        fig.savefig(f"{out}.{ext}", dpi=300)
    plt.close(fig)

    cols = 4
    fig, axes = plt.subplots(2, cols, figsize=(7.2, 3.8), constrained_layout=True, sharex=True)
    for ax, problem in zip(axes.flat, problems):
        for tool in tools:
            ax.errorbar(sizes, [row(problem, tool, n)["last"] for n in sizes],
                        yerr=[row(problem, tool, n)["last_std"] for n in sizes], color=COLOR[tool],
                        marker=MARKER[tool], markersize=3, linewidth=1.1, capsize=1.5, elinewidth=0.7,
                        label=LABEL[tool])
        work = [n * row(problem, tools[0], n)["work_ms"] / 1000 for n in sizes]
        ax.plot(sizes, work, color="#7f7f7f", linewidth=0.9, linestyle=":", label="cell work alone")
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_title(f"{problem}: {titles[problem]}", fontsize=7.5)
        ax.yaxis.grid(True, color="#e5e5e5", linewidth=0.5)
    for ax in axes.flat[len(problems):]:
        handles, labels = axes.flat[0].get_legend_handles_labels()
        ax.legend(handles, labels, frameon=False, loc="center")
        ax.axis("off")
    for ax in axes[:, 0]:
        ax.set_ylabel("s, launch → last cell")
    for ax in axes[-1]:
        ax.set_xlabel("cells (N)")
    for ext in ("png", "pdf"):
        fig.savefig(f"{out}_problems.{ext}", dpi=300)
    plt.close(fig)


def main():
    rows = json.loads(Path(sys.argv[1]).read_text())
    draw(rows, sys.argv[2])


if __name__ == "__main__":
    main()
