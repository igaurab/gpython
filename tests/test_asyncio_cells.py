"""asyncio.run, run_until_complete and asyncio.Runner work inside a cell (kernel_asyncio.py)."""

import asyncio
import time

import pytest

from gpython.kernel import Kernel

LIBRARY = '''
import asyncio
import contextvars

TAG = contextvars.ContextVar("TAG", default="unset")


async def fetch(port):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"ping\\n")
    await writer.drain()
    line = await reader.readline()
    writer.close()
    await writer.wait_closed()
    return line.decode().strip()


def sync_fetch(port):
    return asyncio.run(fetch(port))


async def three_deep(port, depth=3):
    if depth == 1:
        return await fetch(port)
    return asyncio.run(three_deep(port, depth - 1))


class Shared:
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer

    @classmethod
    async def open(cls, port):
        return cls(*await asyncio.open_connection("127.0.0.1", port))

    async def ping(self):
        self.writer.write(b"ping\\n")
        await self.writer.drain()
        return (await self.reader.readline()).decode().strip()


async def tick(counts):
    while True:
        counts.append(1)
        await asyncio.sleep(0.02)


async def tagged():
    seen = TAG.get()
    TAG.set("inside")
    return asyncio.current_task() is not None and seen


async def boom():
    raise ValueError("boom")


async def ask():
    return input("name? ")
'''


