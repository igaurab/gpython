import asyncio

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

            assert await k.variables({"x", "missing"}) == [["x", "int", "41"]]
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
