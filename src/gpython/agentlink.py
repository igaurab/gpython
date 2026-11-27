"""The door between a running gpython and the notebook tools an agent calls.

An agent does not talk to gpython directly: it starts `gpython mcp` (mcpserver.py), a small
program that speaks the Model Context Protocol on its standard input and output, and that
program connects here. `AgentLink` listens on a local TCP port (127.0.0.1 only; a port works
on every platform) and answers each tool call through `NotebookTools` on the app's event loop.

A connection must begin with the link's token, a random secret. The MCP server gets the
address and the token in the environment variable GPYTHON_AGENT_LINK when gpython starts the
agent itself, and from the link file (~/.gpython/links/<pid>.json, readable by the user only)
when an agent started somewhere else runs `gpython mcp`.

Lines are JSON. The server sends {"token": ...} first, then {"id", "tool", "arguments"} per
call, and gets back {"id", "result"} with the tool's answer in MCP's shape.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
from pathlib import Path

ENV = "GPYTHON_AGENT_LINK"
LINE_LIMIT = 64 * 1024 * 1024


def links_folder() -> Path:
    return Path(os.environ.get("GPYTHON_HOME") or Path.home() / ".gpython") / "links"


class AgentLink:
    def __init__(self, tools, cwd: Path) -> None:
        self.tools = tools
        self.cwd = Path(cwd)
        self.token = secrets.token_hex(16)
        self.port = 0
        self._server: asyncio.AbstractServer | None = None
        self._tasks: set[asyncio.Task] = set()
        self._file: Path | None = None

    @property
    def address(self) -> str:
        """What GPYTHON_AGENT_LINK holds: the port and the token."""
        return f"{self.port}:{self.token}"

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._connected, "127.0.0.1", 0, limit=LINE_LIMIT)
        self.port = self._server.sockets[0].getsockname()[1]
        self._write_file()

    def _write_file(self) -> None:
        folder = links_folder()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / f"{os.getpid()}.json"
            handle = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                json.dump({"pid": os.getpid(), "address": self.address, "cwd": str(self.cwd)}, out)
            self._file = target
        except OSError:
            self._file = None

    def mcp_server(self) -> dict:
        """The entry for ACP's `mcpServers`: how an agent starts the MCP server for this gpython."""
        return {
            "name": "gpython", "command": sys.executable,
            "args": ["-c", "from gpython.mcpserver import main; main()"],
            "env": [{"name": ENV, "value": self.address}],
        }

    async def _connected(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            hello = json.loads(await reader.readline() or b"{}")
            if not isinstance(hello, dict) or not secrets.compare_digest(str(hello.get("token", "")), self.token):
                return
            while line := await reader.readline():
                request = json.loads(line)
                task = asyncio.ensure_future(self._answer(request, writer))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        except (json.JSONDecodeError, OSError, ValueError):
            pass
        finally:
            writer.close()

    async def _answer(self, request: dict, writer: asyncio.StreamWriter) -> None:
        result = await self.tools.call(str(request.get("tool")), request.get("arguments") or {})
        try:
            writer.write(json.dumps({"id": request.get("id"), "result": result}).encode() + b"\n")
            await writer.drain()
        except OSError:
            pass

    async def stop(self) -> None:
        if self._file is not None:
            self._file.unlink(missing_ok=True)
            self._file = None
        for task in list(self._tasks):
            task.cancel()
        if self._server is not None:
            self._server.close()
            self._server = None


def live_links() -> list[dict]:
    """The link files of gpython processes that still run, newest first; files of dead ones are removed."""
    found = []
    try:
        files = sorted(links_folder().glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    except OSError:
        return []
    import psutil

    for path in files:
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            alive = psutil.pid_exists(int(entry["pid"])) and isinstance(entry["address"], str)
        except (OSError, ValueError, KeyError, TypeError):
            alive = False
        if alive:
            found.append(entry)
        else:
            path.unlink(missing_ok=True)
    return found


def find_address(cwd: Path) -> str | None:
    """The address for `gpython mcp`: from the environment, else the running gpython whose
    folder holds `cwd` (or is inside it), else the newest one."""
    if os.environ.get(ENV):
        return os.environ[ENV]
    links = live_links()
    here = Path(cwd).resolve()
    for entry in links:
        there = Path(entry.get("cwd", "")).resolve()
        if here == there or there in here.parents or here in there.parents:
            return entry["address"]
    return links[0]["address"] if links else None
