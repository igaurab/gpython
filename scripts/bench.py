"""Headless performance benchmark for gpython.

    uv run python3 scripts/bench.py [--cells N] [--json] [--profile keys|mount|moves|stream]

Prints one `name<TAB>seconds` row per metric (or a JSON dict with --json).
"""

from __future__ import annotations

import argparse
import asyncio
import cProfile
import json
import pstats
import string
import sys
import tempfile
import time
from pathlib import Path

t0 = time.perf_counter()
import gpython.app as gapp

IMPORT_APP = time.perf_counter() - t0

_WAIT_ROWS = ("select.kqueue", "select.select", "select.poll", "select.epoll")


def make_notebook(n: int) -> Path:
    parts = []
    for i in range(n):
        body = f"v{i} = {i}\nprint(v{i})\n" + "".join(f"# comment line {j}\n" for j in range(8))
        parts.append("# %%\n" + body)
    path = Path(tempfile.mkdtemp(prefix="gpbench-")) / "bench.py"
    path.write_text("\n".join(parts))
    return path


def cpu_seconds(profile: cProfile.Profile) -> float:
    """Sum of tottime of all functions minus the event-loop wait rows."""
    stats = pstats.Stats(profile)
    total = 0.0
    for (_file, _line, func), (_cc, _nc, tt, _ct, _callers) in stats.stats.items():
        if any(w in func for w in _WAIT_ROWS):
            continue
        total += tt
    return total


def top(profile: cProfile.Profile, n: int = 10, sort: str = "tottime") -> str:
    import io

    out = io.StringIO()
    pstats.Stats(profile, stream=out).sort_stats(sort).print_stats(n)
    return out.getvalue()


async def bench(n: int, profile_what: str | None) -> dict[str, float]:
    path = make_notebook(n)
    results: dict[str, float] = {"import_app": IMPORT_APP}
    profiles: dict[str, cProfile.Profile] = {}

    start = time.perf_counter()
    app = gapp.GPython(path)
    kernel_ready_at: list[float] = []
    first_paint: list[float] = []
    real_display = app._display

    def display(screen, renderable):
        if renderable is not None and not app._batch_count and not first_paint:
            first_paint.append(time.perf_counter() - start)
        return real_display(screen, renderable)

    app._display = display

    async def watch_kernel() -> None:
        await app.kernel.ready.wait()
        kernel_ready_at.append(time.perf_counter() - start)

    watcher = asyncio.create_task(watch_kernel())
    mount_prof = cProfile.Profile() if profile_what == "mount" else None
    if mount_prof:
        mount_prof.enable()
    async with app.run_test(size=(140, 45)) as pilot:
        results[f"mount_{n}"] = time.perf_counter() - start
        results["first_paint"] = first_paint[0] if first_paint else -1.0
        t = time.perf_counter()
        await pilot.pause()
        results["first_pause"] = time.perf_counter() - t
        loaded = getattr(app, "loaded", None)
        if loaded is not None:
            await loaded.wait()
            results[f"loaded_{n}"] = time.perf_counter() - start
        if mount_prof:
            mount_prof.disable()
            profiles["mount"] = mount_prof

        cells = app.cells()
        assert len(cells) == n, (len(cells), n)

        cells[0].editor.focus()
        await pilot.pause()
        await pilot.press("i")
        await pilot.pause()
        letters = (string.ascii_lowercase * 2)[:30]
        prof = cProfile.Profile()
        t = time.perf_counter()
        prof.enable()
        await pilot.press(*letters)
        prof.disable()
        results["keys_30_wall"] = time.perf_counter() - t
        results["keys_30_cpu"] = cpu_seconds(prof)
        profiles["keys"] = prof
        t = time.process_time()
        await pilot.press(*letters)
        results["keys_30_proc"] = time.process_time() - t
        await pilot.press("escape")
        await pilot.pause()

        prof = cProfile.Profile()
        prof.enable()
        await pilot.press(*["f12"] * 30)
        prof.disable()
        results["press_floor_cpu"] = cpu_seconds(prof)
        results["keys_30_net"] = max(0.0, results["keys_30_cpu"] - results["press_floor_cpu"])
        t = time.perf_counter()
        for _ in range(30):
            await pilot.pause()
        results["pause_30_floor"] = time.perf_counter() - t

        t, cpu = time.perf_counter(), time.process_time()
        for _ in range(30):
            app.action_focus_cell(1)
            await pilot.pause()
        results["moves_30"] = time.perf_counter() - t
        results["moves_30_proc"] = time.process_time() - cpu
        if profile_what == "moves":
            prof = cProfile.Profile()
            prof.enable()
            for _ in range(30):
                app.action_focus_cell(-1)
                await pilot.pause()
            prof.disable()
            profiles["moves"] = prof

        cell = cells[2]
        prof = cProfile.Profile() if profile_what == "stream" else None
        t = time.perf_counter()
        if prof:
            prof.enable()
        for i in range(2000):
            cell.add_output("stream", {"name": "stdout", "text": f"line {i}\n"})
        await pilot.pause()
        if prof:
            prof.disable()
            profiles["stream"] = prof
        results["stream_2000"] = time.perf_counter() - t
        assert cell.output_text.endswith("line 1999\n"), cell.output_text[-50:]

        await asyncio.wait_for(watcher, 60)
        results["kernel_ready"] = kernel_ready_at[0]

        t = time.perf_counter()
        await app.run_cells([cells[1].cid])
        results["run_trivial"] = time.perf_counter() - t

        cells[3].editor.text = "for i in range(3000): print('x', i)"
        t = time.perf_counter()
        await app.run_cells([cells[3].cid])
        await pilot.pause()
        results["run_print_3000"] = time.perf_counter() - t
        assert cells[3].output_text.endswith("x 2999\n"), cells[3].output_text[-50:]

    if profile_what:
        print(top(profiles[profile_what], 25, "tottime"), file=sys.stderr)
        print(top(profiles[profile_what], 40, "cumulative"), file=sys.stderr)
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cells", type=int, default=60)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--profile", choices=["keys", "mount", "moves", "stream"])
    args = parser.parse_args()
    results = asyncio.run(bench(args.cells, args.profile))
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for name, seconds in results.items():
            print(f"{name}\t{seconds:.3f}")


if __name__ == "__main__":
    main()
