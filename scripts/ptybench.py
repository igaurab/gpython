"""Launch ./gpython in a pseudo-terminal, the way a real terminal would, and time it.

    uv run python3 scripts/ptybench.py [--cells N] [--runs N]

Per run: `first_bytes` (the terminal driver starts), `first_frame` (the first cell is fully
on screen), `first_frame_quiet` (nothing painted for 250 ms: all cells mounted) and the
key-to-paint latency of ten `j` presses in milliseconds. Unlike bench.py this includes the
launcher, the Python start-up, the imports and Textual's real driver and frame rate.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import pty
import select
import signal
import struct
import sys
import termios
import time
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT + "/scripts")
from bench import make_notebook

def run(path, keys=10):
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(ROOT)
        env = dict(os.environ, TERM="xterm-256color", COLORTERM="truecolor")
        os.execve(ROOT + "/gpython", ["gpython", str(path)], env)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 45, 140, 0, 0))
    t0 = time.perf_counter()
    marks = {}
    buf = b""

    def pump(quiet, limit):
        """Read until `quiet` seconds of silence (or `limit`); answer terminal queries."""
        nonlocal buf
        got = False
        last = time.perf_counter()
        while True:
            r, _, _ = select.select([fd], [], [], 0.005)
            now = time.perf_counter()
            if r:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    return got
                if not data:
                    return got
                if not got:
                    marks.setdefault("first_bytes", now - t0)
                got = True
                last = now
                buf += data
                if "comment line 7" in buf.decode("utf-8", "replace") and "first_frame" not in marks:
                    marks["first_frame"] = now - t0
                if b"\x1b[c" in data:
                    os.write(fd, b"\x1b[?62;c")
            if got and now - last > quiet or now - t0 > limit:
                return got

    pump(0.25, 5.0)
    marks["first_frame_quiet"] = time.perf_counter() - t0
    lat = []
    for _ in range(keys):
        t = time.perf_counter()
        os.write(fd, b"j")
        start_buf = len(buf)
        while len(buf) == start_buf and time.perf_counter() - t < 1:
            pump(0.0, 1)
        lat.append(time.perf_counter() - t)
        pump(0.05, 1)
    marks["key_to_paint_ms"] = [round(x * 1000, 1) for x in lat]
    os.write(fd, b"\x17q")
    pump(0.3, 2)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    os.waitpid(pid, 0)
    return marks

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cells", type=int, default=60)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    path = make_notebook(args.cells)
    for _ in range(args.runs):
        marks = run(path)
        print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in marks.items()})


if __name__ == "__main__":
    main()
