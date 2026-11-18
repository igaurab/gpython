"""asyncio.run, loop.run_until_complete and asyncio.Runner inside a cell, kernel side.

A cell runs inside the kernel's event loop, which is already running, and asyncio refuses to run
a loop from inside a running one, so `asyncio.run(main())` in a cell (or in a library a cell
calls) raises RuntimeError. `install` makes it work, with the idea of nest_asyncio (BSD 2-Clause,
Ewald de Wit) and the least patching that carries it: the awaitable becomes a task on the running
loop, and the loop is driven by hand, one iteration at a time, until that task is done. While it
is driven the task the cell itself runs in is hidden, so the tasks inside may be entered; a
callback whose Context is the one the cell runs in (ipykernel 7 runs every cell in one Context)
cannot be entered again and waits until the drive is over.

The Task class, the loop class's own iteration and every loop that is not running stay as they
are, so code that only awaits runs exactly as before (nest_asyncio2, which ipykernel ships, made
it 20 to 70 % slower; the comparison is in docs/plans/16-asyncio-run-in-cells.md). A loop that is
not a plain BaseEventLoop is left alone, which keeps the RuntimeError.

Read by the kernel from this file's path (kernel.py), so nothing here may import gpython.
"""

from __future__ import annotations

import asyncio
import heapq
from asyncio import base_events, events, runners, tasks

MAXIMUM_SELECT_TIMEOUT = 24 * 3600
NEEDED = ("_ready", "_scheduled", "_selector", "_process_events", "_clock_resolution", "_stopping", "_timer_cancelled_count")

_original_run = runners.run
_original_run_until_complete = base_events.BaseEventLoop.run_until_complete
_original_runner_run = getattr(getattr(runners, "Runner", None), "run", None)


def install() -> None:
    """Make asyncio.run, run_until_complete and Runner.run work from inside a running loop."""
    asyncio.run = runners.run = run
    base_events.BaseEventLoop.run_until_complete = run_until_complete
    if _original_runner_run is not None:
        runners.Runner.run = runner_run


def drivable(loop) -> bool:
    """True for a loop whose iteration is asyncio's own, the only one `_turn` knows how to repeat."""
    return (
        isinstance(loop, base_events.BaseEventLoop)
        and type(loop)._run_once is base_events.BaseEventLoop._run_once
        and all(hasattr(loop, name) for name in NEEDED)
    )


def run(main, **options):
    """asyncio.run; from inside a running loop, `main` runs on that loop and this call waits for it."""
    loop = events._get_running_loop()
    if loop is None or not drivable(loop):
        return _original_run(main, **options)
    return _run_nested(loop, main)


def run_until_complete(self, future):
    """loop.run_until_complete; the running loop is driven by hand, another loop runs inside it."""
    running = events._get_running_loop()
    if running is None:
        return _original_run_until_complete(self, future)
    if running is self:
        if not drivable(self):
            return _original_run_until_complete(self, future)
        return _run_nested(self, future)
    with _OuterLoopAside(running):
        return _original_run_until_complete(self, future)


def runner_run(self, coro, **options):
    """asyncio.Runner.run (anyio.run uses it); inside a running loop the Runner's own loop runs nested."""
    running = events._get_running_loop()
    if running is None:
        return _original_runner_run(self, coro, **options)
    with _OuterLoopAside(running):
        return _original_runner_run(self, coro, **options)


class _OuterLoopAside:
    """For the time another loop runs on this thread: the running loop and its current task are put away."""

    def __init__(self, loop) -> None:
        self.loop = loop
        self.task = None

    def __enter__(self) -> None:
        self.task = _hide_current_task(self.loop)
        events._set_running_loop(None)

    def __exit__(self, *exc) -> None:
        events._set_running_loop(self.loop)
        _show_current_task(self.loop, self.task)


def _hide_current_task(loop):
    swap = getattr(tasks, "_swap_current_task", None)
    if swap is None:
        return tasks._current_tasks.pop(loop, None)
    try:
        return swap(loop, None)
    except KeyError:
        return None


def _show_current_task(loop, task) -> None:
    if task is None:
        return
    swap = getattr(tasks, "_swap_current_task", None)
    if swap is None:
        tasks._current_tasks[loop] = task
    else:
        swap(loop, task)


def _run_nested(loop, awaitable):
    task = asyncio.ensure_future(awaitable, loop=loop)
    try:
        _drive(loop, task)
    except BaseException:
        if not task.done():
            task.cancel()
            try:
                _drive(loop, task)
            except BaseException:
                pass
        if task.done() and not task.cancelled():
            task.exception()
        raise
    return task.result()


def _drive(loop, future) -> None:
    """Iterate the running loop until `future` is done, then once more for its done callbacks."""
    waiting: list = []
    hidden = _hide_current_task(loop)
    try:
        while not future.done():
            if loop._stopping:
                raise RuntimeError("Event loop stopped before Future completed.")
            _turn(loop, waiting)
        if loop._ready:
            _turn(loop, waiting)
    finally:
        _show_current_task(loop, hidden)
        loop._ready.extendleft(reversed(waiting))


def _turn(loop, waiting: list) -> None:
    """One iteration, as BaseEventLoop._run_once does it, except that a callback whose Context is
    already entered goes to `waiting` instead of failing."""
    ready, scheduled = loop._ready, loop._scheduled
    while scheduled and scheduled[0]._cancelled:
        loop._timer_cancelled_count -= 1
        heapq.heappop(scheduled)._scheduled = False
    if ready or loop._stopping:
        timeout = 0
    elif scheduled:
        timeout = min(max(scheduled[0]._when - loop.time(), 0), MAXIMUM_SELECT_TIMEOUT)
    else:
        timeout = None
    loop._process_events(loop._selector.select(timeout))
    end = loop.time() + loop._clock_resolution
    while scheduled and scheduled[0]._when < end:
        handle = heapq.heappop(scheduled)
        handle._scheduled = False
        ready.append(handle)
    for _ in range(len(ready)):
        if not ready:
            break
        handle = ready.popleft()
        if handle._cancelled:
            continue
        if _entered(getattr(handle, "_context", None)):
            waiting.append(handle)
            continue
        handle._run()


def _entered(context) -> bool:
    """True when `context` is running further up the stack, so a callback in it cannot start now."""
    if context is None:
        return False
    try:
        context.run(int)
    except RuntimeError:
        return True
    return False
