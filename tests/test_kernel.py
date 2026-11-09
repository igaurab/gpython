import asyncio
from pathlib import Path

from gpython.kernel import Kernel


def test_execute_outputs_variables_and_delete():
    async def main():
        k = Kernel()
        await k.start()
        try:
            outputs = []
            ok = await k.execute("x = 41\nprint('hi')\nx + 1", lambda kind, c: outputs.append((kind, c)))
            assert ok
            assert ("stream", "hi\n") in [(kd, c.get("text")) for kd, c in outputs]
            assert any(kd == "execute_result" and c["data"]["text/plain"] == "42" for kd, c in outputs)

            assert await k.variables({"x", "missing"}) == [["x", "int", "41", "", "data"]]
            await k.delete({"x"})
            assert await k.variables({"x"}) == []

            errors = []
            assert not await k.execute("1/0", lambda kind, c: errors.append(kind))
            assert "error" in errors
        finally:
            await k.shutdown()

    asyncio.run(main())


def test_marimo_shim_renders_markdown():
    async def main():
        k = Kernel()
        await k.start()
        try:
            outputs = []
            code = "import marimo as mo\nmo.vstack([mo.md('# T'), mo.Html('<p>x <code>y</code></p>')])"
            assert await k.execute(code, lambda kind, c: outputs.append((kind, c)))
            data = next(c["data"] for kd, c in outputs if kd == "execute_result")
            assert "# T" in data["text/markdown"] and "`y`" in data["text/markdown"]

            errors = []
            assert await k.execute(
                "app = mo.App(width='medium')\n@app.cell(hide_code=True)\ndef f(): return 1\n"
                "@app.class_definition\nclass C: pass\napp.run()\nassert f() == 1 and C",
                lambda kind, c: errors.append(c),
            ), errors
            names = []
            assert not await k.execute("mo.ui.slider(1, 2)", lambda kind, c: names.append(c.get("ename")))
            assert "NotImplementedError" in names
        finally:
            await k.shutdown()

    asyncio.run(main())


def test_run_batch_runs_cells_in_order_and_stops_at_the_first_failure():
    async def main():
        k = Kernel()
        await k.start()
        try:
            events, outputs = [], []
            cells = [
                ("a", "x = 1\nprint('one')\nx", []),
                ("b", "import sys\nsys.stderr.write('err\\n')\ny = x + 1\ny", ["y"]),
                ("c", "1/0", []),
                ("d", "print('never')", []),
            ]
            clean = await k.run_batch(
                [[cell] for cell in cells],
                lambda cid, event, payload: events.append((cid, event, payload.get("ok"))),
                lambda cid, kind, content: outputs.append((cid, kind, content.get("text") or content.get("data", {}).get("text/plain") or content.get("ename"))),
            )
            assert clean
            assert events == [("a", "begin", None), ("a", "end", True), ("b", "begin", None), ("b", "end", True), ("c", "begin", None), ("c", "end", False)]
            assert outputs == [
                ("a", "stream", "one\n"), ("a", "execute_result", "1"), ("b", "stream", "err\n"), ("b", "execute_result", "2"),
                ("c", "error", "ZeroDivisionError"),
            ]
            assert await k.variables({"x", "y"}) == [["x", "int", "1", "", "data"], ["y", "int", "2", "", "data"]]
            await k.run_batch([[("e", "x = 5", ["x", "y"])]], lambda *a: None, lambda *a: None)
            assert await k.variables({"x", "y"}) == [["x", "int", "5", "", "data"]]
            assert await k.execute("import asyncio\nawait asyncio.sleep(0)\nx", lambda kind, c: outputs.append(("f", kind, c.get("data", {}).get("text/plain"))))
            assert outputs[-1] == ("f", "execute_result", "5")
        finally:
            await k.shutdown()

    asyncio.run(main())


def test_run_batch_interrupt_stops_a_blocking_cell_and_the_rest_of_the_batch():
    async def main():
        k = Kernel()
        await k.start()
        try:
            events, outputs = [], []
            batch = k.run_batch(
                [[("a", "import time\nprint('start')\ntime.sleep(30)", [])], [("b", "print('b')", [])]],
                lambda cid, event, payload: events.append((cid, event, payload.get("ok"))),
                lambda cid, kind, content: outputs.append((cid, kind, content.get("text") or content.get("ename"))),
            )
            task = asyncio.create_task(batch)
            for _ in range(200):
                await asyncio.sleep(0.05)
                if ("a", "stream", "start\n") in outputs:
                    break
            await k.interrupt()
            assert await asyncio.wait_for(task, 10)
            assert events == [("a", "begin", None), ("a", "end", False)]
            assert ("a", "error", "KeyboardInterrupt") in outputs and not any(cid == "b" for cid, _, _ in outputs)
        finally:
            await k.shutdown()

    asyncio.run(main())


def test_a_kernel_spawned_before_the_ui_is_adopted_and_stays_manageable(tmp_path):
    from gpython import envs, prespawn

    async def main():
        file = tmp_path / "nb.py"
        file.write_text("# %%\nx = 1\n")
        spawned = prespawn.spawn(envs.builtin(), file)
        prespawn._pending = spawned
        k = Kernel(file=file, env=envs.builtin())
        try:
            await k.start()
            assert k.manager.provisioner.process is spawned.process and prespawn._pending is None
            outputs = []
            assert await k.execute("import os\nprint(os.getcwd())", lambda kind, c: outputs.append(c.get("text", "")))
            assert "".join(outputs).strip() == str(file.parent.resolve())
            task = asyncio.create_task(k.execute("import time\ntime.sleep(30)", None))
            await asyncio.sleep(0.5)
            await k.interrupt()
            assert not await asyncio.wait_for(task, 10)
            await k.restart()
            assert await k.execute("1 + 1", None) and k.manager.provisioner.process is not spawned.process
            assert spawned.process.poll() is not None
        finally:
            await k.shutdown()
        assert not await k.manager.is_alive()

    asyncio.run(main())


def test_a_spawn_for_another_env_or_file_is_discarded():
    from gpython import envs, prespawn

    async def main():
        spawned = prespawn.spawn(envs.builtin(), None)
        prespawn._pending = spawned
        assert prespawn.take(envs.builtin(), Path("/tmp/other.py")) is None
        assert spawned.process.poll() is not None and not spawned.connection_file.exists()
        assert prespawn.take(envs.builtin(), None) is None

    asyncio.run(main())
