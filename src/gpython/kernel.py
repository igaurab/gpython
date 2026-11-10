"""A thin async wrapper around an IPython kernel in a separate process.

Running code out of process keeps the UI responsive, lets us interrupt
long-running cells, and survives crashes in user code.
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from . import envs, prespawn

if TYPE_CHECKING:
    from jupyter_client.manager import AsyncKernelManager

OutputCallback = Callable[[str, dict], None]
InputCallback = Callable[[str, bool], None]
CellOutputCallback = Callable[[str, str, dict], None]
CellInputCallback = Callable[[str, str, bool], None]
RUN_MARK = re.compile("\x1egpython-run\x1e(\\{.*?\\})\n")

_HELPERS = r"""
def _gp_delete(names):
    g = globals()
    for n in names:
        g.pop(n, None)

import sys as _gp_sys
_gp_stdout, _gp_stderr = _gp_sys.stdout, _gp_sys.stderr

def _gp_group(cells, mark, watch):
    import sys, threading, types
    mod = sys.modules.get("_gpython_concurrency")
    if mod is None:
        mod = types.ModuleType("_gpython_concurrency")
        with open(_GP_CONCURRENCY, encoding="utf-8") as source:
            exec(compile(source.read(), "<gpython-concurrency>", "exec"), mod.__dict__)
        sys.modules["_gpython_concurrency"] = mod
    return mod.GroupRunner(get_ipython(), globals(), mark, threading.RLock()).run(cells, watch)

async def _gp_run(groups, watches=None):
    import asyncio, json, signal, sys, time
    shell = get_ipython()
    g = globals()
    cancel_handler = signal.getsignal(signal.SIGINT)
    open_cid = None
    watches = watches or [None] * len(groups)

    def mark(cid, event, **extra):
        buffered = getattr(_gp_stderr, "_buffer", None)
        if buffered is not None and buffered.tell():
            _gp_stderr.flush()
        payload = dict(cid=cid, event=event, t=time.perf_counter(), **extra)
        _gp_stdout.write("\x1egpython-run\x1e" + json.dumps(payload) + "\n")

    try:
        for group, watch in zip(groups, watches):
            if len(group) > 1:
                if not _gp_group(group, mark, watch):
                    break
                continue
            cid, code, names = group[0]
            for n in names:
                g.pop(n, None)
            try:
                transformed, trouble = shell.transform_cell(code), None
            except Exception:
                transformed, trouble = code, sys.exc_info()
            blocking = not shell.should_run_async(code, transformed_cell=transformed, preprocessing_exc_tuple=trouble)
            open_cid = cid
            mark(cid, "begin")
            if blocking:
                signal.signal(signal.SIGINT, signal.default_int_handler)
            try:
                result = await shell.run_cell_async(
                    code, store_history=True, cell_id=cid, transformed_cell=transformed, preprocessing_exc_tuple=trouble
                )
            finally:
                if blocking:
                    signal.signal(signal.SIGINT, cancel_handler)
            open_cid = None
            mark(cid, "end", ok=result.success)
            if not result.success:
                break
    except (KeyboardInterrupt, asyncio.CancelledError):
        if open_cid is not None:
            mark(open_cid, "end", ok=False)
    _gp_stdout.flush()

def _gp_vars(names):
    import sys
    return sys.modules["_gpython_debug"].variables(globals(), names)

def _gp_full(name):
    import json, sys
    g = globals()
    found = sys.modules["_gpython_debug"].full(g[name]) if name in g else {"kind": "text", "text": f"{name} is not defined"}
    print(json.dumps(found, default=str))

def _gp_configure(settings):
    import sys
    sys.modules["_gpython_debug"].configure(settings)

def _gp_postmortem():
    import sys
    sys.modules["_gpython_debug"].postmortem()

def _gp_arm(cid):
    import sys
    sys.modules["_gpython_debug"].arm(cid)

def _gp_install(path):
    import sys, types
    mod = types.ModuleType("_gpython_debug")
    with open(path, encoding="utf-8") as source:
        exec(compile(source.read(), "<gpython-debug>", "exec"), mod.__dict__)
    sys.modules["_gpython_debug"] = mod
    try:
        shell = get_ipython()
    except NameError:
        shell = None
    mod.install(shell)

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
    """_HELPERS plus the shim's mo.Html conversion (the same one the static preview of a marimo cell
    uses), then the debugger's kernel half, read by the kernel from kernel_side.py's path so IPython
    does not transform a 60 kB cell at every start."""
    import inspect

    from .markdown import html_to_markdown

    side = str(Path(__file__).with_name("kernel_side.py"))
    concurrency = str(Path(__file__).with_name("kernel_concurrency.py"))
    shim = inspect.getsource(html_to_markdown).replace("def html_to_markdown", "def _gp_html_to_md", 1)
    return _HELPERS + shim + f"\n_GP_CONCURRENCY = {concurrency!r}\n_gp_install({side!r})\n"


