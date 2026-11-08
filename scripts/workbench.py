"""Workload scaling: CPU-bound and IO-bound notebooks of 1 to 1000 cells in gpy, JupyterLab and marimo.

    ~/.cache/gpython-workbench-env/bin/python scripts/workbench.py [--problems C1,D1] [--cells 1,10,100,500,1000]
        [--runs 5] [--tools gpy,lab,marimo] [--json FILE]

    scripts/workeval.sh builds the shared environment and runs the full sweep.

Every notebook is one setup cell and N work cells. The setup cell imports, builds the data and
defines `work(i)`; work cell i runs `r{i} = work({i})` once (about 10 ms of real work), prints one
line and appends its own start and end time to a spans file. The tools are driven exactly as in
warmbench.py, and all three kernels run in one interpreter (the environment this script runs in,
linked as `.venv` next to the gpy notebook), with BLAS pinned to one thread.

Problems (see docs/plans/13-workload-eval.md):
  C1 numpy matmul 1200x1200      C2 numpy rfft of 1.5M + reduction   C3 pure-Python sum of squares
  D1 6 MB write, fsync, read      D2 100 x 4 KB files written and read
  N1 4 sequential HTTP GETs (64 KB, 2 ms server delay)   N2 the same 4 GETs with top-level await

Per run: `ready` and `last` (seconds from launch, as warmbench), `work_ms` (mean in-cell time),
`gap_ms` (mean idle time between one work cell ending and the next starting: the tool's own
cost per cell), `span` (first work cell start to last end), `mem_mb`. The tool order is shuffled
in each run, after one discarded warm-up launch per tool (the first launch reads a cold disk
cache).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import warmbench

BLAS_THREADS = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS")
PAYLOAD = os.urandom(64 * 1024)
SERVER_DELAY = 0.002


@dataclass(frozen=True)
class Problem:
    """One workload: `setup` defines `work(i)` (and its data); a cell calls it once."""

    key: str
    domain: str
    title: str
    setup: str
    awaits: bool = False


PROBLEMS = [
    Problem("C1", "cpu", "numpy matmul 1200²", (
        "rng = np.random.default_rng(0)\n"
        "A = rng.random((1200, 1200))\n"
        "B = rng.random((1200, 1200))\n"
        "def work(i):\n"
        "    return float((A @ B).sum())\n"
    )),
    Problem("C2", "cpu", "numpy rfft 1.5M", (
        "X = np.random.default_rng(0).random(1_500_000)\n"
        "def work(i):\n"
        "    return float(np.abs(np.fft.rfft(X)).sum())\n"
    )),
    Problem("C3", "cpu", "pure-Python loop", (
        "def work(i):\n"
        "    return sum(k * k for k in range(270_000))\n"
    )),
    Problem("D1", "io", "disk: 6 MB + fsync", (
        "blob = os.urandom(6 << 20)\n"
        "def work(i):\n"
        "    path = f'{DATA}/big{i}'\n"
        "    with open(path, 'wb') as out:\n"
        "        out.write(blob)\n"
        "        out.flush()\n"
        "        os.fsync(out.fileno())\n"
        "    with open(path, 'rb') as back:\n"
        "        digest = hashlib.blake2b(back.read()).hexdigest()[:16]\n"
        "    os.remove(path)\n"
        "    return digest\n"
    )),
    Problem("D2", "io", "disk: 100 × 4 KB files", (
        "small = os.urandom(4096)\n"
        "def work(i):\n"
        "    digest = hashlib.blake2b()\n"
        "    for k in range(100):\n"
        "        with open(f'{DATA}/s{i}_{k}', 'wb') as out:\n"
        "            out.write(small)\n"
        "    for k in range(100):\n"
        "        with open(f'{DATA}/s{i}_{k}', 'rb') as back:\n"
        "            digest.update(back.read())\n"
        "    return digest.hexdigest()[:16]\n"
    )),
    Problem("N1", "io", "network: 4 GETs, sync", (
        "def work(i):\n"
        "    return sum(len(urllib.request.urlopen(f'{URL}{i}/{k}').read()) for k in range(4))\n"
    )),
    Problem("N2", "io", "network: 4 GETs, async", (
        "async def fetch(path):\n"
        "    reader, writer = await asyncio.open_connection(HOST, PORT)\n"
        "    writer.write(f'GET /{path} HTTP/1.0\\r\\nHost: bench\\r\\n\\r\\n'.encode())\n"
        "    body = await reader.read()\n"
        "    writer.close()\n"
        "    await writer.wait_closed()\n"
        "    return len(body)\n"
        "async def work(i):\n"
        "    return sum(await asyncio.gather(*(fetch(f'{i}/{k}') for k in range(4))))\n"
    ), awaits=True),
]
BY_KEY = {problem.key: problem for problem in PROBLEMS}
WORK_NAMES = ("MARK", "SPANS", "time", "work")


def setup_source(problem, workdir, port):
    """The first cell: imports, paths, the problem's data and `work`, and one mark."""
    head = (
        "import asyncio, hashlib, os, sys, time, urllib.request\n"
        "import numpy as np\n"
        f"MARK = {str(workdir / 'marks')!r}\n"
        f"SPANS = {str(workdir / 'spans')!r}\n"
        f"DATA = {str(workdir / 'data')!r}\n"
        f"HOST, PORT = '127.0.0.1', {port}\n"
        f"URL = f'http://{{HOST}}:{{PORT}}/'\n"
        "os.makedirs(DATA, exist_ok=True)\n"
    )
    tail = (
        f"with open({str(workdir / 'kernel')!r}, 'w') as _out:\n"
        "    _out.write(sys.prefix)\n"
        f"print('setup', {problem.key!r})\n"
        "with open(MARK, 'a') as _out:\n"
        "    _out.write(f'{time.time()}\\n')\n"
    )
    return head + problem.setup + tail


