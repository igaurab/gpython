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
