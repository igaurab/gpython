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
from typing import TYPE_CHECKING

from . import envs

if TYPE_CHECKING:
    from jupyter_client.manager import AsyncKernelManager

OutputCallback = Callable[[str, dict], None]

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
        if os.path.isdir(folder):
            os.chdir(folder)
        if folder not in sys.path:
            sys.path.insert(0, folder)

def _gp_marimo_shim():
    try:
        import marimo
        return
    except ImportError:
        pass
    import sys, types

    def _md_of(item):
        f = getattr(item, "_repr_markdown_", None)
        return f() if callable(f) else str(item)

    class _Md:
        def __init__(self, text):
            self.text = text
        def _repr_markdown_(self):
            return self.text
        def __repr__(self):
            return self.text

    class Html:
        def __init__(self, text):
            self.text = text
        def _repr_html_(self):
            return self.text
        def _repr_markdown_(self):
            return _gp_html_to_md(self.text)
        def __repr__(self):
            return self._repr_markdown_()

    class _Stack:
        def __init__(self, items, sep):
            self.items, self.sep = list(items), sep
        def _repr_markdown_(self):
            return self.sep.join(_md_of(i) for i in self.items)
        def __repr__(self):
            return self._repr_markdown_()

    def _keep(obj=None, **kw):
        if obj is None:
            return lambda f: f
        return obj

    class App:
        def __init__(self, **kw):
            self.kw = kw
        cell = class_definition = function = staticmethod(_keep)
        def run(self):
            pass

    class _Ui(types.ModuleType):
        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)
            raise NotImplementedError("marimo.ui widgets are not supported in gpython")

    mod = types.ModuleType("marimo")
    mod.__gpython_shim__ = True
    mod.md = lambda text: _Md(text)
    mod.Html = Html
    mod.vstack = lambda items, **kw: _Stack(items, "\n\n")
    mod.hstack = lambda items, **kw: _Stack(items, "  ")
    mod.App = App
    mod.ui = _Ui("marimo.ui")
    sys.modules["marimo"] = mod
    sys.modules["marimo.ui"] = mod.ui

_gp_marimo_shim()

def _gp_uv_magics():
    # %uv runs uv against *this* kernel's interpreter; %pip falls back to uv where pip is missing.
    import importlib, importlib.util, os, shlex, subprocess, sys
    try:
        ip = get_ipython()
    except NameError:
        return
    exe = os.environ.get("GPYTHON_UV")

    def _run(argv, env):
        p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
        for line in p.stdout:
            print(line, end="", flush=True)
        p.wait()
        importlib.invalidate_caches()
        if p.returncode:
            print(f"(exit code {p.returncode})")

    def uv(line):
        if not exe:
            print("uv is not installed: https://docs.astral.sh/uv/")
            return
        args = shlex.split(line)
        env = dict(os.environ)
        if args[:1] == ["pip"] and len(args) > 1 and not {"--python", "-p"} & set(args):
            args[2:2] = ["--python", sys.executable]
        elif args[:1] in (["add"], ["remove"], ["sync"]) and sys.prefix != sys.base_prefix and "--active" not in args:
            args[1:1] = ["--active"]
            env["VIRTUAL_ENV"] = sys.prefix
        _run([exe, *args], env)

    ip.register_magic_function(uv, "line", "uv")
    if exe and importlib.util.find_spec("pip") is None:
        ip.register_magic_function(lambda line: uv("pip " + line), "line", "pip")

_gp_uv_magics()
"""



def _helpers() -> str:
    """_HELPERS plus the shim's mo.Html conversion: the same one the static preview of a marimo cell uses."""
    import inspect

    from .markdown import html_to_markdown

    return _HELPERS + inspect.getsource(html_to_markdown).replace("def html_to_markdown", "def _gp_html_to_md", 1)


class Kernel:
    def __init__(self, kernel_name: str = "python3", file: Path | None = None, env: envs.Env | None = None) -> None:
        self.kernel_name = kernel_name
        self.env = env or envs.builtin()
        self.manager: AsyncKernelManager | None = None
        self.client = None
        self.file = file
        self.ready = asyncio.Event()
        self._lock = asyncio.Lock()

    def _manager(self) -> AsyncKernelManager:
        from jupyter_client.manager import AsyncKernelManager

        manager = AsyncKernelManager(kernel_name=self.kernel_name)
        if self.env.kind != "builtin":
            from jupyter_client.kernelspec import KernelSpec

            manager._kernel_spec = KernelSpec(
                argv=[str(self.env.python), "-m", "ipykernel_launcher", "-f", "{connection_file}"],
                display_name=self.env.short,
                language="python",
            )
        return manager

    async def start(self) -> None:
        if self.manager is None:
            self.manager = self._manager()
        cwd = str(self.file.parent) if self.file and self.file.parent.is_dir() else os.getcwd()
        await self.manager.start_kernel(
            cwd=cwd, env=envs.kernel_env(self.env), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        self.client = self.manager.client()
        self.client.start_channels()
        await self._prepare()

    async def switch(self, env: envs.Env) -> None:
        """Replace the kernel process with one running in `env`. All variables are lost."""
        self.ready.clear()
        old_env = self.env
        if self.manager is not None and await self.manager.is_alive():
            await self.manager.shutdown_kernel(now=True)
        async with self._lock:
            pass
        await self.shutdown()
        self.env, self.client = env, None
        self.manager = self._manager()
        try:
            await self.start()
        except Exception:
            self.env, self.client = old_env, None
            self.manager = self._manager()
            await self.start()
            raise

    async def restart(self) -> None:
        if self.manager is None:
            return await self.start()
        self.ready.clear()
        await self.manager.restart_kernel(now=True)
        await self._prepare()

    async def _prepare(self) -> None:
        await self.client.wait_for_ready(timeout=60)
        await self._execute(_helpers(), None, store_history=False)
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
        if self.manager is not None and await self.manager.is_alive():
            await self.manager.shutdown_kernel(now=True)

    async def interrupt(self) -> None:
        if self.manager is not None:
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
