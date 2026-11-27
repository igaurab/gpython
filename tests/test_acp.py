"""The ACP client (acp.py) against tests/fake_agent.py, a program that speaks the protocol."""

import asyncio
import sys
from pathlib import Path

import pytest

from gpython import acp, agents

FAKE = [sys.executable, str(Path(__file__).parent / "fake_agent.py")]


class Recorder(acp.Listener):
    def __init__(self, choose=None):
        self.updates = []
        self.asked = []
        self.exited = None
        self.choose = choose

    def session_update(self, update):
        self.updates.append(update)

    async def permission(self, tool_call, options):
        self.asked.append((tool_call, options))
        return self.choose

    def agent_exited(self, code, stderr_tail):
        self.exited = (code, stderr_tail)

    def text(self, kind="agent_message_chunk"):
        return "".join(u["content"]["text"] for u in self.updates if u["sessionUpdate"] == kind)


def session_test(test, listener=None, command=FAKE, servers=()):
    async def main():
        heard = listener or Recorder()
        session = acp.AgentSession(command, Path.cwd(), heard)
        try:
            await session.open(list(servers))
            await test(session, heard)
        finally:
            await session.close()

    asyncio.run(asyncio.wait_for(main(), 30))


def test_a_prompt_streams_text_and_ends_the_turn():
    async def test(session, heard):
        assert session.agent_name == "Fake Agent" and session.session_id == "s1"
        assert await session.prompt([acp.text_block("echo hello there")]) == "end_turn"
        assert heard.text() == "hello there"
        assert heard.text("agent_thought_chunk") == "thinking about it"

    session_test(test)


def test_the_session_is_opened_with_the_mcp_servers():
    server = {"name": "gpython", "command": "x", "args": ["mcp"], "env": [{"name": "A", "value": "b"}]}

    async def test(session, heard):
        await session.prompt([acp.text_block("mcp")])
        assert '"name": "gpython"' in heard.text() and '"value": "b"' in heard.text()

    session_test(test, servers=[server])


def test_tool_calls_and_plans_reach_the_listener():
    async def test(session, heard):
        await session.prompt([acp.text_block("tool")])
        await session.prompt([acp.text_block("plan")])
        kinds = [u["sessionUpdate"] for u in heard.updates]
        assert kinds == ["tool_call", "tool_call_update", "agent_message_chunk", "plan"]
        assert heard.updates[1]["content"][0]["newText"] == "one\nthree\n"

    session_test(test)


def test_a_permission_question_is_answered_with_the_chosen_option():
    async def test(session, heard):
        await session.prompt([acp.text_block("ask")])
        assert heard.asked[0][0]["title"] == "Run rm -rf build"
        assert [o["optionId"] for o in heard.asked[0][1]] == ["yes", "no"]
        assert heard.text() == "chose no"

    session_test(test, Recorder(choose="no"))


def test_a_refused_permission_is_answered_as_cancelled():
    async def test(session, heard):
        await session.prompt([acp.text_block("ask")])
        assert heard.text() == "chose cancelled"

    session_test(test, Recorder(choose=None))


def test_cancel_ends_a_running_turn():
    async def test(session, heard):
        turn = asyncio.ensure_future(session.prompt([acp.text_block("wait")]))
        await asyncio.sleep(0.2)
        assert not turn.done() and session.prompting
        session.cancel()
        assert await turn == "cancelled"
        assert not session.prompting

    session_test(test)


def test_an_agent_that_dies_fails_the_prompt_and_tells_the_listener():
    async def test(session, heard):
        with pytest.raises(acp.AgentError, match="exit code 3"):
            await session.prompt([acp.text_block("die")])
        assert heard.exited[0] == 3 and "out of luck" in heard.exited[1]

    session_test(test)


def test_an_agent_that_needs_a_login_reports_the_auth_code():
    async def main():
        session = acp.AgentSession(FAKE + ["--no-login"], Path.cwd(), Recorder())
        with pytest.raises(acp.AgentError) as caught:
            await session.open([])
        await session.close()
        assert caught.value.code == acp.AUTH_REQUIRED

    asyncio.run(asyncio.wait_for(main(), 30))


def test_a_command_that_does_not_exist_is_an_os_error():
    async def main():
        session = acp.AgentSession(["gpython-no-such-agent-program"], Path.cwd(), Recorder())
        with pytest.raises(OSError):
            await session.open([])
        await session.close()

    asyncio.run(asyncio.wait_for(main(), 30))


def test_error_text_adds_the_detail_an_agent_puts_in_data():
    assert acp.error_text({"message": "Internal error", "data": {"details": "no model set"}}) == "Internal error: no model set"
    assert acp.error_text({"message": "Bad", "data": "worse"}) == "Bad: worse"
    assert acp.error_text({}) == "The agent reported an error"


def test_the_registry_has_the_built_in_agents_and_the_ones_in_the_config():
    found = agents.known({"agents.mine": "my-agent --acp --name 'two words'", "agents.claude": "claude-agent-acp", "theme": "nord"})
    assert list(found)[:5] == ["claude", "codex", "opencode", "gemini", "pi"]
    assert found["mine"].command == ("my-agent", "--acp", "--name", "two words")
    assert found["claude"].command == ("claude-agent-acp",) and found["claude"].title == "Claude Code"
    assert found["opencode"].command == ("opencode", "acp")
    assert agents.default_name({"agent": "mine", "agents.mine": "x"}) == "mine"
    assert agents.Agent("x", "X", ("gpython-no-such-agent-program",)).missing == "gpython-no-such-agent-program"
    assert agents.Agent("x", "X", (sys.executable,)).missing is None
