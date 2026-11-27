"""The notebook tools an agent calls (notebooktools.py), through the agent link and the MCP server."""

import asyncio
import io
import json
import os
import subprocess
import sys

import pytest

from gpython import agentlink, mcpserver, notebook, notebooktools
from gpython.app import GPython
from gpython.notebook import CellSpec
from gpython.notebooktools import ToolError, clipped, parse_cells, plain

CELLS = [
    CellSpec("nums = [1, 3, 5]\nprint('made', len(nums))"),
    CellSpec("total = sum(nums)\ntotal"),
    CellSpec("# Notes\nsome words", kind="markdown"),
    CellSpec("print(nums[7])"),
]


def ui(tmp_path, test, cells=CELLS, kernel=True):
    path = tmp_path / "nb.ipynb"
    notebook.save(path, cells)

    async def main():
        app = GPython(path)
        async with app.run_test(size=(140, 45)) as pilot:
            await app.loaded.wait()
            if kernel:
                await asyncio.wait_for(app.kernel.ready.wait(), 60)
            await pilot.pause()
            await test(app, pilot, notebooktools.NotebookTools(app))

    asyncio.run(asyncio.wait_for(main(), 150))


def text_of(answer) -> str:
    return "\n".join(block["text"] for block in answer["content"] if block["type"] == "text")


def test_cell_numbers_are_parsed_and_checked():
    assert parse_cells(None, 3) == [0, 1, 2]
    assert parse_cells("all", 3) == [0, 1, 2]
    assert parse_cells("2", 3) == [1]
    assert parse_cells("1, 3-4, c2", 5) == [0, 2, 3, 1]
    with pytest.raises(ToolError, match="c1 to c3"):
        parse_cells("4", 3)
    with pytest.raises(ToolError, match="cell numbers"):
        parse_cells("two", 3)


def test_long_output_keeps_its_start_and_its_end():
    text = "start " + "x" * 9000 + " end"
    short = clipped(text, 300)
    assert short.startswith("start ") and short.endswith(" end") and "left out" in short and len(short) < 400
    assert plain("\x1b[31mred\x1b[0m") == "red"


def test_the_tool_list_is_valid_for_mcp():
    names = [spec["name"] for spec in notebooktools.SPECS]
    assert len(names) == len(set(names)) and "cells_read" in names and "cells_run" in names
    for spec in notebooktools.SPECS:
        schema = spec["inputSchema"]
        assert schema["type"] == "object" and set(schema["required"]) <= set(schema["properties"])
        assert hasattr(notebooktools.NotebookTools, f"tool_{spec['name']}")


def test_cells_are_read_with_their_kind_and_source(tmp_path):
    async def test(app, pilot, tools):
        answer = await tools.call("cells_read", {})
        text = text_of(answer)
        assert not answer["isError"]
        assert "4 cells" in text and "== c1 [code] not run" in text and "nums = [1, 3, 5]" in text
        assert "== c3 [markdown]" in text and "some words" in text
        one = text_of(await tools.call("cells_read", {"cells": "2"}))
        assert "total = sum(nums)" in one and "c1" not in one
        assert (await tools.call("cells_read", {"cells": "9"}))["isError"]
        assert (await tools.call("no_such_tool", {}))["isError"]
        listing = text_of(await tools.call("notebooks", {}))
        assert "nb.ipynb" in listing and "4 cells" in listing and listing.startswith("* ")

    ui(tmp_path, test, kernel=False)


def test_an_edit_changes_the_cell_and_one_undo_takes_it_back(tmp_path):
    async def test(app, pilot, tools):
        pane = app.pane
        answer = await tools.call("cell_edit", {"cell": 2, "old": "sum(nums)", "new": "max(nums)"})
        await pilot.pause()
        assert not answer["isError"], answer
        assert pane.states[1].source == "total = max(nums)\ntotal" and pane.dirty
        await tools.call("cell_edit", {"cell": 1, "source": "nums = [2, 4]"})
        await pilot.pause()
        assert pane.states[0].source == "nums = [2, 4]"
        await pane.undo()
        await pilot.pause()
        assert pane.states[0].source == CELLS[0].source
        await pane.undo()
        await pilot.pause()
        assert pane.states[1].source == CELLS[1].source
        refused = await tools.call("cell_edit", {"cell": 1, "old": "no such text", "new": "x"})
        assert refused["isError"] and "occurs 0 times" in text_of(refused)
        assert (await tools.call("cell_edit", {"cell": 1}))["isError"]

    ui(tmp_path, test, kernel=False)


def test_cells_are_added_and_removed_and_the_focus_stays(tmp_path):
    async def test(app, pilot, tools):
        pane = app.pane
        focused = app.focused
        answer = await tools.call("cell_insert", {"after": 1, "source": "extra = 1"})
        await pilot.pause()
        assert "Added c2" in text_of(answer) and [s.source for s in pane.states][1] == "extra = 1"
        assert len(pane.states) == 5 and pane.states[4].label == "c5"
        await tools.call("cell_insert", {"after": 0, "source": "# first", "kind": "markdown"})
        await tools.call("cell_insert", {"source": "last = 1"})
        await pilot.pause()
        assert pane.states[0].kind == "markdown" and pane.states[0].source == "# first"
        assert pane.states[-1].source == "last = 1" and len(pane.states) == 7
        removed = await tools.call("cell_delete", {"cell": 1})
        await pilot.pause()
        await pilot.pause()
        assert "Removed c1" in text_of(removed) and len(pane.states) == 6
        assert pane.states[1].source == "extra = 1"
        assert app.focused is focused
        await pane.undo()
        await pilot.pause()
        assert pane.states[0].source == "# first"
        assert (await tools.call("cell_insert", {"after": 99, "source": "x"}))["isError"]

    ui(tmp_path, test, kernel=False)


