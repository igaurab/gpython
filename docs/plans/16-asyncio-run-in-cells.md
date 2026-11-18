# 16 – asyncio.run inside a cell

A cell runs inside the kernel's event loop, which is already running. `asyncio.run(main())`
starts a loop, and asyncio allows one running loop per thread, so the call raised
`RuntimeError: asyncio.run() cannot be called from a running event loop`. The same happened
when a library called `asyncio.run` for the user, where writing `await` instead is not possible.

## What gpython does

`src/gpython/kernel_asyncio.py` is read by the kernel at start (`_gp_nested_asyncio` in
`kernel.py`) and replaces three functions:

- `asyncio.run`: with no running loop, the original. Inside a running loop, the coroutine
  becomes a task on that loop and the loop is driven by hand, one iteration at a time, until the
  task is done.
- `loop.run_until_complete`: the same drive for the running loop. For another loop, the running
  one is put aside for the length of the call and the original runs.
- `asyncio.Runner.run`: the running loop is put aside and the original runs its own loop.

While the loop is driven, the task the cell runs in is hidden (`asyncio.tasks._swap_current_task`,
or the `_current_tasks` dict before Python 3.12), because asyncio lets one task be current per
loop. A callback whose Context is the one the cell runs in cannot start until the drive ends, so
it waits in a list and goes back to the front of the queue afterwards.

A loop that is not a plain `BaseEventLoop`, or one whose `_run_once` is not asyncio's own, is
left alone and keeps the error.

## Why not nest_asyncio2

ipykernel 7 depends on `nest_asyncio2`, so calling `nest_asyncio2.apply()` at kernel start was
the other choice. Both were built and run against 44 workflows in real kernels on Python 3.10 to
3.15 (2026-11-17).

| | nest_asyncio2 | ours |
|---|---|---|
| asyncio.run, run_until_complete, library calls, shared connections, interrupt | ok | ok |
| asyncio.Runner | fails | ok |
| plain `await`, 100 tasks × 1000 switches, Python 3.13 (off: 78 ms) | 109 ms | 77 ms |
| plain `await`, start and finish 20 000 tasks, Python 3.13 (off: 51 ms) | 86 ms | 50 ms |
| the same two on Python 3.14 (off: 83 and 45 ms) | 99 and 55 ms | 78 and 42 ms |
| kernel peak memory, Python 3.13 (off: 79.8 MB) | 83.9 MB | 79.7 MB |

nest_asyncio2 replaces the loop's iteration for good and, before Python 3.14, swaps Task and
Future for the pure-Python classes, which is what slows every cell that awaits. Ours changes
nothing until a nested call is made.

## Known limits

- `anyio.run` has its own check and still says "Already running asyncio in this thread".
- A coroutine run by `asyncio.run` in a cell shares the kernel's loop; it does not get a new one.
- Interrupting `asyncio.run` works in a cell without top-level `await`. In a cell that also
  awaits, and in any cell that sits in a plain `await`, the interrupt waits until something wakes
  the loop; ipykernel 7.4 alone behaves the same.
- Not tried: Windows, uvloop, ipykernel 6, `breakpoint()` inside a nested run.
