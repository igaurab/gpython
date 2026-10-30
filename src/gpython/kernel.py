"""A thin async wrapper around an IPython kernel in a separate process.

Running code out of process keeps the UI responsive, lets us interrupt
long-running cells, and survives crashes in user code.
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import subprocess
from collections.abc import Callable
from pathlib import Path

from jupyter_client.manager import AsyncKernelManager

OutputCallback = Callable[[str, dict], None]

# Helpers defined in the kernel. Leading underscores keep them out of the graph.
_HELPERS = r"""
def _gp_delete(names):
    g = globals()
    for n in names:
        g.pop(n, None)

def _gp_vars(names):
    g = globals()
    out = []
    for n in names:
        if n in g:
            v = g[n]
            try:
                r = repr(v)
            except Exception as e:
                r = f"<repr failed: {e!r}>"
            out.append([n, type(v).__name__, r.replace("\n", " ")[:80]])
    return out

def _gp_setup(file):
    import os, sys
    if file:
        globals()["__file__"] = file
        folder = os.path.dirname(file) or os.getcwd()
        os.chdir(folder)
        if folder not in sys.path:
            sys.path.insert(0, folder)
"""


class Kernel:
    def __init__(self, kernel_name: str = "python3", file: Path | None = None) -> None:
        self.manager = AsyncKernelManager(kernel_name=kernel_name)
        self.client = None
        self.file = file
        self.ready = asyncio.Event()
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        cwd = str(self.file.parent) if self.file else os.getcwd()
        # The kernel's own stdout/stderr would draw over the TUI; user output
        # still arrives through the Jupyter protocol.
        await self.manager.start_kernel(cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.client = self.manager.client()
        self.client.start_channels()
        await self._prepare()

    async def restart(self) -> None:
        self.ready.clear()
        await self.manager.restart_kernel(now=True)
        await self._prepare()

    async def _prepare(self) -> None:
        await self.client.wait_for_ready(timeout=60)
        await self._execute(_HELPERS, None, store_history=False)
        await self.set_file(self.file)
        self.ready.set()

    async def set_file(self, file: Path | None) -> None:
        """Make `__file__` and the working directory match the notebook, like a script."""
        self.file = file
        if file is not None:
            await self._execute(f"_gp_setup({str(file.resolve())!r})", None, store_history=False)

    async def shutdown(self) -> None:
        if self.client:
            self.client.stop_channels()
        if await self.manager.is_alive():
            await self.manager.shutdown_kernel(now=True)

    async def interrupt(self) -> None:
        await self.manager.interrupt_kernel()

    async def execute(self, code: str, on_output: OutputCallback | None = None) -> bool:
        """Run code, streaming iopub messages to on_output. Returns True on success."""
        await self.ready.wait()
        return await self._execute(code, on_output, store_history=True)

    async def delete(self, names: set[str]) -> None:
        if names:
            await self.ready.wait()
            await self._execute(f"_gp_delete({sorted(names)!r})", None, store_history=False)

    async def variables(self, names: set[str]) -> list[list[str]]:
        out: list[str] = []

        def collect(kind: str, content: dict) -> None:
            if kind == "stream":
                out.append(content["text"])

        code = f"print(__import__('json').dumps(_gp_vars({sorted(names)!r})))"
        await self.ready.wait()
        await self._execute(code, collect, store_history=False)
        try:
            return json.loads("".join(out))
        except json.JSONDecodeError:
            return []

    async def complete(self, code: str, cursor_pos: int) -> tuple[list[str], int, int]:
        """Return (matches, start, end) offsets into code, via the kernel's completer."""
        await self.ready.wait()
        async with self._lock:
            msg_id = self.client.complete(code, cursor_pos)
            reply = await self._shell_reply(msg_id, timeout=3)
        content = reply["content"] if reply else {}
        if content.get("status") != "ok":
            return [], cursor_pos, cursor_pos
        return content["matches"], content["cursor_start"], content["cursor_end"]

    async def _shell_reply(self, msg_id: str, timeout: float) -> dict | None:
        while True:
            try:
                reply = await self.client.get_shell_msg(timeout=timeout)
            except queue.Empty:
                return None
            if reply["parent_header"].get("msg_id") == msg_id:
                return reply

    async def _execute(self, code: str, on_output: OutputCallback | None, store_history: bool) -> bool:
        async with self._lock:
            msg_id = self.client.execute(code, store_history=store_history)
            ok = True
            while True:
                try:
                    msg = await self.client.get_iopub_msg(timeout=0.5)
                except queue.Empty:
                    if not await self.manager.is_alive():
                        if on_output:
                            on_output("error", {"traceback": ["Kernel died. Restart it from the command palette (ctrl+p)."]})
                        return False
                    continue
                if msg["parent_header"].get("msg_id") != msg_id:
                    continue
                kind, content = msg["msg_type"], msg["content"]
                if kind == "status" and content["execution_state"] == "idle":
                    break
                if kind == "error":
                    ok = False
                if on_output and kind in ("stream", "execute_result", "display_data", "error", "clear_output"):
                    on_output(kind, content)
            reply = await self._shell_reply(msg_id, timeout=5)
            if reply is not None:
                ok = ok and reply["content"].get("status") == "ok"
            return ok
