"""Hints under a traceback: asyncio.run() inside the kernel's running event loop."""

import asyncio

from gpython import hints, notebook
from gpython.app import GPython
from gpython.notebook import CellSpec

NESTED = {"ename": "RuntimeError", "evalue": "asyncio.run() cannot be called from a running event loop"}


def test_the_cells_own_asyncio_run_becomes_await():
    hint = hints.event_loop(NESTED, "import asyncio\nresult = asyncio.run(fetch(url, retries=2))\n")
    assert hint.endswith("write await fetch(url, retries=2) instead")


def test_asyncio_run_inside_a_library_says_to_await_its_coroutine():
    hint = hints.event_loop(NESTED, 'f = DocumentFolder("p").run_sync()\n')
    assert "already runs an event loop" in hint and "await the coroutine" in hint


def test_other_errors_and_broken_source_get_no_or_the_general_hint():
    assert hints.event_loop({"ename": "RuntimeError", "evalue": "something else"}, "asyncio.run(x())") is None
    assert hints.event_loop({"ename": "ValueError", "evalue": NESTED["evalue"]}, "asyncio.run(x())") is None
    assert hints.event_loop(None, "") is None
    assert "await the coroutine" in hints.event_loop(NESTED, "def (:")


def test_a_cell_that_calls_asyncio_run_shows_the_hint(tmp_path):
    path = tmp_path / "nb.py"
    source = "import asyncio\n\nasync def main():\n    return 42\n\nanswer = asyncio.run(main())"
    notebook.save(path, [CellSpec(source)])

    async def main():
        app = GPython(path)
        async with app.run_test(size=(120, 40)) as pilot:
            await app.loaded.wait()
            await asyncio.wait_for(app.kernel.ready.wait(), 60)
            cell = app.cells()[0]
            cell.focus()
            await pilot.press("ctrl+e")
            for _ in range(3):
                await pilot.pause()
                await app.workers.wait_for_complete()
            assert cell.status == "error"
            assert "write await main() instead" in cell.output_text
            assert ":debug" not in cell.output_text

    asyncio.run(asyncio.wait_for(main(), 90))
