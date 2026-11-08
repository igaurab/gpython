"""Warm-up and cell scaling: from launching the tool until its first and last cells have run.

    uv run --with jupyterlab --with marimo python3 scripts/warmbench.py [--cells 1,10,100,500] [--runs 3] [--tools gpy,lab,marimo] [--json FILE]

Every tool gets the same N cells. Each cell assigns a variable, prints it and appends a
timestamp to a marker file, so `first` and `last` are read from the file the kernel wrote,
measured from the moment the tool's process was launched:

  gpy     ./gpython in a pseudo-terminal; F5 (run all) is pressed when the first frame is painted
  lab     `jupyter lab --no-browser`; a kernel is started over REST and every cell is sent over
          the kernel WebSocket at once, as JupyterLab's "run all" does (the browser loading
          JupyterLab's own page is not counted)
  marimo  `marimo edit --headless`; like its browser page, the script reads the server token from
          the page, opens the session WebSocket (which starts the kernel), waits for kernel-ready
          and asks the kernel to run every cell; the browser's own rendering is not counted

Columns: `ready` (gpy: first frame; lab and marimo: the server answers HTTP), `first` and
`last` (seconds from launch), `mem_mb` (RSS of the process tree after the last cell). Mean and
sample standard deviation over --runs (3 by default); --json FILE keeps every run.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import pty
import re
import select
import socket
import statistics
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
F5 = b"\x1b[15~"


def cell_sources(n, marker):
    stamp = f"open({str(marker)!r}, 'a').write(f\"{{__import__('time').time()}}\\n\")"
    return [f"v{i} = {i}\nprint(v{i})\n{stamp}" for i in range(n)]


def marks(marker):
    try:
        return [float(line) for line in marker.read_text().split()]
    except FileNotFoundError:
        return []


def tree_mb(pid):
    total = 0
    try:
        root = psutil.Process(pid)
        procs = [root] + root.children(recursive=True)
    except psutil.Error:
        return 0.0
    for proc in procs:
        try:
            total += proc.memory_info().rss
        except psutil.Error:
            pass
    return total / 2**20


def kill_tree(pid):
    try:
        root = psutil.Process(pid)
        procs = root.children(recursive=True) + [root]
    except psutil.Error:
        return
    for proc in procs:
        try:
            proc.kill()
        except psutil.Error:
            pass


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def result(t0, ready, marker, pid):
    stamps = marks(marker)
    return {"ready": ready, "first": min(stamps) - t0, "last": max(stamps) - t0, "mem_mb": tree_mb(pid)}


def run_gpy(n, workdir, timeout, sources=None):
    marker = workdir / "marks"
    notebook = workdir / "nb.py"
    sources = sources or cell_sources(n, marker)
    n = len(sources)
    notebook.write_text("\n\n".join("# %%\n" + source for source in sources) + "\n")
    t0 = time.time()
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(ROOT)
        os.execve(str(ROOT / "gpython"), ["gpython", str(notebook)], dict(os.environ, TERM="xterm-256color"))
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 45, 140, 0, 0))
    ready = None
    screen = b""
    try:
        while len(marks(marker)) < n:
            if time.time() - t0 > timeout:
                raise TimeoutError(f"gpy ran {len(marks(marker))} of {n} cells (first frame seen: {ready is not None})")
            readable, _, _ = select.select([fd], [], [], 0.005)
            if not readable:
                continue
            data = os.read(fd, 65536)
            if b"\x1b[c" in data:
                os.write(fd, b"\x1b[?62;c")
            if ready is None:
                screen += data
                if b"print" in screen:
                    ready = time.time() - t0
                    os.write(fd, F5)
        return result(t0, ready, marker, pid)
    finally:
        kill_tree(pid)
        os.close(fd)
        os.waitpid(pid, 0)


async def wait_http(url, timeout, headers=None):
    from tornado.httpclient import AsyncHTTPClient, HTTPClientError

    start = time.time()
    while True:
        try:
            await AsyncHTTPClient().fetch(url, headers=headers)
            return
        except HTTPClientError:
            return
        except OSError:
            if time.time() - start > timeout:
                raise
            await asyncio.sleep(0.02)


async def wait_marks(marker, n, t0, timeout, tool):
    while len(marks(marker)) < n:
        if time.time() - t0 > timeout:
            raise TimeoutError(f"{tool} ran {len(marks(marker))} of {n} cells")
        await asyncio.sleep(0.005)


def execute_message(session, code):
    return json.dumps({
        "header": {
            "msg_id": uuid.uuid4().hex, "msg_type": "execute_request", "username": "bench",
            "session": session, "date": "", "version": "5.3",
        },
        "parent_header": {}, "metadata": {}, "channel": "shell", "buffers": [],
        "content": {
            "code": code, "silent": False, "store_history": True, "user_expressions": {},
            "allow_stdin": False, "stop_on_error": True,
        },
    })


async def run_lab(n, workdir, timeout, sources=None):
    from tornado.httpclient import AsyncHTTPClient
    from tornado.websocket import websocket_connect

    marker = workdir / "marks"
    sources = sources or cell_sources(n, marker)
    n = len(sources)
    port, token = free_port(), uuid.uuid4().hex
    auth = {"Authorization": f"token {token}"}
    t0 = time.time()
    proc = subprocess.Popen(
        [sys.executable, "-m", "jupyterlab", "--no-browser", f"--port={port}", f"--IdentityProvider.token={token}",
         f"--ServerApp.root_dir={workdir}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        await wait_http(f"http://127.0.0.1:{port}/api/status", timeout, auth)
        ready = time.time() - t0
        response = await AsyncHTTPClient().fetch(
            f"http://127.0.0.1:{port}/api/kernels", method="POST", headers=auth, body=json.dumps({"name": "python3"})
        )
        kernel_id = json.loads(response.body)["id"]
        ws = await websocket_connect(f"ws://127.0.0.1:{port}/api/kernels/{kernel_id}/channels?token={token}")
        session = uuid.uuid4().hex
        for source in sources:
            ws.write_message(execute_message(session, source))
        await wait_marks(marker, n, t0, timeout, "lab")
        ws.close()
        return result(t0, ready, marker, proc.pid)
    finally:
        kill_tree(proc.pid)


def marimo_notebook(sources):
    cells = "".join(
        "@app.cell\ndef _():\n" + "".join(f"    {line}\n" for line in source.splitlines()) + "    return\n\n\n"
        for source in sources
    )
    return f"import marimo\n\napp = marimo.App()\n\n\n{cells}if __name__ == '__main__':\n    app.run()\n"


async def run_marimo(n, workdir, timeout, notebook_text=None, cells=None):
    from tornado.httpclient import AsyncHTTPClient
    from tornado.websocket import websocket_connect

    marker = workdir / "marks"
    notebook = workdir / "nb.py"
    notebook.write_text(notebook_text or marimo_notebook(cell_sources(n, marker)))
    n = cells or n
    port = free_port()
    t0 = time.time()
    proc = subprocess.Popen(
        [sys.executable, "-m", "marimo", "edit", str(notebook), "--headless", "--no-token", "-p", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        await wait_http(f"http://127.0.0.1:{port}/health", timeout)
        ready = time.time() - t0
        page = (await AsyncHTTPClient().fetch(f"http://127.0.0.1:{port}/")).body.decode()
        server_token = re.search(r'data-token="([^"]+)"', page).group(1)
        session = uuid.uuid4().hex
        ws = await websocket_connect(f"ws://127.0.0.1:{port}/ws?session_id={session}")
        while True:
            message = await ws.read_message()
            if message is None or '"kernel-ready"' in message:
                break
        await AsyncHTTPClient().fetch(
            f"http://127.0.0.1:{port}/api/kernel/instantiate", method="POST",
            headers={"Marimo-Session-Id": session, "Marimo-Server-Token": server_token, "Content-Type": "application/json"},
            body=json.dumps({"objectIds": [], "values": [], "autoRun": True}),
        )

        async def keep_reading():
            while await ws.read_message() is not None:
                pass

        reader = asyncio.create_task(keep_reading())
        await wait_marks(marker, n, t0, timeout, "marimo")
        reader.cancel()
        ws.close()
        return result(t0, ready, marker, proc.pid)
    finally:
        kill_tree(proc.pid)


def available(tool):
    module = {"gpy": "jupyter_client", "lab": "jupyterlab", "marimo": "marimo"}[tool]
    try:
        __import__(module)
        return True
    except ImportError:
        print(f"{tool}: skipped, {module} is not installed (add `--with {module}` to uv run)", file=sys.stderr)
        return False


def warn_if_busy():
    """The numbers are only worth comparing on an idle machine: a load average above half the
    cores (another job compiling or training) slows every tool and widens the error bars."""
    load, cores = os.getloadavg()[0], os.cpu_count() or 1
    if load > cores / 2:
        print(f"warning: load average {load:.1f} on {cores} cores; results will be slow and noisy", file=sys.stderr)


def run_once(tool, n, timeout):
    workdir = Path(tempfile.mkdtemp(prefix="gpwarm-"))
    if tool == "gpy":
        return run_gpy(n, workdir, timeout)
    if tool == "lab":
        return asyncio.run(run_lab(n, workdir, timeout))
    return asyncio.run(run_marimo(n, workdir, timeout))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cells", default="1,10,100,500")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--tools", default="gpy,lab,marimo")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--json", metavar="FILE", help="also write every run and the mean/std per cell to FILE")
    args = parser.parse_args()
    warn_if_busy()
    tools = [tool for tool in args.tools.split(",") if available(tool)]
    sizes = [int(n) for n in args.cells.split(",")]
    metrics = ["ready", "first", "last", "mem_mb"]
    rows = []
    print(f"{'tool':8}{'cells':>7}" + "".join(f"{m + ' (mean±std)':>22}" for m in metrics))
    for n in sizes:
        for tool in tools:
            runs = [run_once(tool, n, args.timeout) for _ in range(args.runs)]
            row = {"tool": tool, "cells": n, "runs": runs}
            for key in metrics:
                values = [run[key] for run in runs]
                row[key] = statistics.mean(values)
                row[key + "_std"] = statistics.stdev(values) if len(values) > 1 else 0.0
            rows.append(row)
            print(f"{tool:8}{n:7d}" + "".join(f"{row[m]:13.3f} ± {row[m + '_std']:6.3f}" for m in metrics), flush=True)
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