def work_source(problem, i):
    call = f"await work({i})" if problem.awaits else f"work({i})"
    return (
        "_t0 = time.time()\n"
        f"r{i} = {call}\n"
        "_t1 = time.time()\n"
        f"print({i}, r{i})\n"
        "with open(SPANS, 'a') as _out:\n"
        "    _out.write(f'{_t0} {_t1}\\n')\n"
        "with open(MARK, 'a') as _out:\n"
        "    _out.write(f'{time.time()}\\n')\n"
    )


def sources(problem, n, workdir, port):
    return [setup_source(problem, workdir, port)] + [work_source(problem, i) for i in range(1, n + 1)]


def marimo_cell(source, args=(), returns=()):
    kind = "async def" if "await " in source else "def"
    body = "".join(f"    {line}\n" if line else "\n" for line in source.splitlines())
    ret = f"    return ({', '.join(returns)},)\n" if returns else "    return\n"
    return f"@app.cell\n{kind} _({', '.join(args)}):\n{body}{ret}\n\n"


def marimo_notebook(cells):
    """Setup returns the names the work cells read; each work cell takes them as arguments."""
    text = marimo_cell(cells[0], returns=WORK_NAMES)
    text += "".join(marimo_cell(source, args=WORK_NAMES) for source in cells[1:])
    return f"import marimo\n\napp = marimo.App()\n\n\n{text}if __name__ == '__main__':\n    app.run()\n"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_GET(self):
        time.sleep(SERVER_DELAY)
        self.send_response(200)
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.end_headers()
        self.wfile.write(PAYLOAD)

    def log_message(self, format, *args):
        pass


def serve(port):
    ThreadingHTTPServer.request_queue_size = 256
    ThreadingHTTPServer.daemon_threads = True
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def start_server():
    """The local HTTP server for N1/N2, in its own process outside every tool's process tree."""
    port = warmbench.free_port()
    proc = subprocess.Popen([sys.executable, __file__, "--serve", str(port)])
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return proc, port
        except OSError:
            time.sleep(0.05)
    proc.kill()
    raise RuntimeError("the local HTTP server did not start")


