"""The agent panel (:agent) with tests/fake_agent.py as the agent."""

import asyncio
import json
import shlex
import sys
from pathlib import Path

from textual.widgets import Input

from gpython import agentlink, agents, config, notebook
from gpython.agent import AgentPanel, diff_picture, tool_picture
from gpython.app import GPython
from gpython.notebook import CellSpec

FAKE = f"{shlex.quote(sys.executable)} {shlex.quote(str(Path(__file__).parent / 'fake_agent.py'))}"
CELLS = [CellSpec("a = 1"), CellSpec("b = a + 1"), CellSpec("print(b)")]


def ui(tmp_path, test, agent=FAKE, steps="keep"):
    path = tmp_path / "nb.py"
    notebook.save(path, CELLS)
    config.save({"agent.default": "fake", "agents.fake": agent, "agent.tools": steps, "agent.thoughts": steps})

    async def main():
        app = GPython(path)
        async with app.run_test(size=(170, 45)) as pilot:
            await app.loaded.wait()
            await pilot.pause()
            await test(app, pilot)

    asyncio.run(asyncio.wait_for(main(), 120))


async def until(pilot, condition, seconds=20):
    for _ in range(int(seconds * 20)):
        if condition():
            return
        await pilot.pause(0.05)
    raise AssertionError("the condition did not become true")


async def open_panel(app, pilot) -> AgentPanel:
    await app._ex_agent("")
    panel = app.agent_panel
    await until(pilot, lambda: panel.state == "ready")
    return panel


async def ask(pilot, panel, text):
    panel.input.value = text
    await pilot.press("enter")
    await until(pilot, lambda: panel.state == "ready" and panel.entries[-1].kind != "user")
    panel.flush()


def drawn(renderable, width: int = 60) -> str:
    from io import StringIO

    from rich.console import Console

    out = StringIO()
    Console(file=out, width=width, color_system=None).print(renderable)
    return "\n".join(line.rstrip() for line in out.getvalue().splitlines())


def shown(panel) -> list[tuple[str, str]]:
    return [(entry.kind, entry.text) for entry in panel.entries]


def test_agent_opens_the_panel_starts_the_agent_and_takes_the_focus(tmp_path):
    async def test(app, pilot):
        assert app.agent_panel is None
        panel = await open_panel(app, pilot)
        assert panel.display and app.focused is panel.input and app.ui_mode == "agent"
        assert panel.agent.name == "fake" and panel.session.agent_name == "Fake Agent"
        await pilot.press("escape")
        await pilot.pause()
        assert app.focused is not panel.input and panel.display and app.ui_mode != "agent"
        app.focus_window("right")
        await pilot.pause()
        assert app.focused is panel.input
        app.focus_window("left")
        await pilot.pause()
        assert not app.in_agent_panel
        await app._ex_agent("")
        await pilot.pause()
        assert app.focused is panel.input
        await app._ex_agent("")
        await pilot.pause()
        assert not panel.display and not app.in_agent_panel

    ui(tmp_path, test)


def test_a_prompt_shows_the_user_line_the_thought_and_the_answer(tmp_path):
    async def test(app, pilot):
        panel = await open_panel(app, pilot)
        await ask(pilot, panel, "echo hello **there**")
        assert shown(panel) == [("user", "echo hello **there**"), ("thought", "thinking about it"), ("agent", "hello **there**")]
        assert panel.input.value == ""
        assert all(not entry.dirty for entry in panel.entries)
        assert len(panel.log_box.children) == 3

    ui(tmp_path, test)


def test_the_agent_is_told_which_notebook_is_open_once_with_the_how_to(tmp_path):
    async def test(app, pilot):
        panel = await open_panel(app, pilot)
        await ask(pilot, panel, "first")
        first = panel.entries[-1].text
        assert first.startswith("first | [gpython] Open notebook: ") and "nb.py, 3 cells, the cursor is in c1" in first
        assert "cells_read" in first and "Do not edit the notebook's file" in first
        await ask(pilot, panel, "second")
        second = panel.entries[-1].text
        assert "3 cells" in second and "cells_read" not in second

    ui(tmp_path, test, agent=FAKE + " --context")


def test_the_session_gets_the_notebook_tools_as_an_mcp_server(tmp_path):
    async def test(app, pilot):
        panel = await open_panel(app, pilot)
        await ask(pilot, panel, "mcp")
        servers = json.loads(panel.entries[-1].text)
        link = await app.agent_link()
        assert servers[0]["name"] == "gpython" and servers[0]["env"] == [{"name": agentlink.ENV, "value": link.address}]

    ui(tmp_path, test)