def debug_settings() -> dict:
    """The `debug_*` keys of the config: the debugger's preset and its limits."""
    from . import config

    return {key: value for key, value in config.load().items() if key.startswith("debug_")}


class Kernel:
    def __init__(self, kernel_name: str = "python3", file: Path | None = None, env: envs.Env | None = None) -> None:
        self.kernel_name = kernel_name
        self.env = env or envs.builtin()
        self.manager: AsyncKernelManager | None = None
        self.client = None
        self.file = file
        self.ready = asyncio.Event()
        self._lock = asyncio.Lock()
        self._generation = 0
        self._log: Path | None = None
        self.warning: str | None = None

    def _manager(self) -> AsyncKernelManager:
        from jupyter_client.manager import AsyncKernelManager

        manager = AsyncKernelManager(kernel_name=self.kernel_name)
        if self.env.kind != "builtin":
            from jupyter_client.kernelspec import KernelSpec

            manager._kernel_spec = KernelSpec(
                argv=envs.kernel_argv(self.env, "{connection_file}"),
                display_name=self.env.short,
                language="python",
            )
        return manager

    async def start(self) -> None:
        """Start the kernel. A uv project that does not start (its sync failed, say) starts
        again on its environment as it is (`envs.fallback`); `warning` then says what uv said."""
        self.warning = None
        sync_error: list[str] | None = None
        while True:
            try:
                await self._start()
                break
            except Exception as error:
                said = envs.read_log(self._log)
                next_try = envs.fallback(self.env)
                if next_try is None:
                    if self.env.kind != "uv-project":
                        raise
                    raise RuntimeError("\n".join([str(error), *said])) from error
                if sync_error is None:
                    sync_error = said
                await self._discard_failed()
                self.env = next_try
        envs.read_log(self._log)
        self._log = None
        if sync_error is not None:
            self.warning = "\n".join(["uv could not sync the project, so the kernel uses it as it is:", *sync_error[-4:]])

    async def _start(self) -> None:
        if self.manager is None:
            self.manager = self._manager()
        cwd = str(self.file.parent) if self.file and self.file.parent.is_dir() else os.getcwd()
        spawned = prespawn.take(self.env, self.file)
        self._log = spawned.log if spawned is not None else envs.launch_log(self.env)
        errors = open(self._log, "w") if spawned is None and self._log is not None else subprocess.DEVNULL
        try:
            launch = dict(cwd=cwd, env=envs.kernel_env(self.env), stdout=subprocess.DEVNULL, stderr=errors)
            if spawned is not None:
                self._adopt(spawned, launch)
            else:
                await self.manager.start_kernel(**launch)
        finally:
            if errors is not subprocess.DEVNULL:
                errors.close()
                if isinstance(self.manager._launch_args, dict):
                    self.manager._launch_args["stderr"] = subprocess.DEVNULL
        self.client = self.manager.client()
        self.client.start_channels()
        await self._prepare()

    async def _discard_failed(self) -> None:
        if self.client is not None:
            self.client.stop_channels()
        if self.manager is not None and self.manager.has_kernel:
            await self.manager.shutdown_kernel(now=True)
        self.client, self.manager = None, None

    def _adopt(self, spawned: prespawn.Spawned, launch: dict) -> None:
        """Manage a kernel process started before the UI (prespawn.py) as if start_kernel had
        launched it: the manager reads its connection file and gets a provisioner that owns
        the process, so interrupt, restart and shutdown work as usual."""
        import uuid

        from jupyter_client.provisioning import LocalProvisioner

        manager = self.manager
        manager.kernel_id = str(uuid.uuid4())
        manager.connection_file = str(spawned.connection_file)
        manager.load_connection_file()
        provisioner = LocalProvisioner(kernel_id=manager.kernel_id, kernel_spec=manager.kernel_spec, parent=manager)
        provisioner.process = spawned.process
        provisioner.pid = spawned.process.pid
        provisioner.pgid = os.getpgid(spawned.process.pid) if hasattr(os, "getpgid") else None
        provisioner.connection_info = manager.get_connection_info()
        manager.provisioner = provisioner
        manager._launch_args = dict(launch)
        manager._attempted_start = True
        manager._ready = asyncio.get_running_loop().create_future()
        manager._ready.set_result(None)
        manager._connect_control_socket()

    async def switch(self, env: envs.Env) -> None:
        """Replace the kernel process with one running in `env`. All variables are lost."""
        self.ready.clear()
        self._generation += 1
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
        """A fresh kernel process with fresh channels: a run that was waiting (paused in the
        debugger, say) ends as soon as it sees the new generation, and no stale socket state
        from the old process is read by the new one."""
        if self.manager is None:
            return await self.start()
        self.ready.clear()
        self._generation += 1
        await self.manager.restart_kernel(now=True)
        async with self._lock:
            self.client.stop_channels()
            self.client = self.manager.client()
            self.client.start_channels()
        await self._prepare()

    async def _prepare(self) -> None:
        await self.client.wait_for_ready(timeout=60)
        await self._execute(_helpers(), None, store_history=False)
        await self.set_file(self.file)
        settings = debug_settings()
        if settings:
            await self._execute(f"_gp_configure({settings!r})", None, store_history=False)
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

    async def execute(
        self, code: str, on_output: OutputCallback | None = None, cell_id: str | None = None,
        on_input: InputCallback | None = None, store_history: bool = True,
    ) -> bool:
        """Run code, streaming iopub messages to on_output. Returns True on success.

        `cell_id` goes in the request's metadata (ipykernel hands it to the pre_run_cell hook, which
        maps the cell's code filename to it for the debugger). With `on_input`, input() and the
        debugger's prompts reach it as (prompt, password); answer with `reply`."""
        await self.ready.wait()
        return await self._execute(code, on_output, store_history=store_history, cell_id=cell_id, on_input=on_input)

    def reply(self, text: str) -> None:
        """Answer the kernel's pending input request (input(), getpass, the debugger's prompt)."""
        if self.client is not None:
            self.client.input(text)

    async def full_value(self, name: str) -> dict:
        """The whole value of a global for the value viewer: text, or a table (capped in the kernel)."""
        return await self._print_json(f"_gp_full({name!r})") or {"kind": "text", "text": ""}

    async def configure_debugger(self, settings: dict) -> None:
        """The `debug_*` settings (preset and limits) for the kernel's next recording."""
        await self.ready.wait()
        await self._execute(f"_gp_configure({settings!r})", None, store_history=False)

    async def arm(self, cid: str) -> None:
        """Make the next run of cell `cid` pause on its first line."""
        await self.ready.wait()
        await self._execute(f"_gp_arm({cid!r})", None, store_history=False)

    async def _print_json(self, code: str):
        out: list[str] = []

        def collect(kind: str, content: dict) -> None:
            if kind == "stream":
                out.append(content["text"])

        await self.ready.wait()
        await self._execute(code, collect, store_history=False)
        try:
            return json.loads("".join(out))
        except json.JSONDecodeError:
            return None

    async def run_batch(
        self, groups: list[list[tuple[str, str, list[str]]]], on_event: CellOutputCallback, on_output: CellOutputCallback,
        on_input: CellInputCallback | None = None, watches: list[list[str]] | None = None,
    ) -> bool:
        """Run `groups` of cells, (cell id, code, names to delete first) each, in one request: the
        groups in order, the cells of a group of more than one at the same time on threads
        (kernel_concurrency.py), stopping after the first group with a cell that fails.
        `watches` names, per group, the shared names checked for changes after it ran.

        The kernel brackets every cell with a marker line written into its stdout stream:
        on_event(cid, "begin" | "end", payload), where the payload carries the kernel's clock
        in `t` and, at the end, `ok`. Outputs in between go to on_output(cid, kind, content)
        with the same (kind, content) as `execute`; an input() prompt reaches
        on_input(cid, prompt, password). The prompt travels on the stdin socket and the marker
        on iopub, so a prompt that arrives before its cell's begin (or switch) marker waits for
        it. One request for a whole run costs one round trip
        instead of two per cell, and a marker in the stream costs nothing where a message of
        its own would make the kernel wait for a flush, which is what makes run-all fast.

        Cells of a group interleave their output: a `switch` marker says which cell the stream
        speaks for next, and a `race` event (cell id None) names watched names that changed.

        Returns False when the request ended with a cell still open (the kernel died or was
        restarted), in which case the cells after it never began."""
        await self.ready.wait()
        current: list[str | None] = [None]
        open_cells: set[str] = set()
        early_prompts: list[tuple[str, bool]] = []

        def deliver_prompts() -> None:
            while early_prompts and current[0] is not None and on_input is not None:
                prompt, password = early_prompts.pop(0)
                on_input(current[0], prompt, password)

        def event(payload: dict) -> None:
            cid, kind = payload["cid"], payload["event"]
            if kind == "switch":
                current[0] = cid
                deliver_prompts()
                return
            if kind == "begin":
                open_cells.add(cid)
                current[0] = cid
            elif kind == "end":
                open_cells.discard(cid)
                current[0] = None
            on_event(cid, kind, payload)
            if kind == "begin":
                deliver_prompts()

        def text(name: str, chunk: str) -> None:
            if chunk and current[0] is not None:
                on_output(current[0], "stream", {"name": name, "text": chunk})

        def dispatch(kind: str, content: dict) -> None:
            if kind != "stream":
                if current[0] is not None:
                    on_output(current[0], kind, content)
                return
            name, body, at = content["name"], content["text"], 0
            for found in RUN_MARK.finditer(body):
                text(name, body[at : found.start()])
                event(json.loads(found.group(1)))
                at = found.end()
            text(name, body[at:])

        def ask(prompt: str, password: bool) -> None:
            if on_input is None:
                return
            if current[0] is None:
                early_prompts.append((prompt, password))
            else:
                on_input(current[0], prompt, password)

        code = f"await _gp_run({groups!r}, {watches!r})" if watches else f"await _gp_run({groups!r})"
        await self._execute(code, dispatch, store_history=False, on_input=ask if on_input else None)
        return not open_cells

    async def delete(self, names: set[str]) -> None:
        if names:
            await self.ready.wait()
            await self._execute(f"_gp_delete({sorted(names)!r})", None, store_history=False)

    async def variables(self, names: set[str]) -> list[list[str]]:
        """Rows [name, type, value, shape/len/dtype, category] for the names that exist in the kernel."""
        rows = await self._print_json(f"print(__import__('json').dumps(_gp_vars({sorted(names)!r})))")
        return rows if isinstance(rows, list) else []

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

    async def inspect(self, code: str, cursor_pos: int) -> str:
        """The kernel's help for the name at cursor_pos (IPython's `name?`: signature, docstring), or ""."""
        await self.ready.wait()
        async with self._lock:
            msg_id = self.client.inspect(code, cursor_pos, detail_level=0)
            reply = await self._shell_reply(msg_id, timeout=3)
        content = reply["content"] if reply else {}
        if content.get("status") != "ok" or not content.get("found"):
            return ""
        return content.get("data", {}).get("text/plain", "")

    async def _shell_reply(self, msg_id: str, timeout: float) -> dict | None:
        while True:
            try:
                reply = await self.client.get_shell_msg(timeout=timeout)
            except queue.Empty:
                return None
            if reply["parent_header"].get("msg_id") == msg_id:
                return reply

    def _send(self, code: str, store_history: bool, cell_id: str | None, allow_stdin: bool) -> str:
        content = {
            "code": code, "silent": False, "store_history": store_history, "user_expressions": {},
            "allow_stdin": allow_stdin, "stop_on_error": True,
        }
        msg = self.client.session.msg("execute_request", content, metadata={"cellId": cell_id} if cell_id else {})
        self.client.shell_channel.send(msg)
        return msg["header"]["msg_id"]

    async def _listen_stdin(self, msg_id: str, on_input: InputCallback) -> None:
        while True:
            try:
                msg = await self.client.get_stdin_msg(timeout=1)
            except queue.Empty:
                continue
            if msg["msg_type"] == "input_request" and msg["parent_header"].get("msg_id") == msg_id:
                content = msg["content"]
                on_input(content.get("prompt", ""), bool(content.get("password")))

    async def _execute(
        self, code: str, on_output: OutputCallback | None, store_history: bool,
        cell_id: str | None = None, on_input: InputCallback | None = None,
    ) -> bool:
        async with self._lock:
            if self.client is None or not self.client.channels_running:
                return False
            generation = self._generation
            msg_id = self._send(code, store_history, cell_id, on_input is not None)
            listener = asyncio.create_task(self._listen_stdin(msg_id, on_input)) if on_input is not None else None
            try:
                return await self._collect(msg_id, generation, on_output)
            finally:
                if listener is not None:
                    listener.cancel()

    async def _collect(self, msg_id: str, generation: int, on_output: OutputCallback | None) -> bool:
        """Stream the run's iopub messages until the kernel is idle; a restart or a dead kernel ends it."""
        ok = True
        while True:
            if generation != self._generation:
                if on_output:
                    on_output("error", {"ename": "KernelRestarted", "evalue": "", "traceback": ["The kernel was restarted while this cell ran."]})
                return False
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
