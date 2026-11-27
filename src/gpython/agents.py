"""Which coding agents gpython can start, and the command that starts each one.

An agent is any program that speaks the Agent Client Protocol on its standard input and
output (acp.py). OpenCode and Gemini speak it themselves; Claude Code, Codex and pi do
through a small adapter that `npx` gets on first use. A line in the config adds another:

    agent = "codex"                      the agent :agent starts when no name is given

    [agents]
    mine = "my-agent --acp"              :agent mine
    claude = "claude-agent-acp"          a built-in name can be given another command
"""

from __future__ import annotations

import shlex
import shutil
import sys
from dataclasses import dataclass

BUILT_IN = {
    "claude": ("Claude Code", "npx -y @agentclientprotocol/claude-agent-acp"),
    "codex": ("Codex", "npx -y @agentclientprotocol/codex-acp"),
    "opencode": ("OpenCode", "opencode acp"),
    "gemini": ("Gemini", "gemini --acp"),
    "pi": ("pi", "npx -y pi-acp"),
}
NEEDS = {"claude": "claude", "codex": "codex", "pi": "pi"}
CONFIG_PREFIX = "agents."


@dataclass(frozen=True)
class Agent:
    name: str
    title: str
    command: tuple[str, ...]

    @property
    def missing(self) -> str | None:
        """The program this agent cannot start without and that is not installed, if any."""
        for program in (self.command[0] if self.command else "", NEEDS.get(self.name, "")):
            if program and shutil.which(program) is None:
                return program
        return None


def _split(command: str) -> tuple[str, ...]:
    try:
        return tuple(shlex.split(command, posix=sys.platform != "win32"))
    except ValueError:
        return tuple(command.split())


def known(settings: dict) -> dict[str, Agent]:
    """Every agent by name: the built-in ones, then the config's `agents.NAME = "command"` lines."""
    found = {name: Agent(name, title, _split(command)) for name, (title, command) in BUILT_IN.items()}
    for key, command in settings.items():
        name = key[len(CONFIG_PREFIX):]
        if key.startswith(CONFIG_PREFIX) and name and isinstance(command, str) and command.strip():
            title = BUILT_IN[name][0] if name in BUILT_IN else name
            found[name] = Agent(name, title, _split(command))
    return found


def default_name(settings: dict) -> str:
    """The config's `agent`, else the first agent whose programs are installed."""
    agents = known(settings)
    chosen = settings.get("agent")
    if isinstance(chosen, str) and chosen in agents:
        return chosen
    return next((name for name, agent in agents.items() if agent.missing is None), "claude")