def spans(workdir):
    try:
        pairs = [tuple(map(float, line.split())) for line in (workdir / "spans").read_text().splitlines()]
    except FileNotFoundError:
        return []
    return sorted(pairs)


def measure(tool, problem, n, port, timeout):
    workdir = Path(tempfile.mkdtemp(prefix="gpwork-"))
    try:
        (workdir / ".venv").symlink_to(sys.prefix)
        cells = sources(problem, n, workdir, port)
        if tool == "gpy":
            run = warmbench.run_gpy(n, workdir, timeout, sources=cells)
        elif tool == "lab":
            run = asyncio.run(warmbench.run_lab(n, workdir, timeout, sources=cells))
        else:
            run = asyncio.run(warmbench.run_marimo(n, workdir, timeout, marimo_notebook(cells), len(cells)))
        kernel = (workdir / "kernel").read_text()
        if os.path.realpath(kernel) != os.path.realpath(sys.prefix):
            raise RuntimeError(f"{tool} ran its kernel in {kernel}, not {sys.prefix}")
        pairs = spans(workdir)
        if len(pairs) != n:
            raise RuntimeError(f"{tool} wrote {len(pairs)} spans for {n} cells")
        work = [end - start for start, end in pairs]
        gaps = [pairs[k + 1][0] - pairs[k][1] for k in range(n - 1)]
        return {
            **run,
            "work_ms": 1000 * statistics.mean(work),
            "gap_ms": 1000 * statistics.mean(gaps) if gaps else None,
            "gap_p50_ms": 1000 * statistics.median(gaps) if gaps else None,
            "span": pairs[-1][1] - pairs[0][0],
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


METRICS = ["ready", "last", "span", "work_ms", "gap_ms", "gap_p50_ms", "mem_mb"]


def summarize(runs):
    row = {}
    for key in METRICS:
        values = [run[key] for run in runs if run.get(key) is not None]
        row[key] = statistics.mean(values) if values else None
        row[key + "_std"] = statistics.stdev(values) if len(values) > 1 else (0.0 if values else None)
    return row


def fmt(value, std):
    return f"{value:10.3f} ± {std:7.3f}" if value is not None else f"{'—':>20}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--problems", default=",".join(BY_KEY))
    parser.add_argument("--cells", default="1,10,100,500,1000")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--tools", default="gpy,lab,marimo")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--json", metavar="FILE", help="write every run and the mean/std per point to FILE")
    args = parser.parse_args()
    if args.serve:
        serve(args.serve)
        return
    for name in BLAS_THREADS:
        os.environ[name] = "1"
    warmbench.warn_if_busy()
    tools = [tool for tool in args.tools.split(",") if warmbench.available(tool)]
    problems = [BY_KEY[key] for key in args.problems.split(",")]
    sizes = [int(n) for n in args.cells.split(",")]
    shuffle = random.Random(args.seed)
    server, port = start_server()
    for tool in tools:
        measure(tool, BY_KEY["C3"], 1, port, args.timeout)
    rows = []
    shown = ["last", "span", "work_ms", "gap_ms", "mem_mb"]
    print(f"{'prob':5}{'tool':8}{'cells':>6}" + "".join(f"{m:>21}" for m in shown), flush=True)
    try:
        for problem in problems:
            for n in sizes:
                runs = {tool: [] for tool in tools}
                for _ in range(args.runs):
                    order = tools[:]
                    shuffle.shuffle(order)
                    for tool in order:
                        runs[tool].append(measure(tool, problem, n, port, args.timeout))
                for tool in tools:
                    row = {"problem": problem.key, "domain": problem.domain, "title": problem.title,
                           "tool": tool, "cells": n, "runs": runs[tool], **summarize(runs[tool])}
                    rows.append(row)
                    print(f"{problem.key:5}{tool:8}{n:6d}" + "".join(fmt(row[m], row[m + '_std']) for m in shown),
                          flush=True)
                if args.json:
                    Path(args.json).write_text(json.dumps(rows, indent=2))
    finally:
        server.kill()


if __name__ == "__main__":
    main()
