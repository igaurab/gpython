"""`gpython mcp`: the notebook tools as a Model Context Protocol server on stdin and stdout.

A coding agent (Claude Code, Codex, OpenCode, pi, Gemini) starts this program and sees the
tools of notebooktools.py. Each call is passed to the gpython the user has open, over the
local connection of agentlink.py, and the answer is passed back. gpython starts it for the
agent in its own agent panel; an agent in another terminal can use it too:

    claude mcp add gpython -- gpython mcp
    codex mcp add gpython -- gpython mcp

The program is small on purpose: no textual, no kernel, plain blocking sockets, one thread
per tool call so that a long run does not hold up the next call.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
from pathlib import Path

from . import agentlink
from .notebooktools import INSTRUCTIONS, SPECS

PROTOCOL_VERSION = "2025-06-18"
METHOD_NOT_FOUND = -32601
NOT_OPEN = (
    "gpython is not open, or its agent link is off. Open the notebook with gpython and type :agent link "
    "(or open the agent panel with :agent), then call the tool again."
)


class Link:
    """The connection to the running gpython. It is opened on the first tool call and again
    after it was lost, so the agent can be started before gpython is."""

    def __init__(self) -> None:
        self._socket: socket.socket | None = None
        self._reader = None
        self._lock = threading.Lock()
        self._waiting: dict[int, dict] = {}
        self._next_id = 0

    def _connect(self) -> None:
        address = agentlink.find_address(Path.cwd())
        if address is None:
            raise ConnectionError(NOT_OPEN)
        port, _, token = address.partition(":")
        self._socket = socket.create_connection(("127.0.0.1", int(port)), timeout=5)
        self._socket.settimeout(None)
        self._socket.sendall(json.dumps({"token": token}).encode() + b"\n")
        self._reader = self._socket.makefile("rb")
        threading.Thread(target=self._read, args=(self._reader, self._socket), daemon=True).start()

    def _read(self, reader, connection: socket.socket) -> None:
        try:
            for line in reader:
                answer = json.loads(line)
                slot = self._waiting.pop(answer.get("id"), None)
                if slot is not None:
                    slot["result"] = answer.get("result")
                    slot["done"].set()
        except (OSError, ValueError):
            pass
        with self._lock:
            if self._socket is connection:
                self._socket = None
            waiting, self._waiting = self._waiting, {}
        for slot in waiting.values():
            slot["done"].set()

    def call(self, tool: str, arguments: dict) -> dict:
        slot = {"done": threading.Event(), "result": None}
        try:
            with self._lock:
                if self._socket is None:
                    self._connect()
                self._next_id += 1
                self._waiting[self._next_id] = slot
                self._socket.sendall(json.dumps({"id": self._next_id, "tool": tool, "arguments": arguments}).encode() + b"\n")
        except (OSError, ValueError):
            self._socket = None
            return refusal(NOT_OPEN)
        slot["done"].wait()
        return slot["result"] or refusal("gpython closed before it answered. " + NOT_OPEN)


def refusal(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True}


class Server:
    def __init__(self, out=None) -> None:
        self.link = Link()
        self.out = out or sys.stdout
        self._write_lock = threading.Lock()

    def send(self, message: dict) -> None:
        with self._write_lock:
            self.out.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.out.flush()

    def handle(self, message: dict) -> None:
        method, request_id = message.get("method"), message.get("id")
        if method is None or request_id is None:
            return
        params = message.get("params") or {}
        if method == "initialize":
            self.reply(request_id, {
                "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "gpython", "title": "gpython notebook", "version": "1"},
                "instructions": INSTRUCTIONS,
            })
        elif method == "ping":
            self.reply(request_id, {})
        elif method == "tools/list":
            self.reply(request_id, {"tools": SPECS})
        elif method == "tools/call":
            threading.Thread(target=self._call, args=(request_id, params), daemon=True).start()
        else:
            self.send({"jsonrpc": "2.0", "id": request_id, "error": {"code": METHOD_NOT_FOUND, "message": f"Method not found: {method}"}})

    def reply(self, request_id, result: dict) -> None:
        self.send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _call(self, request_id, params: dict) -> None:
        self.reply(request_id, self.link.call(str(params.get("name")), params.get("arguments") or {}))

    def serve(self, lines) -> None:
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict):
                self.handle(message)


def main() -> None:
    for stream in (sys.stdin, sys.stdout):
        stream.reconfigure(encoding="utf-8")
    Server().serve(sys.stdin)