def test_a_tool_call_is_one_entry_that_follows_its_state_and_shows_the_diff(tmp_path):
    async def test(app, pilot):
        panel = await open_panel(app, pilot)
        await ask(pilot, panel, "tool")
        kinds = [entry.kind for entry in panel.entries]
        assert kinds == ["user", "tool", "agent"]
        picture = drawn(tool_picture(panel.entries[1].data))
        assert picture.startswith("● Edit notes.txt") and "-two" in picture and "+three" in picture
        await ask(pilot, panel, "plan")
        assert panel.entries[-1].kind == "plan" and "☑ Read the cells" in panel.entries[-1].renderable().plain

    ui(tmp_path, test)


def test_a_permission_question_is_answered_with_a_number(tmp_path):
    async def test(app, pilot):
        panel = await open_panel(app, pilot)
        panel.input.value = "ask"
        await pilot.press("enter")
        await until(pilot, lambda: panel.waiting_permission is not None)
        assert panel.state == "working"
        await pilot.press("7")
        assert panel.waiting_permission is not None and panel.input.value == ""
        await pilot.press("1")
        await until(pilot, lambda: panel.state == "ready")
        panel.flush()
        assert panel.waiting_permission is None
        assert panel.entries[-2].kind == "permission" and panel.entries[-2].data["answer"] == "Allow"
        assert panel.entries[-1].text == "chose yes"

    ui(tmp_path, test)


def test_escape_refuses_a_permission_and_ctrl_c_stops_a_turn(tmp_path):
    async def test(app, pilot):
        panel = await open_panel(app, pilot)
        panel.input.value = "ask"
        await pilot.press("enter")
        await until(pilot, lambda: panel.waiting_permission is not None)
        await pilot.press("escape")
        await until(pilot, lambda: panel.state == "ready")
        assert panel.entries[-1].text == "chose cancelled" and app.focused is panel.input
        panel.input.value = "wait"
        await pilot.press("enter")
        await until(pilot, lambda: panel.state == "working" and panel.session.prompting)
        panel.input.value = "more"
        await pilot.press("enter")
        assert panel.input.value == "more" and len([e for e in panel.entries if e.kind == "user"]) == 2
        await pilot.press("ctrl+c")
        await until(pilot, lambda: panel.state == "ready")
        assert panel.entries[-1].text == "Stopped."

    ui(tmp_path, test)


def test_the_app_keys_do_not_act_while_the_user_types_to_the_agent(tmp_path):
    async def test(app, pilot):
        panel = await open_panel(app, pilot)
        await pilot.press("a", "b", "ctrl+d", "ctrl+n", "backspace")
        await pilot.pause()
        assert len(app.pane.states) == 3 and app.focused is panel.input and panel.input.value == "a"

    ui(tmp_path, test)


def test_a_line_typed_before_the_agent_is_ready_is_sent_when_it_is(tmp_path):
    async def test(app, pilot):
        await app._ex_agent("")
        panel = app.agent_panel
        panel.input.value = "echo early"
        await pilot.press("enter")
        await until(pilot, lambda: panel.state == "ready" and panel.entries[-1].kind == "agent")
        assert panel.entries[-1].text == "early"

    ui(tmp_path, test)


def test_an_agent_that_dies_is_reported_and_agent_new_starts_it_again(tmp_path):
    async def test(app, pilot):
        panel = await open_panel(app, pilot)
        panel.input.value = "die"
        await pilot.press("enter")
        await until(pilot, lambda: panel.state == "stopped")
        assert any(entry.kind == "error" and "out of luck" in entry.text for entry in panel.entries)
        await app._ex_agent("new")
        await until(pilot, lambda: panel.state == "ready")
        await ask(pilot, panel, "echo back")
        assert panel.entries[-1].text == "back"

    ui(tmp_path, test)


def test_a_turn_that_ends_without_a_word_is_explained(tmp_path):
    async def test(app, pilot):
        panel = await open_panel(app, pilot)
        await ask(pilot, panel, "silent")
        assert panel.entries[-1].kind == "error" and "said nothing" in panel.entries[-1].text
        await ask(pilot, panel, "echo fine")
        assert panel.entries[-1].text == "fine"

    ui(tmp_path, test)


