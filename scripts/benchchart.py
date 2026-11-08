"""Draw warmbench.py's results as a three-panel figure for docs/benchmarks.md (or a paper).

    uv run --with matplotlib python3 scripts/benchchart.py RESULTS.json OUT_STEM

RESULTS.json is `warmbench.py --json` output. Writes OUT_STEM.png (300 dpi) and OUT_STEM.pdf
(fonts embedded). Panels: (a) warm-up of a 1-cell notebook, (b) launch until every cell
has run, (c) memory after the run, each tool's mean with a ±1 sample std error bar.

The palette is Okabe–Ito (colour-blind safe, checked with the dataviz validator); every
series also has its own hatch and a black outline, so the figure reads in greyscale too.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

TOOLS = [
    ("gpy", "gpy (terminal)"),
    ("lab", "JupyterLab (server, no browser)"),
    ("marimo", "marimo edit (server, no browser)"),
]
COLOR = {"gpy": "#0072B2", "lab": "#E69F00", "marimo": "#009E73"}
HATCH = {"gpy": "///", "lab": "...", "marimo": "xx"}


def setup(matplotlib):
    matplotlib.use("Agg")
    matplotlib.pyplot.rcParams.update({
        "font.family": "serif", "font.size": 8.5, "axes.titlesize": 9, "axes.labelsize": 8.5,
        "legend.fontsize": 8, "xtick.labelsize": 8, "ytick.labelsize": 8, "hatch.linewidth": 0.6,
        "axes.spines.top": False, "axes.spines.right": False, "pdf.fonttype": 42, "ps.fonttype": 42,
    })


def bars(ax, tools, groups, value, labels, annotate=False):
    import numpy as np

    width = 0.8 / len(tools)
    x = np.arange(len(groups))
    for i, (tool, _) in enumerate(tools):
        means = [value(tool, g)[0] for g in groups]
        stds = [value(tool, g)[1] for g in groups]
        ax.bar(
            x + (i - (len(tools) - 1) / 2) * width, means, width * 0.92, yerr=stds, capsize=2,
            color=COLOR[tool], hatch=HATCH[tool], edgecolor="black", linewidth=0.6,
            error_kw={"elinewidth": 0.8, "capthick": 0.8},
        )
        if annotate:
            ax.bar_label(ax.containers[-1], labels=[f"{m:.1f}" for m in means], padding=2, fontsize=6, rotation=90)
    ax.set_xticks(x, labels)
    ax.yaxis.grid(True, color="#d9d9d9", linewidth=0.5)
    ax.set_axisbelow(True)


def draw(rows, out):
    import matplotlib
    import matplotlib.patches
    import matplotlib.pyplot as plt

    setup(matplotlib)
    present = {r["tool"] for r in rows}
    tools = [(t, label) for t, label in TOOLS if t in present]

    def row(tool, cells):
        return next(r for r in rows if r["tool"] == tool and r["cells"] == cells)

    cells = sorted({r["cells"] for r in rows})
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.6), constrained_layout=True)

    stages = [("ready", "UI / server\nready"), ("first", "first cell\ndone")]
    bars(axes[0], tools, [s for s, _ in stages], lambda t, s: (row(t, cells[0])[s], row(t, cells[0])[s + "_std"]),
         [label for _, label in stages])
    axes[0].set_title(f"(a) Warm-up, {cells[0]}-cell notebook")
    axes[0].set_ylabel("seconds from launch")

    bars(axes[1], tools, cells, lambda t, n: (row(t, n)["last"], row(t, n)["last_std"]), [str(n) for n in cells], annotate=True)
    top = max(row(t, n)["last"] + row(t, n)["last_std"] for t, _ in tools for n in cells)
    axes[1].set_ylim(0, top * 1.25)
    axes[1].set_title("(b) Launch to all cells run")
    axes[1].set_xlabel("cells in notebook")
    axes[1].set_ylabel("seconds from launch")

    bars(axes[2], tools, cells, lambda t, n: (row(t, n)["mem_mb"], row(t, n)["mem_mb_std"]), [str(n) for n in cells])
    axes[2].set_title("(c) Memory after run-all")
    axes[2].set_xlabel("cells in notebook")
    axes[2].set_ylabel("RSS of process tree (MB)")

    handles = [
        matplotlib.patches.Patch(facecolor=COLOR[t], hatch=HATCH[t], edgecolor="black", linewidth=0.6, label=label)
        for t, label in tools
    ]
    fig.legend(handles=handles, loc="outside upper center", ncol=len(tools), frameon=False)
    for suffix in (".png", ".pdf"):
        fig.savefig(out.with_suffix(suffix), dpi=300)


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    draw(json.loads(Path(sys.argv[1]).read_text()), Path(sys.argv[2]))
    print(Path(sys.argv[2]).with_suffix(".png"))


if __name__ == "__main__":
    main()