def with_kernel(tmp_path, test):
    async def pong(reader, writer):
        while await reader.readline():
            writer.write(b"pong\n")
            await writer.drain()
        writer.close()

    async def main():
        server = await asyncio.start_server(pong, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        (tmp_path / "asynclib.py").write_text(LIBRARY)
        k = Kernel()
        await k.start()
        try:
            setup = f"import sys, asyncio, time\nsys.path.insert(0, {str(tmp_path)!r})\nimport asynclib as lib\nPORT = {port}"
            assert (await cell(k, setup))[0]
            await test(k)
        finally:
            await k.shutdown()
            server.close()

    asyncio.run(main())


async def cell(k, code):
    outputs = []
    ok = await asyncio.wait_for(k.execute(code, lambda kind, content: outputs.append((kind, content))), 30)
    said = []
    for kind, content in outputs:
        if kind == "error":
            said.append(f"{content['ename']}: {content['evalue']}")
        elif kind == "execute_result":
            said.append(content["data"]["text/plain"])
        elif kind == "stream":
            said.append(content["text"].strip())
    return ok, " | ".join(said)


async def batch(k, groups, answer=None):
    events, outputs = [], []

    def asked(cid, prompt, password):
        k.reply(answer)

    await k.run_batch(
        groups,
        lambda cid, kind, payload: events.append((cid, kind, payload.get("ok"))),
        lambda cid, kind, content: outputs.append((cid, kind, content)),
        asked if answer else None,
    )
    return {cid: ok for cid, kind, ok in events if kind == "end"}, outputs


def test_asyncio_run_in_a_cell_a_library_and_an_async_cell(tmp_path):
    async def test(k):
        assert await cell(k, "asyncio.run(lib.fetch(PORT))") == (True, "'pong'")
        assert await cell(k, "lib.sync_fetch(PORT)") == (True, "'pong'")
        assert await cell(k, "asyncio.run(lib.three_deep(PORT))") == (True, "'pong'")
        assert await cell(k, "async def outer():\n    return asyncio.run(lib.fetch(PORT))\nawait outer()") == (True, "'pong'")
        assert await cell(k, "await lib.fetch(PORT)") == (True, "'pong'")

    with_kernel(tmp_path, test)


def test_run_until_complete_and_runner(tmp_path):
    async def test(k):
        assert await cell(k, "asyncio.get_event_loop().run_until_complete(lib.fetch(PORT))") == (True, "'pong'")
        fresh = "fresh = asyncio.new_event_loop()\ntry:\n    print(fresh.run_until_complete(lib.fetch(PORT)))\nfinally:\n    fresh.close()"
        assert await cell(k, fresh) == (True, "pong")
        assert await cell(k, "with asyncio.Runner() as runner:\n    print(runner.run(lib.fetch(PORT)))") == (True, "pong")
        assert await cell(k, "await lib.fetch(PORT)") == (True, "'pong'")

    with_kernel(tmp_path, test)


def test_the_cell_and_the_coroutine_share_one_loop(tmp_path):
    async def test(k):
        assert await cell(k, "conn = await lib.Shared.open(PORT)\nawait conn.ping()") == (True, "'pong'")
        assert await cell(k, "asyncio.run(conn.ping())") == (True, "'pong'")
        assert await cell(k, "await conn.ping()") == (True, "'pong'")
        assert await cell(k, "counts = []\nbg = asyncio.create_task(lib.tick(counts))\nawait asyncio.sleep(0.1)\nlen(counts) > 2") == (True, "True")
        assert await cell(k, "seen = len(counts)\nasyncio.run(asyncio.sleep(0.3))\nlen(counts) - seen > 5") == (True, "True")
        assert await cell(k, "await asyncio.sleep(0.1)\nalive = not bg.done()\nbg.cancel()\nalive") == (True, "True")

    with_kernel(tmp_path, test)


def test_tasks_context_and_errors_are_as_in_a_script(tmp_path):
    async def test(k):
        assert await cell(k, "lib.TAG.set('cell')\n(asyncio.run(lib.tagged()), lib.TAG.get())") == (True, "('cell', 'cell')")
        same = "before = asyncio.current_task()\nasyncio.run(asyncio.sleep(0))\nawait asyncio.sleep(0)\nbefore is not None and before is asyncio.current_task()"
        assert await cell(k, same) == (True, "True")
        assert await cell(k, "asyncio.run(lib.boom())") == (False, "ValueError: boom")
        assert await cell(k, "asyncio.run(lib.fetch(PORT))") == (True, "'pong'")
        left = "await asyncio.sleep(0.05)\nlen([t for t in asyncio.all_tasks() if t is not asyncio.current_task() and 'boom' in repr(t)])"
        assert await cell(k, left) == (True, "0")

    with_kernel(tmp_path, test)


def test_run_all_routes_output_and_input_and_groups_still_run_on_threads(tmp_path):
    async def test(k):
        ends, outputs = await batch(k, [[("a", "async def talk():\n    print('from a')\nasyncio.run(talk())", [])], [("b", "print('from b')", [])]])
        assert ends == {"a": True, "b": True}
        texts = [(cid, content["text"].strip()) for cid, kind, content in outputs if kind == "stream"]
        assert texts == [("a", "from a"), ("b", "from b")]
        ends, outputs = await batch(k, [[("a", "asyncio.run(lib.ask())", [])]], answer="gp")
        assert ends == {"a": True}
        assert any(kind == "execute_result" and content["data"]["text/plain"] == "'gp'" for _, kind, content in outputs)
        ends, _ = await batch(k, [[("a", "asyncio.run(lib.fetch(PORT))", []), ("b", "asyncio.run(lib.fetch(PORT))", [])]])
        assert ends == {"a": True, "b": True}

    with_kernel(tmp_path, test)


def test_interrupt_stops_asyncio_run_and_the_cells_after_it(tmp_path):
    async def test(k):
        events = []
        cells = [[("s", "asyncio.run(asyncio.gather(*(asyncio.sleep(30) for _ in range(5))))", [])], [("next", "print('never')", [])]]
        task = asyncio.create_task(k.run_batch(cells, lambda cid, kind, payload: events.append((cid, kind, payload.get("ok"))), lambda *a: None))
        await asyncio.sleep(1)
        began = time.perf_counter()
        await k.interrupt()
        await asyncio.wait_for(task, 10)
        assert time.perf_counter() - began < 5
        assert events == [("s", "begin", None), ("s", "end", False)]
        assert await cell(k, "asyncio.run(lib.fetch(PORT))") == (True, "'pong'")
        left = "await asyncio.sleep(0.05)\nlen([t for t in asyncio.all_tasks() if t is not asyncio.current_task() and 'sleep' in repr(t)])"
        assert await cell(k, left) == (True, "0")

    with_kernel(tmp_path, test)


async def interrupted(k, code):
    events, errors = [], []
    cells = [[("s", code, [])], [("next", "print('never')", [])]]
    task = asyncio.create_task(k.run_batch(
        cells,
        lambda cid, kind, payload: events.append((cid, kind, payload.get("ok"))),
        lambda cid, kind, content: errors.append(content.get("ename")) if kind == "error" else None,
    ))
    await asyncio.sleep(1)
    began = time.perf_counter()
    await k.interrupt()
    await asyncio.wait_for(task, 10)
    assert time.perf_counter() - began < 5
    assert events == [("s", "begin", None), ("s", "end", False)]
    return errors


def test_interrupt_stops_a_cell_that_sits_in_await(tmp_path):
    async def test(k):
        assert await interrupted(k, "await asyncio.sleep(30)") == ["CancelledError"]
        assert await cell(k, "await lib.fetch(PORT)") == (True, "'pong'")
        outputs = []
        task = asyncio.create_task(k.execute("await asyncio.sleep(30)", lambda kind, content: outputs.append(kind)))
        await asyncio.sleep(1)
        await k.interrupt()
        assert await asyncio.wait_for(task, 10) is False
        assert await cell(k, "asyncio.run(lib.fetch(PORT))") == (True, "'pong'")

    with_kernel(tmp_path, test)


def test_interrupt_stops_asyncio_run_inside_a_cell_that_awaits(tmp_path):
    async def test(k):
        code = "async def outer():\n    asyncio.run(asyncio.sleep(30))\nawait outer()"
        assert await interrupted(k, code) == ["KeyboardInterrupt"]
        assert await cell(k, "asyncio.run(lib.fetch(PORT))") == (True, "'pong'")
        assert await cell(k, "await lib.fetch(PORT)") == (True, "'pong'")

    with_kernel(tmp_path, test)


def test_anyio_run_in_a_cell(tmp_path):
    pytest.importorskip("anyio")

    async def test(k):
        code = "import anyio\nasync def double(x):\n    await anyio.sleep(0.01)\n    return x * 2\nanyio.run(double, 21)"
        assert await cell(k, code) == (True, "42")
        group = "async def many():\n    seen = []\n    async with anyio.create_task_group() as tg:\n        for n in range(3):\n            tg.start_soon(lambda n=n: seen.append(n) or anyio.sleep(0))\n    return sorted(seen)\nanyio.run(many)"
        assert await cell(k, group) == (True, "[0, 1, 2]")
        assert await cell(k, "from anyio import run as plain\nplain(double, 4)") == (True, "8")
        assert await cell(k, "async def outer():\n    return anyio.run(double, 5)\nawait outer()") == (True, "10")
        assert await cell(k, "(asyncio.run(double(3)), await double(2))") == (True, "(6, 4)")

    with_kernel(tmp_path, test)
