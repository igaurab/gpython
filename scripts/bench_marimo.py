"""Benchmark of a marimo notebook whose hidden cells show problem text (the static preview).

    uv run python3 scripts/bench_marimo.py [--problems N] [--no-preview] [--profile load|moves] [--json]

Each problem is a hidden mo.vstack([mo.md(...), mo.Html(...)]) cell plus a code cell, like a
LeetCode study notebook. --no-preview turns the static preview off to get the baseline.
Prints one `name<TAB>seconds` row per metric.
"""

from __future__ import annotations

import argparse
import asyncio
import cProfile
import json
import pstats
import sys
import tempfile
import time
from pathlib import Path

t0 = time.perf_counter()
import gpython.app as gapp  # noqa: E402

IMPORT_APP = time.perf_counter() - t0

HTML = (
    "<p>Given an integer array <code>nums</code>, return all the triplets <code>[nums[i], nums[j], nums[k]]</code> "
    "such that <code>i != j</code>, <code>i != k</code>, and <code>j != k</code>, and "
    "<code>nums[i] + nums[j] + nums[k] == 0</code>.</p>\n<p>Notice that the solution set must not contain "
    "duplicate triplets.</p>\n<p><strong>Example 1:</strong></p>\n<pre><strong>Input:</strong> nums = [-1,0,1,2,-1,-4]\n"
    "<strong>Output:</strong> [[-1,-1,2],[-1,0,1]]\n<strong>Explanation:</strong> \nnums[0] + nums[1] + nums[2] = 0.\n"
    "</pre>\n<p><strong>Example 2:</strong></p>\n<pre><strong>Input:</strong> nums = [0,1,1]\n<strong>Output:</strong> []\n"
    "</pre>\n<p><strong>Constraints:</strong></p>\n<ul>\n\t<li><code>3 &lt;= nums.length &lt;= 3000</code></li>\n"
    "\t<li><code>-10<sup>5</sup> &lt;= nums[i] &lt;= 10<sup>5</sup></code></li>\n</ul>"
)


def make_notebook(problems: int) -> Path:
    cells = ['@app.cell(hide_code=True)\ndef _():\n    import marimo as mo\n\n    return (mo,)\n']
    for i in range(problems):
        md = f"## {i}. 3Sum (Medium)\n\n*Study plan: Two Pointers Tags: Array, Two Pointers, Sorting*"
        cells.append(f"@app.cell(hide_code=True)\ndef _(mo):\n    mo.vstack([mo.md({md!r}), mo.Html({HTML!r})])\n    return\n")
        cells.append(f"@app.cell\ndef _():\n    x{i} = {i}\n    return\n")
    text = 'import marimo\n\n__generated_with = "0.25.2"\napp = marimo.App(width="medium")\n\n\n'
    text += "\n\n".join(cells) + '\n\nif __name__ == "__main__":\n    app.run()\n'
    path = Path(tempfile.mkdtemp(prefix="gpbench-")) / "lc.py"
    path.write_text(text)
    return path


async def bench(problems: int, preview: bool, profile_what: str | None) -> dict[str, float]:
    from gpython import cell as gcell

    if not preview:
        gcell.marimo_markdown = lambda source: None
    path = make_notebook(problems)
    results: dict[str, float] = {"import_app": IMPORT_APP}
    prof = cProfile.Profile()

    start, cpu = time.perf_counter(), time.process_time()
    app = gapp.GPython(path)
    if profile_what == "load":
        prof.enable()
    async with app.run_test(size=(140, 45)) as pilot:
        results["mount"] = time.perf_counter() - start
        await app.loaded.wait()
        await pilot.pause()
        results["loaded"] = time.perf_counter() - start
        results["loaded_proc"] = time.process_time() - cpu
        if profile_what == "load":
            prof.disable()
        cells = app.cells()
        assert len(cells) == 1 + 2 * problems, len(cells)
        shown = sum(1 for c in cells if c.output_box.has_output)
        assert shown == (problems if preview else 0), shown

        cells[0].focus()
        await pilot.pause()
        if profile_what == "moves":
            prof.enable()
        t, cpu = time.perf_counter(), time.process_time()
        for _ in range(30):
            app.action_focus_cell(1)
            await pilot.pause()
        results["moves_30"] = time.perf_counter() - t
        results["moves_30_proc"] = time.process_time() - cpu
        if profile_what == "moves":
            prof.disable()
        app.kernel.ready.set()  # do not wait for the kernel on exit

    if profile_what:
        stats = pstats.Stats(prof, stream=sys.stderr)
        stats.sort_stats("tottime").print_stats(25)
        stats.sort_stats("cumulative").print_stats(45)
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--problems", type=int, default=50)
    parser.add_argument("--no-preview", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--profile", choices=["load", "moves"])
    args = parser.parse_args()
    results = asyncio.run(bench(args.problems, not args.no_preview, args.profile))
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for name, seconds in results.items():
            print(f"{name}\t{seconds:.3f}")


if __name__ == "__main__":
    main()