def test_a_missing_program_a_login_and_an_unknown_name_are_explained(tmp_path):
    async def test(app, pilot):
        await app._ex_agent("")
        panel = app.agent_panel
        await until(pilot, lambda: panel.entries)
        assert panel.state == "off" and "not installed" in panel.entries[-1].text and "gpython-no-such-agent-program" in panel.entries[-1].text
        await app._ex_agent("nobody")
        assert "No agent named nobody" in panel.entries[-1].text
        config.save({"agents.locked": FAKE + " --no-login"})
        await app._ex_agent("locked")
        await until(pilot, lambda: "not logged in" in panel.entries[-1].text)
        assert panel.state == "off"
        await app._ex_agent("stop")
        assert panel.session is None

    ui(tmp_path, test, agent="gpython-no-such-agent-program --acp")


def test_agent_link_opens_the_tools_without_the_panel(tmp_path):
    async def test(app, pilot):
        await app._ex_agent("link")
        assert app.agent_panel is None and app._agent_link.port > 0
        assert agentlink.find_address(Path.cwd()) == app._agent_link.address

    ui(tmp_path, test)


def test_a_diff_shows_only_the_changed_lines_and_says_how_many_more():
    old = "\n".join(f"line {n}" for n in range(40))
    new = "\n".join(f"LINE {n}" for n in range(40))
    picture = diff_picture({"path": "/x/y/notes.py", "oldText": old, "newText": new}).plain
    assert picture.splitlines()[0].strip() == "notes.py" and "more lines" in picture
    assert tool_picture({"title": "Run\n  tests", "status": "failed"}).plain == "✗ Run tests"


def test_a_long_command_is_one_line_cut_at_the_panel_edge():
    command = "mkdir -p /tmp/check && cat > check.py <<'EOF'\n" + "import torch\n" * 80
    assert drawn(tool_picture({"title": command, "status": "completed"}), width=40).splitlines() == ["● mkdir -p /tmp/check && cat > check.py…"]


def test_folded_steps_show_while_the_agent_works_and_leave_one_line_after(tmp_path):
    async def test(app, pilot):
        panel = await open_panel(app, pilot)
        await ask(pilot, panel, "echo hello")
        assert shown(panel) == [("user", "echo hello"), ("agent", "hello")]
        await ask(pilot, panel, "tool")
        await pilot.pause()
        assert shown(panel)[2:] == [("user", "tool"), ("note", "1 tool call"), ("agent", "edited")]
        assert len(panel.log_box.children) == len(panel.entries) == 5
        panel.input.value = "ask"
        await pilot.press("enter")
        await until(pilot, lambda: panel.waiting_permission is not None)
        await pilot.press("1")
        await until(pilot, lambda: panel.state == "ready")
        assert panel.entries[-2].kind == "permission"

    ui(tmp_path, test, steps="fold")


def test_hidden_steps_never_show_and_a_bad_mode_is_fold(tmp_path):
    async def test(app, pilot):
        panel = await open_panel(app, pilot)
        await ask(pilot, panel, "echo hello")
        await ask(pilot, panel, "tool")
        assert shown(panel) == [("user", "echo hello"), ("agent", "hello"), ("user", "tool"), ("agent", "edited")]
        assert len(panel.log_box.children) == 4

    ui(tmp_path, test, steps="hide")
    assert agents.step_mode({"agent.tools": "sometimes"}, "tools") == "fold" and agents.step_mode({}, "thoughts") == "fold"


def test_agent_default_and_the_step_modes_are_set_with_the_command_and_saved(tmp_path):
    async def test(app, pilot):
        await app._ex_agent("default opencode")
        assert config.load()["agent.default"] == "opencode" and app.agent_panel is None
        await app._ex_agent("default nobody")
        assert config.load()["agent.default"] == "opencode"
        await app._ex_agent("default fake")
        panel = await open_panel(app, pilot)
        assert panel.agent.name == "fake" and panel.step_modes == {"tool": "keep", "thought": "keep"}
        await app._ex_agent("tools hide")
        await app._ex_agent("thoughts fold")
        await app._ex_agent("tools sometimes")
        assert config.load()["agent.tools"] == "hide" and panel.step_modes == {"tool": "hide", "thought": "fold"}
        await ask(pilot, panel, "tool")
        assert shown(panel) == [("user", "tool"), ("agent", "edited")]
        await app._ex_agent("default")
        await app._ex_agent("tools")

    ui(tmp_path, test)
    assert agents.default_name({"agent.default": "pi"}) == "pi"


def test_the_panel_is_not_imported_at_start_up():
    import subprocess

    code = "import sys; from gpython.app import GPython; print([m for m in ('gpython.agent', 'gpython.acp', 'gpython.agentlink', 'gpython.notebooktools', 'gpython.mcpserver', 'gpython.agents') if m in sys.modules])"
    assert subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60).stdout.strip() == "[]"
