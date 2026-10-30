"""Headless memory profile for gpython: where the bytes go and what does not come back.

    uv run python3 scripts/memprof.py [--cells N] [--cycles N] [--top N] [--who]

Every phase prints the process RSS, the Python heap (tracemalloc) and the change in live
counts of the app's own object types; phases that should be neutral (add then delete, run
then clear, open then close) also print the allocation sites that grew. A weak set of every
Cell and NotebookPane ever created reports how many removed ones are still alive after a full
collection, and `--who` walks the referrers of one of them (how the theme-watcher leak in
Textual's TextArea was found). RSS under tracemalloc is inflated by its own bookkeeping;
`--no-trace` gives the honest RSS without the per-line detail. Numbers from 2026-10-30 are
in `docs/plans/TODO.md` §1 "Memory".
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import linecache
import os
import string
import tempfile
import tracemalloc
import types
import weakref
from collections import Counter, defaultdict
from functools import partial
from pathlib import Path

import psutil
import textual._wait

textual._wait.SLEEP_GRANULARITY = 0.002

TRACKED = ("Cell", "VimTextArea", "OutputArea", "MarkdownView", "NotebookPane", "Kernel", "Static", "Timer", "EditHistory", "Edit", "CellSnapshot", "Segment", "Style", "Strip", "Text", "Region", "Content")
HEAP_FILTER = ("gpython", "textual", "rich", "tree_sitter", "jupyter_client", "zmq", "markdown_it")

PROC = psutil.Process(os.getpid())


def rss_mb(process: psutil.Process = PROC) -> float:
    try:
        return process.memory_info().rss / 1e6
    except psutil.Error:
        return 0.0


def heap_mb() -> float:
    return tracemalloc.get_traced_memory()[0] / 1e6 if tracemalloc.is_tracing() else 0.0


def live_counts() -> Counter:
    counts: Counter = Counter()
    for obj in gc.get_objects():
        name = type(obj).__name__
        if name in TRACKED:
            counts[name] += 1
    return counts


def collect() -> None:
    for _ in range(3):
        gc.collect()


def make_notebook(n: int) -> Path:
    parts = []
    for i in range(n):
        body = f"v{i} = {i}\nprint(v{i})\n" + "".join(f"# comment line {j}\n" for j in range(8))
        parts.append("# %%\n" + body)
    path = Path(tempfile.mkdtemp(prefix="gpmem-")) / "mem.py"
    path.write_text("\n".join(parts))
    return path


def short(filename: str) -> str:
    return filename.split("site-packages/")[-1].split("src/")[-1]


class Report:
    def __init__(self, top: int) -> None:
        self.top = top
        self.prev_snapshot: tracemalloc.Snapshot | None = None
        self.prev_counts: Counter = Counter()
        self.prev_heap = 0.0

    def phase(self, name: str, *, diff: bool = False, note: str = "") -> None:
        collect()
        counts = live_counts()
        snapshot = tracemalloc.take_snapshot() if tracemalloc.is_tracing() else None
        heap = heap_mb()
        print(f"\n=== {name}  rss {rss_mb():.1f} MB  heap {heap:.2f} MB ({heap - self.prev_heap:+.2f})  {note}")
        changed = {k: counts[k] - self.prev_counts[k] for k in TRACKED if counts[k] != self.prev_counts[k]}
        if changed:
            print("  live objects:", ", ".join(f"{k} {v:+d}" for k, v in sorted(changed.items(), key=lambda kv: -abs(kv[1]))))
        if diff and snapshot is not None and self.prev_snapshot is not None:
            self.print_diff(snapshot)
        self.prev_snapshot, self.prev_counts, self.prev_heap = snapshot, counts, heap

    def print_diff(self, snapshot: tracemalloc.Snapshot) -> None:
        shown = 0
        for stat in snapshot.compare_to(self.prev_snapshot, "lineno"):
            frame = stat.traceback[0]
            if stat.size_diff <= 0 or not any(f in frame.filename for f in HEAP_FILTER):
                continue
            line = linecache.getline(frame.filename, frame.lineno).strip()
            print(f"  {stat.size_diff / 1e3:8.1f} kB  {stat.count_diff:+6d} blocks  {short(frame.filename)}:{frame.lineno}  {line[:70]}")
            shown += 1
            if shown >= self.top:
                break


def top_allocations(snapshot: tracemalloc.Snapshot, n: int) -> None:
    print("\n--- largest allocation sites at full load, by file ---")
    for stat in snapshot.statistics("filename")[:n]:
        print(f"  {stat.size / 1e6:7.2f} MB  {stat.count:8d} blocks  {short(stat.traceback[0].filename)}")


def describe(obj, child) -> str:
    name = type(obj).__name__
    if isinstance(obj, dict):
        keys = [k for k, v in obj.items() if v is child]
        return f"dict[{keys[0]!r}]" if keys else "dict"
    if isinstance(obj, (list, set, frozenset, tuple)) or name == "deque":
        return f"{name}(len {len(obj)})"
    if isinstance(obj, types.MethodType):
        return f"bound method {obj.__func__.__qualname__}"
    if isinstance(obj, partial):
        return f"partial({getattr(obj.func, '__qualname__', obj.func)})"
    if isinstance(obj, asyncio.Task):
        return f"Task {obj.get_name()}"
    if isinstance(obj, types.CoroutineType):
        return f"coroutine {obj.__qualname__}"
    if isinstance(obj, types.FrameType):
        return f"frame {obj.f_code.co_qualname}"
    return name


def who_holds(target, depth: int = 7, width: int = 3) -> list[str]:
    """Referrer chains from `target` outwards, built from one reverse map of the whole heap
    so the walk's own temporaries do not show up as holders."""
    objs = gc.get_objects()
    rev: dict[int, list] = defaultdict(list)
    for o in objs:
        for r in gc.get_referents(o):
            rev[id(r)].append(o)
    mine = {id(l) for l in rev.values()} | {id(rev), id(objs)}
    out: list[str] = []

    def walk(obj, path: list[str], seen: set[int], level: int) -> None:
        refs = [r for r in rev.get(id(obj), []) if id(r) not in seen and id(r) not in mine]
        refs = [r for r in refs if type(r).__name__ not in ("WeakSet", "weakref", "ReferenceType", "KeyedRef")]
        refs = [r for r in refs if not (isinstance(r, types.CoroutineType) and r.__qualname__ in ("profile", "main"))]
        if level == depth or not refs:
            out.append(" <- ".join(path))
            return
        for r in refs[:width]:
            label = describe(r, obj)
            nxt = r
            if isinstance(r, dict):
                owners = [o for o in rev.get(id(r), []) if getattr(o, "__dict__", None) is r]
                if owners:
                    keys = [k for k, v in r.items() if v is obj]
                    label = f"{type(owners[0]).__name__}.{keys[0] if keys else '?'}"
                    nxt = owners[0]
            walk(nxt, path + [label], seen | {id(r), id(nxt)}, level + 1)

    walk(target, [type(target).__name__], {id(target)}, 0)
    return out


