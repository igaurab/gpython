"""The client side of the Agent Client Protocol (ACP, protocol version 1).

An ACP agent is a program that reads and writes JSON-RPC messages, one per line, on its
standard input and output. gpython starts the agent (`AgentProcess`), opens a session with
the notebook tools attached as an MCP server (`AgentSession.open`), sends the user's prompt
and passes on what the agent streams back: text, thoughts, tool calls, plans and questions
about permission. Nothing here knows which agent is on the other end, and nothing here
imports textual; the panel (agent.py) is the listener.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Awaitable, Callable

PROTOCOL_VERSION = 1
LINE_LIMIT = 64 * 1024 * 1024
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603
AUTH_REQUIRED = -32000
STDERR_KEPT = 4000

NotificationHandler = Callable[[str, dict], None]
RequestHandler = Callable[[str, dict], Awaitable[Any]]


class AgentError(Exception):
    """The agent answered a request with an error, or went away before it answered."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class UnknownMethod(Exception):
    """The agent asked for something this client does not do."""


class AgentProcess:
    """One running agent and the JSON-RPC conversation with it."""

    def __init__(self, command: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
        self.command = command
        self.cwd = cwd
        self.env = env
        self.process: asyncio.subprocess.Process | None = None
        self.on_notification: NotificationHandler = lambda method, params: None
        self.on_request: RequestHandler | None = None
        self.on_exit: Callable[[int | None], None] = lambda code: None
        self.stderr_tail = ""
        self._next_id = 0
        self._waiting: dict[int, asyncio.Future] = {}
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        self.process = await asyncio.create_subprocess_exec(
            *self.command, cwd=self.cwd, env={**os.environ, **(self.env or {})}, limit=LINE_LIMIT,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        self._spawn(self._read_messages())
        self._spawn(self._read_stderr())

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    def _spawn(self, coroutine) -> None:
        task = asyncio.ensure_future(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def request(self, method: str, params: dict) -> Any:
        if not self.running:
            raise AgentError("The agent is not running")
        self._next_id += 1
        answer: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiting[self._next_id] = answer
        self._write({"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params})
        return await answer

    def notify(self, method: str, params: dict) -> None:
        if self.running:
            self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _write(self, message: dict) -> None:
        try:
            self.process.stdin.write(json.dumps(message, ensure_ascii=False).encode() + b"\n")
        except (OSError, RuntimeError):
            pass

    async def _read_messages(self) -> None:
        stream = self.process.stdout
        while True:
            try:
                line = await stream.readline()
            except (ValueError, OSError):
                break
            if not line:
                break
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict):
                self._dispatch(message)
        code = await self.process.wait()
        self._fail_waiting(AgentError(self._exit_message(code)))
        self.on_exit(code)

    def _exit_message(self, code: int | None) -> str:
        last = self.stderr_tail.strip().splitlines()[-1:] or [""]
        return f"The agent stopped (exit code {code}). {last[0]}".strip()

    def _fail_waiting(self, error: AgentError) -> None:
        waiting, self._waiting = self._waiting, {}
        for answer in waiting.values():
            if not answer.done():
                answer.set_exception(error)

    async def _read_stderr(self) -> None:
        stream = self.process.stderr
        while chunk := await stream.read(4096):
            self.stderr_tail = (self.stderr_tail + chunk.decode(errors="replace"))[-STDERR_KEPT:]

    def _dispatch(self, message: dict) -> None:
        method = message.get("method")
        if method is None:
            self._answered(message)
        elif "id" in message:
            self._spawn(self._serve(message["id"], method, message.get("params") or {}))
        else:
            self.on_notification(method, message.get("params") or {})

    def _answered(self, message: dict) -> None:
        answer = self._waiting.pop(message.get("id"), None)
        if answer is None or answer.done():
            return
        error = message.get("error")
        if error is not None:
            answer.set_exception(AgentError(error_text(error), error.get("code") if isinstance(error, dict) else None))
        else:
            answer.set_result(message.get("result"))

    async def _serve(self, request_id, method: str, params: dict) -> None:
        try:
            if self.on_request is None:
                raise UnknownMethod(method)
            result = await self.on_request(method, params)
            reply = {"jsonrpc": "2.0", "id": request_id, "result": result}
        except UnknownMethod:
            reply = {"jsonrpc": "2.0", "id": request_id, "error": {"code": METHOD_NOT_FOUND, "message": f"Method not found: {method}"}}
        except Exception as error:
            reply = {"jsonrpc": "2.0", "id": request_id, "error": {"code": INTERNAL_ERROR, "message": str(error)}}
        if self.running:
            self._write(reply)

    async def stop(self) -> None:
        process = self.process
        if process is None:
            return
        if process.returncode is None:
            try:
                process.stdin.close()
                process.terminate()
            except (OSError, ProcessLookupError):
                pass
            try:
                await asyncio.wait_for(process.wait(), 2)
            except asyncio.TimeoutError:
                process.kill()
        for task in list(self._tasks):
            task.cancel()


def error_text(error) -> str:
    """The words of a JSON-RPC error; agents put the useful part in `message` or in `data`."""
    if not isinstance(error, dict):
        return str(error)
    message = str(error.get("message") or "The agent reported an error")
    data = error.get("data")
    detail = data.get("details") or data.get("message") if isinstance(data, dict) else data
    if isinstance(detail, str) and detail and detail not in message:
        message = f"{message}: {detail}"
    return message


class Listener:
    """What a session tells its owner. The panel overrides these; the defaults do nothing."""

    def session_update(self, update: dict) -> None:
        """One step of the agent's turn: a `sessionUpdate` payload from the protocol."""

    async def permission(self, tool_call: dict, options: list[dict]) -> str | None:
        """The agent asks before a tool call. Returns the chosen option's id, None to cancel."""
        return None

    def agent_exited(self, code: int | None, stderr_tail: str) -> None:
        """The agent's process ended."""


class AgentSession:
    """One conversation with one agent."""

    def __init__(self, command: list[str], cwd: Path, listener: Listener, env: dict[str, str] | None = None) -> None:
        self.cwd = Path(cwd)
        self.listener = listener
        self.process = AgentProcess(command, self.cwd, env)
        self.process.on_notification = self._notified
        self.process.on_request = self._asked
        self.process.on_exit = self._exited
        self.session_id: str | None = None
        self.agent_name = ""
        self.prompting = False

    async def open(self, mcp_servers: list[dict]) -> None:
        """Start the agent, agree on the protocol and open a session with the MCP servers attached."""
        await self.process.start()
        hello = await self.process.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
            "clientInfo": {"name": "gpython", "title": "gpython", "version": client_version()},
        })
        hello = hello or {}
        info = hello.get("agentInfo") or {}
        self.agent_name = info.get("title") or info.get("name") or ""
        created = await self.process.request("session/new", {"cwd": str(self.cwd), "mcpServers": mcp_servers})
        self.session_id = (created or {}).get("sessionId")

    async def prompt(self, blocks: list[dict]) -> str:
        """Send one user turn and wait for the agent to end it. Returns the stop reason."""
        self.prompting = True
        try:
            done = await self.process.request("session/prompt", {"sessionId": self.session_id, "prompt": blocks})
        finally:
            self.prompting = False
        return (done or {}).get("stopReason") or "end_turn"

    def cancel(self) -> None:
        """Ask the agent to stop the running turn; `prompt` then returns "cancelled"."""
        if self.session_id is not None and self.prompting:
            self.process.notify("session/cancel", {"sessionId": self.session_id})

    async def close(self) -> None:
        await self.process.stop()

    def _notified(self, method: str, params: dict) -> None:
        update = params.get("update")
        if method == "session/update" and isinstance(update, dict):
            self.listener.session_update(update)

    async def _asked(self, method: str, params: dict):
        if method != "session/request_permission":
            raise UnknownMethod(method)
        chosen = await self.listener.permission(params.get("toolCall") or {}, params.get("options") or [])
        if chosen is None:
            return {"outcome": {"outcome": "cancelled"}}
        return {"outcome": {"outcome": "selected", "optionId": chosen}}

    def _exited(self, code: int | None) -> None:
        self.listener.agent_exited(code, self.process.stderr_tail)


def text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def client_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("gpynb")
    except PackageNotFoundError:
        return "0"
