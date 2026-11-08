"""Memory and idle CPU of gpy, JupyterLab and marimo, each with its kernel running.

    uv run python3 scripts/membench.py gpy [--cells N]
    uv run --with jupyterlab python3 scripts/membench.py lab
    uv run --with marimo python3 scripts/membench.py marimo
    uv run python3 scripts/membench.py cache

Each tool's process tree is measured after it settles: RSS per process (shared libraries are
counted in every process, so totals run high) and CPU over five idle seconds. gpy runs in a
pseudo-terminal like ptybench.py; JupyterLab gets a kernel through its REST API; marimo starts
its kernel when a client opens the session WebSocket, so the script opens one. Browser tabs are
not measured. `cache` shows ipykernel's output cache (Out[n], _) holding five 50 MB results.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import pty
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import termios
import time
import uuid
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from bench import make_notebook

MARIMO_NOTEBOOK = (
    "import marimo\napp = marimo.App()\n\n@app.cell\ndef _():\n    x = 1\n    return (x,)\n\n"
    "if __name__ == '__main__':\n    app.run()\n"
)


def tree(pid):
    root = psutil.Process(pid)
    return [root] + root.children(recursive=True)


def footprint(pid):
    rows = []
    for proc in tree(pid):
        try:
            rows.append((proc.name(), proc.memory_info().rss / 2**20))
        except psutil.Error:
            pass
    return rows


def idle_cpu(pid, seconds=5.0):
    def total():
        spent = 0.0
        for proc in tree(pid):
            try:
                times = proc.cpu_times()
                spent += times.user + times.system
            except psutil.Error:
                pass
        return spent

    before = total()
    time.sleep(seconds)
    return (total() - before) / seconds * 100


def report(label, pid):
    rows = footprint(pid)
    cpu = idle_cpu(pid)
    parts = " + ".join(f"{name} {mb:.0f}" for name, mb in rows)
    print(f"{label:24} {sum(mb for _, mb in rows):6.0f} MB  idle_cpu {cpu:5.2f}%  ({parts})")


def kill_tree(pid):
    for proc in reversed(tree(pid)):
        try:
            proc.kill()
        except psutil.Error:
            pass


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def drain(fd, seconds):
    end = time.time() + seconds
    while time.time() < end:
        try:
            os.read(fd, 65536)
        except OSError:
            time.sleep(0.05)


def bench_gpy(cells):
    path = make_notebook(cells)
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(ROOT)
        os.execve(str(ROOT / "gpython"), ["gpython", str(path)], dict(os.environ, TERM="xterm-256color"))
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 45, 140, 0, 0))
    os.set_blocking(fd, False)
    drain(fd, 6)
    try:
        report(f"gpy ({cells} cells)", pid)
    finally:
        kill_tree(pid)
        os.kill(pid, signal.SIGTERM)


async def wait_http(url, headers=None):
    from tornado.httpclient import AsyncHTTPClient, HTTPClientError

    start = time.time()
    while True:
        try:
            await AsyncHTTPClient().fetch(url, headers=headers)
            return
        except HTTPClientError:
            return
        except OSError:
            if time.time() - start > 60:
                raise
            await asyncio.sleep(0.1)


async def bench_lab():
    from tornado.httpclient import AsyncHTTPClient

    port, token = free_port(), uuid.uuid4().hex
    proc = subprocess.Popen(
        [sys.executable, "-m", "jupyterlab", "--no-browser", f"--port={port}", f"--IdentityProvider.token={token}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        auth = {"Authorization": f"token {token}"}
        await wait_http(f"http://127.0.0.1:{port}/api/status", auth)
        await AsyncHTTPClient().fetch(
            f"http://127.0.0.1:{port}/api/kernels", method="POST", headers=auth, body=json.dumps({"name": "python3"})
        )
        await asyncio.sleep(4)
        report("jupyterlab", proc.pid)
    finally:
        kill_tree(proc.pid)


async def bench_marimo():
    from tornado.websocket import websocket_connect

    notebook = Path(tempfile.mkdtemp(prefix="gpbench-")) / "nb.py"
    notebook.write_text(MARIMO_NOTEBOOK)
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "marimo", "edit", str(notebook), "--headless", "--no-token", "-p", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        await wait_http(f"http://127.0.0.1:{port}/health")
        ws = await websocket_connect(f"ws://127.0.0.1:{port}/ws?session_id={uuid.uuid4().hex}")
        while True:
            message = await asyncio.wait_for(ws.read_message(), 60)
            if message is None or '"kernel-ready"' in message:
                break
        await asyncio.sleep(4)
        report("marimo edit", proc.pid)
        ws.close()
    finally:
        kill_tree(proc.pid)


async def output_cache(cache_size):
    from jupyter_client.manager import AsyncKernelManager

    manager = AsyncKernelManager(kernel_name="python3")
    await manager.start_kernel(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    client = manager.client()
    client.start_channels()
    await client.wait_for_ready(timeout=60)

    async def run(code):
        msg_id = client.execute(code)
        while True:
            msg = await client.get_iopub_msg(timeout=60)
            if (
                msg["parent_header"].get("msg_id") == msg_id
                and msg["msg_type"] == "status"
                and msg["content"]["execution_state"] == "idle"
            ):
                return

    kernel = psutil.Process(manager.provisioner.pid)
    if cache_size is not None:
        await run(f"get_ipython().displayhook.cache_size = {cache_size}")
    await run("class Big:\n    def __init__(s): s.d = bytearray(b'x') * 50_000_000\n    def __repr__(s): return 'Big'")
    before = kernel.memory_info().rss / 2**20
    for _ in range(5):
        await run("Big()")
    after = kernel.memory_info().rss / 2**20
    label = "default" if cache_size is None else f"cache_size={cache_size}"
    print(f"output cache {label:14} kernel {before:5.0f} -> {after:5.0f} MB after five cells returning 50 MB each")
    client.stop_channels()
    await manager.shutdown_kernel(now=True)


async def bench_cache():
    await output_cache(None)
    await output_cache(0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("tool", choices=["gpy", "lab", "marimo", "cache"])
    parser.add_argument("--cells", type=int, default=60)
    args = parser.parse_args()
    if args.tool == "gpy":
        bench_gpy(args.cells)
    elif args.tool == "lab":
        asyncio.run(bench_lab())
    elif args.tool == "marimo":
        asyncio.run(bench_marimo())
    else:
        asyncio.run(bench_cache())


if __name__ == "__main__":
    main()