def report_alive(label: str, ever: weakref.WeakSet, who: bool) -> None:
    collect()
    alive = [x for x in ever if not getattr(x, "is_attached", False)]
    print(f"  removed {label} still alive after gc: {len(alive)}")
    if who and alive:
        for chain in who_holds(alive[-1])[:10]:
            print("    ", chain)


def editor_cache_stats(app) -> str:
    sizes = [len(c.editor._line_cache) for c in app.cells()]
    return f"editor line caches: {sum(sizes)} strips in {len(sizes)} editors (max {max(sizes, default=0)})"


async def profile(n: int, cycles: int, top: int, who: bool) -> None:
    report = Report(top)
    import gpython.app as gapp
    from gpython.cell import Cell
    from gpython.pane import NotebookPane

    report.phase("import gpython.app", diff=True)

    cells_ever: weakref.WeakSet = weakref.WeakSet()
    panes_ever: weakref.WeakSet = weakref.WeakSet()
    for cls, seen in ((Cell, cells_ever), (NotebookPane, panes_ever)):
        original = cls.__init__

        def tracking(self, *args, _original=original, _seen=seen, **kwargs):
            _original(self, *args, **kwargs)
            _seen.add(self)

        cls.__init__ = tracking

    app = gapp.GPython(make_notebook(n))
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        report.phase(f"first frame ({len(app.cells())} cells mounted)", diff=True)
        await app.loaded.wait()
        await pilot.pause()
        report.phase(f"loaded ({n} cells)", diff=True, note=editor_cache_stats(app))
        full_load = tracemalloc.take_snapshot() if tracemalloc.is_tracing() else None
        if full_load is not None:
            per_cell = (report.prev_heap - heap_after_import) / n
            print(f"  ~{per_cell * 1000:.0f} kB of Python heap per mounted cell")

        pane = app.pane
        await app.kernel.ready.wait()
        await pilot.pause()
        kernel_pid = app.kernel.manager.provisioner.process.pid if app.kernel.manager and app.kernel.manager.provisioner else None
        kernel_rss = rss_mb(psutil.Process(kernel_pid)) if kernel_pid else 0.0
        report.phase("kernel ready", diff=True, note=f"kernel process rss {kernel_rss:.0f} MB")

        cells = app.cells()
        cells[0].editor.focus()
        await pilot.pause()
        report.phase("baseline before activity")

        added = [await pane._insert(app.cells()[-1]) for _ in range(cycles)]
        await pilot.pause()
        report.phase(f"inserted {cycles} empty cells", diff=True)
        await pane._remove_cells(added, record=False)
        del added
        for _ in range(3):
            await pilot.pause()
        report.phase(f"removed those {cycles} cells", diff=True)
        report_alive("cells", cells_ever, who)
        for step in range(1, 6):
            await pilot.resize_terminal(140 + step, 45)
            await pilot.pause()
        report.phase("five more layouts (the arrangement cache keeps the last four)", diff=True)
        report_alive("cells", cells_ever, who)

        for _ in range(cycles):
            cell = await pane._insert(app.cells()[-1])
            pane.select(cell, "insert")
            await pilot.press("a", "escape")
            await pane._remove_cells([cell])
            del cell
        await pilot.pause()
        report.phase(f"{cycles} x (add, type, delete) with undo recorded", diff=True, note=f"undo stack {len(pane._undo)}")
        pane._undo.clear()
        report.phase("undo stack cleared", diff=True)
        report_alive("cells", cells_ever, who)

        cells = app.cells()
        cells[0].editor.focus()
        await pilot.pause()
        letters = (string.ascii_lowercase * 20)[:300]
        await pilot.press("i", *letters, "escape")
        await pilot.pause()
        report.phase("typed 300 characters in cell 1", diff=True, note=f"undo checkpoints {len(cells[0].editor.history._undo_stack)}")
        for _ in range(10):
            await pilot.press("i", *letters[:30], "escape", "u")
        await pilot.pause()
        report.phase("10 x (type 30, undo)", diff=True)

        for _ in range(2):
            for step in (1, -1):
                for _ in range(len(cells) - 1):
                    app.action_focus_cell(step)
                    await pilot.pause()
        report.phase(f"{4 * (len(cells) - 1)} cell moves", diff=True, note=editor_cache_stats(app))
        for _ in range(2):
            for step in (1, -1):
                for _ in range(len(cells) - 1):
                    app.action_focus_cell(step)
                    await pilot.pause()
        report.phase(f"another {4 * (len(cells) - 1)} cell moves", diff=True, note=editor_cache_stats(app))

        cell = cells[2]
        for _ in range(3):
            for i in range(2000):
                cell.add_output("stream", {"name": "stdout", "text": f"line {i}\n"})
            await pilot.pause()
            await pilot.pause()
        report.phase("3 x 2000 stream lines into one cell", diff=True, note=f"output {len(cell.output_text)} chars")
        for _ in range(3):
            cell.clear_output()
            for i in range(2000):
                cell.add_output("stream", {"name": "stdout", "text": f"line {i}\n"})
            await pilot.pause()
            await pilot.pause()
        report.phase("3 x (clear, 2000 lines)", diff=True)
        cell.clear_output()
        for _ in range(3):
            await pilot.pause()
        report.phase("output cleared", diff=True)

        for i in range(3):
            cell.add_output("display_data", {"data": {"text/markdown": f"# heading {i}\n\nsome *markdown* text\n\n- a\n- b"}})
            cell.add_output("execute_result", {"data": {"text/plain": "x" * 500}})
            await pilot.pause()
            cell.clear_output()
            await pilot.pause()
        report.phase("3 x (markdown + result output, clear)", diff=True)

        md = cells[4]
        await pane.set_cell_kind(md, "markdown", record=False)
        await pilot.pause()
        for _ in range(5):
            md.start_editing()
            pane.select(md, "normal")
            await pilot.pause()
            await md.show_rendered()
            await pilot.pause()
        await pane.set_cell_kind(md, "code", record=False)
        await pilot.pause()
        report.phase("markdown: 5 edit/render cycles, back to code", diff=True)

        for _ in range(10):
            pane.search("comment", 1, "cell")
            await pilot.pause()
            pane.clear_search_highlight()
            await pilot.pause()
        report.phase("10 x search highlight on/off", diff=True)

        for _ in range(cycles):
            await app.run_cells([cells[1].cid])
        await pilot.pause()
        report.phase(f"ran cell 2 {cycles} times", diff=True)

        cells[3].editor.text = "for i in range(3000): print('x', i)"
        for _ in range(3):
            await app.run_cells([cells[3].cid])
            await pilot.pause()
        report.phase("ran a 3000-line print cell 3 times", diff=True)

        other = make_notebook(10)
        for _ in range(3):
            new = await app.open_file(other, how="tab")
            await new.loaded.wait()
            await new.kernel.ready.wait()
            await pilot.pause()
            await app.workspace.delete(new)
            del new
            await app.workers.wait_for_complete()
            await pilot.pause()
        for step in range(1, 6):
            await pilot.resize_terminal(140 + step, 45)
            await pilot.pause()
        report.phase("3 x (open a second notebook with its kernel, close and delete it)", diff=True)
        report_alive("panes", panes_ever, who)
        report_alive("cells", cells_ever, who)

        report.phase("final", diff=True)
        if full_load is not None:
            top_allocations(full_load, top)


heap_after_import = 0.0


def main() -> None:
    global heap_after_import
    parser = argparse.ArgumentParser()
    parser.add_argument("--cells", type=int, default=60)
    parser.add_argument("--cycles", type=int, default=30)
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--who", action="store_true", help="walk the referrers of a leaked cell or pane")
    parser.add_argument("--no-trace", action="store_true", help="RSS only, no tracemalloc")
    args = parser.parse_args()
    if not args.no_trace:
        tracemalloc.start(1)
    import gpython.app

    heap_after_import = heap_mb()
    asyncio.run(profile(args.cells, args.cycles, args.top, args.who))


if __name__ == "__main__":
    main()
