"""A group of more than one cell runs on threads in the kernel (kernel_concurrency.py)."""

import asyncio
import time

import pytest

from gpython.kernel import Kernel


def with_kernel(test):
    async def main():
        k = Kernel()
        await k.start()
        try:
            await test(k)
        finally:
            await k.shutdown()

    asyncio.run(main())


async def run(k, groups, watches=None):
    events, outputs = [], []
    clean = await k.run_batch(
        groups,
        lambda cid, event, payload: events.append((cid, event, payload)),
        lambda cid, kind, content: outputs.append((cid, kind, content)),
        watches=watches,
    )
    return clean, events, outputs


def ends(events):
    return {cid: payload.get("ok") for cid, event, payload in events if event == "end"}


def text(outputs, cid):
    return "".join(c.get("text", "") for who, kind, c in outputs if who == cid and kind == "stream")


def test_group_runs_concurrently():
    async def test(k):
        group = [(f"c{i}", "import time\ntime.sleep(0.5)", []) for i in range(4)]
        start = time.perf_counter()
        clean, events, _ = await run(k, [group])
        assert time.perf_counter() - start < 1.4
        assert clean and ends(events) == {f"c{i}": True for i in range(4)}

    with_kernel(test)


def test_group_outputs_routed():
    async def test(k):
        code = "import time, sys\nfor _ in range(50):\n    print('{0}')\n    time.sleep(0.001)\nsys.stderr.write('{0}-err\\n')"
        group = [(name, code.format(name), []) for name in ("p", "q", "r")]
        clean, _, outputs = await run(k, [group])
        assert clean
        for name in ("p", "q", "r"):
            assert text(outputs, name).replace(f"{name}-err\n", "") == f"{name}\n" * 50
            assert f"{name}-err\n" in text(outputs, name)

    with_kernel(test)


def test_group_display_and_last_expression_routed():
    async def test(k):
        group = [("a", "display('shown a')\n40 + 1", []), ("b", "display('shown b')\n'quiet'", []), ("c", "7;", [])]
        _, _, outputs = await run(k, [group])
        plain = lambda cid: [c["data"]["text/plain"] for who, kind, c in outputs if who == cid and "data" in c]
        assert plain("a") == ["'shown a'", "41"]
        assert plain("b") == ["'shown b'"]
        assert plain("c") == []

    with_kernel(test)


def test_group_error_isolated_and_stops_after_the_group():
    async def test(k):
        group = [("a", "x1 = 1", []), ("b", "1/0", []), ("c", "x3 = 3", [])]
        clean, events, outputs = await run(k, [group, [("d", "x4 = 4", [])]])
        assert clean
        assert ends(events) == {"a": True, "b": False, "c": True}
        assert any(who == "b" and kind == "error" for who, kind, _ in outputs)
        assert [r[0] for r in await k.variables({"x1", "x3", "x4"})] == ["x1", "x3"]

    with_kernel(test)


def test_group_namespace_is_shared():
    async def test(k):
        await run(k, [[("s", "base = 10", [])]])
        clean, events, _ = await run(k, [[("a", "a = base + 1", []), ("b", "b = base + 2", [])]])
        assert ends(events) == {"a": True, "b": True}
        assert await k.variables({"a", "b"}) == [["a", "int", "11", "", "data"], ["b", "int", "12", "", "data"]]

    with_kernel(test)


def test_group_interrupt():
    async def test(k):
        group = [("a", "while True:\n    pass", []), ("b", "while True:\n    pass", [])]
        task = asyncio.create_task(run(k, [group]))
        await asyncio.sleep(0.5)
        await k.interrupt()
        clean, events, outputs = await asyncio.wait_for(task, 10)
        assert clean and ends(events) == {"a": False, "b": False}
        assert {who for who, kind, c in outputs if kind == "error" and c.get("ename") == "KeyboardInterrupt"} == {"a", "b"}
        assert await k.execute("1 + 1")

    with_kernel(test)


def test_no_threads_left():
    async def test(k):
        await run(k, [[("t", "import threading\nbefore = threading.active_count()", [])]])
        await run(k, [[("a", "x = 1", []), ("b", "y = 2", [])]])
        await run(k, [[("u", "after = threading.active_count()", [])]])
        rows = {r[0]: r[2] for r in await k.variables({"before", "after"})}
        assert rows["before"] == rows["after"]

    with_kernel(test)


def test_blas_limits_restored():
    pytest.importorskip("threadpoolctl")

    async def test(k):
        await run(k, [[("t", "import numpy\nfrom threadpoolctl import threadpool_info\nbefore = [i['num_threads'] for i in threadpool_info()]", [])]])
        await run(k, [[("a", "x = numpy.ones(3).sum()", []), ("b", "y = numpy.ones(3).sum()", [])]])
        await run(k, [[("u", "same = [i['num_threads'] for i in threadpool_info()] == before", [])]])
        assert [r[2] for r in await k.variables({"same"})] == ["True"]

    with_kernel(test)


def test_race_detected():
    async def test(k):
        await run(k, [[("s", "xs = []\nys = [1]", [])]])
        group = [("a", "getattr(xs, 'app' + 'end')(1)", []), ("b", "n = len(xs) + len(ys)", [])]
        _, events, _ = await run(k, [group], watches=[["xs", "ys"]])
        races = [payload for cid, event, payload in events if event == "race"]
        assert [r["names"] for r in races] == [["xs"]]

    with_kernel(test)
