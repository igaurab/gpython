"""The bottom bar: the mode badge on the left and tmux-style modules on the right.

Modules are `clock`, `host`, `cpu`, `ram` and `gpu`, all on by default. In the config
`status.cpu = false` drops one, `status.layout = "mode clock host cpu ram gpu"` orders
them, `status.clock_format` is a strftime pattern. The clock ticks once a second; cpu,
ram and gpu are sampled every two seconds in a thread (psutil, and nvidia-smi when it
exists), so the first frame never waits on them. A module whose source is missing
(no GPU tool, psutil import failure) disappears from the bar.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import time

from rich.text import Text
from textual.widgets import Static

MODULES = ("mode", "clock", "host", "cpu", "ram", "gpu")
DEFAULT_LAYOUT = "mode clock host cpu ram gpu"
DEFAULT_CLOCK_FORMAT = "%a %d %b %H:%M"
SAMPLE_SECONDS = 2.0
SEPARATOR = " · "


def enabled_modules(settings: dict) -> list[str]:
    """The modules the config leaves on, in layout order; unknown names are skipped."""
    layout = settings.get("status.layout", DEFAULT_LAYOUT)
    names = layout.split() if isinstance(layout, str) else list(MODULES)
    return [name for name in names if name in MODULES and settings.get(f"status.{name}", True) is not False]


def _gigabytes(n: int) -> str:
    return f"{n / 1024 ** 3:.1f}G"


def sample_cpu_ram(primed: bool) -> dict[str, str]:
    """cpu and ram readings from psutil, imported here so start-up never pays for it. psutil's
    first cpu reading is always 0 (it measures since the previous call), so the first sample
    only primes it and reports ram."""
    import psutil

    memory = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=None)
    readings = {"ram": f"ram {_gigabytes(memory.used)}/{_gigabytes(memory.total)}"}
    if primed:
        readings["cpu"] = f"cpu {cpu:.0f}%"
    return readings


def sample_gpu() -> str | None:
    """`gpu 12% 1.2G/8.0G` from nvidia-smi; None when there is no such tool or it fails."""
    tool = shutil.which("nvidia-smi")
    if tool is None:
        return None
    try:
        out = subprocess.run(
            [tool, "--query-gpu=utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2,
        ).stdout.strip().splitlines()
        util, used, total = (float(v) for v in out[0].split(","))
    except Exception:
        return None
    return f"gpu {util:.0f}% {used / 1024:.1f}G/{total / 1024:.1f}G"


class StatusBar(Static):
    """One row: `[MODE]  …  clock · host · cpu · ram · gpu`, right part right-aligned."""

    def __init__(self, settings: dict, **kwargs) -> None:
        super().__init__(**kwargs)
        self.modules = enabled_modules(settings)
        self.clock_format = settings.get("status.clock_format", DEFAULT_CLOCK_FORMAT)
        self.mode: Text | None = None
        self.values: dict[str, str] = {}
        self._text: Text | None = None
        self._started = False
        self._primed = False
        if "host" in self.modules:
            self.values["host"] = socket.gethostname().split(".")[0]

    def start(self) -> None:
        """Begin ticking; called by the app after the first frame."""
        if self._started:
            return
        self._started = True
        if "clock" in self.modules:
            self._tick_clock()
            self.set_interval(1, self._tick_clock)
        if any(m in self.modules for m in ("cpu", "ram", "gpu")):
            self.set_interval(SAMPLE_SECONDS, self._sample)
            self._sample()

    def set_mode(self, mode: Text | None) -> None:
        self.mode = mode
        self.repaint()

    def _tick_clock(self) -> None:
        now = time.strftime(self.clock_format)
        if self.values.get("clock") != now:
            self.values["clock"] = now
            self.repaint()

    def _sample(self) -> None:
        self.run_worker(self._sample_in_thread, thread=True, exclusive=True, group="statusbar", exit_on_error=False)

    def _sample_in_thread(self) -> None:
        readings: dict[str, str] = {}
        if "cpu" in self.modules or "ram" in self.modules:
            try:
                readings.update(sample_cpu_ram(self._primed))
                self._primed = True
            except Exception:
                self.modules = [m for m in self.modules if m not in ("cpu", "ram")]
        if "gpu" in self.modules:
            gpu = sample_gpu()
            if gpu is None:
                self.modules = [m for m in self.modules if m != "gpu"]
            else:
                readings["gpu"] = gpu
        self.app.call_from_thread(self._apply, readings)

    def _apply(self, readings: dict[str, str]) -> None:
        self.values.update(readings)
        self.repaint()

    def on_resize(self) -> None:
        self.repaint()

    def compose_text(self, width: int) -> Text:
        left = self.mode if "mode" in self.modules and self.mode is not None else Text("")
        right = Text(SEPARATOR, "dim").join(Text(self.values[m], "dim") for m in self.modules if m != "mode" and m in self.values)
        gap = max(2, width - left.cell_len - right.cell_len - 1)
        return Text.assemble(left, " " * gap, right)

    def repaint(self) -> None:
        text = self.compose_text(self.size.width)
        if self._text is not None and text.plain == self._text.plain and text.spans == self._text.spans:
            return
        self._text = text
        self.update(text, layout=False)
