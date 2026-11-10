"""Kernel round-trip benchmark: the same work through three paths to the same ipykernel.

    uv run python scripts/kernelbench.py [--paths raw,gpy,server] [--rtt N] [--starts N] [--json]
    uv run --with jupyter-server python3 scripts/kernelbench.py

Paths:
  raw     jupyter_client straight to the kernel over ZMQ: the protocol floor
  gpy     gpython.kernel.Kernel, the wrapper the app uses (includes its start-up helpers)
  server  jupyter_server's REST API and kernel WebSocket, the hop a browser notebook adds
          (skipped unless jupyter_server is importable; the browser's own rendering is not counted)

Metrics: `start` (launch until the kernel answers), `rtt` (`pass` from execute_request until the
kernel is idle and the reply is in; median and p99), `stream` (print 100k lines), `rich` (one
2 MB image/png display_data) and `interrupt` (SIGINT on a busy loop until idle). Times are wall
clock seconds unless the name ends in `_ms`. gpython's rendering of the output is not measured
here; `scripts/bench.py --profile stream` covers that.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import statistics
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

STREAM_CODE = "for i in range(100_000):\n    print(i)"
RICH_CODE = (
    "import base64, os\n"
    "from IPython.display import display\n"
    "display({'image/png': base64.b64encode(os.urandom(1_500_000)).decode()}, raw=True)"
)
BUSY_CODE = "while True:\n    pass"
OUTPUT_KINDS = {"stream", "execute_result", "display_data", "error"}


class RawPath:
    name = "raw"

    async def start(self):
        from jupyter_client.manager import AsyncKernelManager

        self.manager = AsyncKernelManager(kernel_name="python3")
        await self.manager.start_kernel(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.client = self.manager.client()
        self.client.start_channels()
        await self.client.wait_for_ready(timeout=60)

    async def run(self, code):
        msg_id = self.client.execute(code, store_history=True)
        outputs = 0
        while True:
            msg = await self.client.get_iopub_msg(timeout=60)
            if msg["parent_header"].get("msg_id") != msg_id:
                continue
            if msg["msg_type"] in OUTPUT_KINDS:
                outputs += 1
            if msg["msg_type"] == "status" and msg["content"]["execution_state"] == "idle":
                break
        while True:
            reply = await self.client.get_shell_msg(timeout=60)
            if reply["parent_header"].get("msg_id") == msg_id:
                return outputs

    async def interrupt(self):
        await self.manager.interrupt_kernel()

    async def stop(self):
        self.client.stop_channels()
        await self.manager.shutdown_kernel(now=True)


class GpyPath:
    name = "gpy"

    async def start(self):
        sys.path.insert(0, str(ROOT / "src"))
        from gpython.kernel import Kernel

        self.kernel = Kernel()
        await self.kernel.start()

    async def run(self, code):
        outputs = 0

        def count(kind, content):
            nonlocal outputs
            outputs += 1

        await self.kernel.execute(code, count)
        return outputs

    async def interrupt(self):
        await self.kernel.interrupt()

    async def stop(self):
        await self.kernel.shutdown()


class ServerPath:
    name = "server"

    def __init__(self):
        self.token = uuid.uuid4().hex
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.session = uuid.uuid4().hex
        self.process = None

    async def boot(self):
        """Start the Jupyter server process once; its own start-up is reported as `boot`."""
        from tornado.httpclient import AsyncHTTPClient

        self.process = subprocess.Popen(
            [
                sys.executable, "-m", "jupyter_server", "--no-browser", f"--port={self.port}",
                "--ServerApp.ip=127.0.0.1", f"--IdentityProvider.token={self.token}",
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        http = AsyncHTTPClient()
        t0 = time.perf_counter()
        while True:
            try:
                await http.fetch(f"{self.base}/api/status", headers=self._auth())
                return time.perf_counter() - t0
            except Exception:
                if time.perf_counter() - t0 > 60:
                    raise
                await asyncio.sleep(0.02)

    def _auth(self):
        return {"Authorization": f"token {self.token}"}

    async def _post(self, path, body=None):
        from tornado.httpclient import AsyncHTTPClient

        response = await AsyncHTTPClient().fetch(
            f"{self.base}{path}", method="POST", headers=self._auth(), body=json.dumps(body or {})
        )
        return json.loads(response.body or b"{}")

    async def start(self):
        from tornado.websocket import websocket_connect

        self.kernel_id = (await self._post("/api/kernels", {"name": "python3"}))["id"]
        url = f"ws://127.0.0.1:{self.port}/api/kernels/{self.kernel_id}/channels?token={self.token}"
        self.ws = await websocket_connect(url)
        msg_id = self._send("kernel_info_request", {})
        while True:
            msg = await self._read()
            if msg["msg_type"] == "kernel_info_reply" and msg["parent_header"].get("msg_id") == msg_id:
                return

    def _send(self, msg_type, content):
        msg_id = uuid.uuid4().hex
        self.ws.write_message(json.dumps({
            "header": {
                "msg_id": msg_id, "msg_type": msg_type, "username": "bench", "session": self.session,
                "date": "", "version": "5.3",
            },
            "parent_header": {}, "metadata": {}, "content": content, "channel": "shell", "buffers": [],
        }))
        return msg_id

    async def _read(self):
        raw = await self.ws.read_message()
        if raw is None:
            raise ConnectionError("the kernel WebSocket closed")
        return json.loads(raw)

    async def run(self, code):
        msg_id = self._send("execute_request", {
            "code": code, "silent": False, "store_history": True, "user_expressions": {},
            "allow_stdin": False, "stop_on_error": True,
        })
        outputs, idle, replied = 0, False, False
        while not (idle and replied):
            msg = await self._read()
            if msg["parent_header"].get("msg_id") != msg_id:
                continue
            kind = msg["msg_type"]
            if kind in OUTPUT_KINDS:
                outputs += 1
            elif kind == "status" and msg["content"]["execution_state"] == "idle":
                idle = True
            elif kind == "execute_reply":
                replied = True
        return outputs

    async def interrupt(self):
        await self._post(f"/api/kernels/{self.kernel_id}/interrupt")

    async def stop(self):
        from tornado.httpclient import AsyncHTTPClient

        self.ws.close()
        await AsyncHTTPClient().fetch(
            f"{self.base}/api/kernels/{self.kernel_id}", method="DELETE", headers=self._auth()
        )

    def shutdown(self):
        if self.process is not None:
            self.process.terminate()
            self.process.wait(timeout=10)


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]


async def timed(coro):
    t0 = time.perf_counter()
    result = await coro
    return time.perf_counter() - t0, result


async def measure_interrupt(path):
    task = asyncio.create_task(path.run(BUSY_CODE))
    await asyncio.sleep(0.3)
    t0 = time.perf_counter()
    await path.interrupt()
    await task
    return time.perf_counter() - t0


async def bench_path(path, starts, rtt_runs, heavy_runs):
    rows = {}
    if isinstance(path, ServerPath):
        rows["boot"] = await path.boot()
    start_times = []
    for attempt in range(starts):
        seconds, _ = await timed(path.start())
        start_times.append(seconds)
        if attempt < starts - 1:
            await path.stop()
    rows["start"] = statistics.median(start_times)
    for _ in range(20):
        await path.run("pass")
    rtts = [(await timed(path.run("pass")))[0] for _ in range(rtt_runs)]
    rows["rtt_median_ms"] = statistics.median(rtts) * 1000
    rows["rtt_p99_ms"] = percentile(rtts, 0.99) * 1000
    stream = [await timed(path.run(STREAM_CODE)) for _ in range(heavy_runs)]
    rows["stream"] = statistics.median(s for s, _ in stream)
    rows["stream_msgs"] = statistics.median(n for _, n in stream)
    rows["rich"] = statistics.median([(await timed(path.run(RICH_CODE)))[0] for _ in range(heavy_runs)])
    rows["interrupt"] = statistics.median([await measure_interrupt(path) for _ in range(heavy_runs)])
    await path.stop()
    return rows


def make_path(name):
    if name == "raw":
        return RawPath()
    if name == "gpy":
        return GpyPath()
    try:
        import jupyter_server
    except ImportError:
        print("server: skipped (run with `uv run --with jupyter-server python3 scripts/kernelbench.py`)", file=sys.stderr)
        return None
    return ServerPath()


def print_table(results):
    names = list(results)
    metrics = []
    for rows in results.values():
        metrics += [m for m in rows if m not in metrics]
    print("metric".ljust(16) + "".join(n.rjust(12) for n in names))
    for metric in metrics:
        cells = []
        for name in names:
            value = results[name].get(metric)
            cells.append("-".rjust(12) if value is None else f"{value:12.3f}")
        print(metric.ljust(16) + "".join(cells))


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--paths", default="raw,gpy,server")
    parser.add_argument("--starts", type=int, default=3)
    parser.add_argument("--rtt", type=int, default=500)
    parser.add_argument("--heavy", type=int, default=3)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    results = {}
    for name in args.paths.split(","):
        path = make_path(name.strip())
        if path is None:
            continue
        try:
            results[path.name] = await bench_path(path, args.starts, args.rtt, args.heavy)
        finally:
            if isinstance(path, ServerPath):
                path.shutdown()
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print_table(results)


if __name__ == "__main__":
    asyncio.run(main())