def test_a_run_waits_and_answers_with_outputs_and_the_error(tmp_path):
    async def test(app, pilot, tools):
        answer = await tools.call("cells_run", {"cells": "1-2"})
        text = text_of(answer)
        assert "== c1 [code] ok" in text and "made 3" in text and "\n9" in text
        failed = text_of(await tools.call("cells_run", {"cells": "4"}))
        assert "Failed: c4" in failed and "IndexError" in failed and "\x1b" not in failed
        variables = text_of(await tools.call("variables", {}))
        assert "nums: list" in variables and "total: int" in variables and "(from c2)" in variables
        assert text_of(await tools.call("variables", {"filter": "tot"})).startswith("total")
        assert "[1, 3, 5]" in text_of(await tools.call("variable_value", {"name": "nums"}))
        assert text_of(await tools.call("python_eval", {"code": "print(len(nums))\nnums[-1] * 2"})) == "3\n10"
        assert "ZeroDivisionError" in text_of(await tools.call("python_eval", {"code": "1/0"}))
        await tools.call("cell_edit", {"cell": 1, "source": "nums = [10, 20]\nprint('made', len(nums))"})
        await pilot.pause()
        assert "out of date" in text_of(await tools.call("cells_read", {"cells": "1"}))
        stale = text_of(await tools.call("cells_run", {"cells": "stale"}))
        assert "made 2" in stale
        assert "30" in text_of(await tools.call("cells_run", {"cells": "2"}))

    ui(tmp_path, test)


def test_the_tools_added_by_insert_can_run_at_once_and_save(tmp_path):
    async def test(app, pilot, tools):
        answer = text_of(await tools.call("cell_insert", {"source": "print('new cell ran')", "run": True}))
        assert "Added c5" in answer and "new cell ran" in answer
        assert "Saved" in text_of(await tools.call("notebook_save", {}))
        assert "new cell ran" in app.pane.path.read_text()
        assert not app.pane.dirty

    ui(tmp_path, test)


def test_a_plot_comes_back_as_a_picture(tmp_path):
    png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    code = f"from IPython.display import Image, display\nimport base64\ndisplay(Image(data=base64.b64decode('{png}'), format='png'))"

    async def test(app, pilot, tools):
        answer = await tools.call("cells_run", {"cells": "1"})
        pictures = [block for block in answer["content"] if block["type"] == "image"]
        assert len(pictures) == 1 and pictures[0]["mimeType"] == "image/png" and pictures[0]["data"] == png

    ui(tmp_path, test, cells=[CellSpec(code)])


def test_the_link_answers_only_with_the_token_and_the_mcp_server_passes_calls(tmp_path, monkeypatch):
    async def test(app, pilot, tools):
        link = await app.agent_link()
        assert link is await app.agent_link()
        assert agentlink.live_links()[0]["address"] == link.address
        entry = link.mcp_server()
        assert entry["name"] == "gpython" and entry["env"] == [{"name": agentlink.ENV, "value": link.address}]

        reader, writer = await asyncio.open_connection("127.0.0.1", link.port)
        writer.write(b'{"token": "wrong"}\n{"id": 1, "tool": "notebooks", "arguments": {}}\n')
        await writer.drain()
        assert await reader.read() == b""

        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "cells_read", "arguments": {"cells": "2"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "resources/list"},
        ]
        server = await asyncio.create_subprocess_exec(
            *([entry["command"]] + entry["args"]), env={**os.environ, agentlink.ENV: link.address},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        )
        answers = {}
        for request in requests:
            server.stdin.write(json.dumps(request).encode() + b"\n")
        await server.stdin.drain()
        while len(answers) < 4:
            answer = json.loads(await asyncio.wait_for(server.stdout.readline(), 20))
            answers[answer["id"]] = answer
        server.stdin.close()
        await server.wait()
        assert answers[1]["result"]["serverInfo"]["name"] == "gpython" and "instructions" in answers[1]["result"]
        assert [tool["name"] for tool in answers[2]["result"]["tools"]] == [spec["name"] for spec in notebooktools.SPECS]
        assert "total = sum(nums)" in answers[3]["result"]["content"][0]["text"] and not answers[3]["result"]["isError"]
        assert answers[4]["error"]["code"] == -32601

    ui(tmp_path, test, kernel=False)
    assert agentlink.live_links() == []


def test_the_mcp_server_says_what_to_do_when_gpython_is_not_open(monkeypatch):
    monkeypatch.delenv(agentlink.ENV, raising=False)
    out = io.StringIO()
    server = mcpserver.Server(out)
    answer = server.link.call("cells_read", {})
    assert answer["isError"] and ":agent link" in answer["content"][0]["text"]
    server.serve(['{"jsonrpc": "2.0", "id": 7, "method": "ping"}', "not json", ""])
    assert json.loads(out.getvalue()) == {"jsonrpc": "2.0", "id": 7, "result": {}}


def test_gpython_mcp_is_a_command():
    request = '{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}\n'
    done = subprocess.run([sys.executable, "-c", "import sys; sys.argv = ['gpython', 'mcp']; from gpython import main; main()"], input=request, capture_output=True, text=True, timeout=30)
    assert len(json.loads(done.stdout)["result"]["tools"]) == len(notebooktools.SPECS)
