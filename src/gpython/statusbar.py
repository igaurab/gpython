"""The bottom bar: the mode badge on the left and tmux-style modules on the right.

Modules are `clock`, `host`, `cpu`, `ram` and `gpu`, all on by default. In the config
`status.cpu = false` drops one, `status.layout = "mode clock host cpu ram gpu"` orders
them, `status.clock_format` is a strftime pattern and `status.style` is `btop` (the
default: a sparkline of recent cpu and gpu samples drawn as vertical bars, a block
meter for ram, coloured green, yellow and red by load) or `tmux` (one level glyph in
brackets, as tmux-cpu draws it). The clock ticks once a second; cpu, ram and gpu are
sampled every two seconds on a daemon thread (psutil, and nvidia-smi when it exists),
never a Textual worker, so tests that wait for the app's workers do not wait on the bar,
and the thread starts after the first frame. A module whose source is missing (no GPU
tool, psutil import failure) disappears from the bar.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import threading
import time
from collections import deque

from rich.text import Text
from textual.widgets import Static

MODULES = ("mode", "clock", "host", "cpu", "ram", "gpu")
DEFAULT_LAYOUT = "mode clock host cpu ram gpu"
DEFAULT_CLOCK_FORMAT = "%a %d %b %H:%M"
STYLES = ("btop", "tmux")
SAMPLE_SECONDS = 2.0
SEPARATOR = " · "
LEVELS = "▁▂▃▄▅▆▇█"
HISTORY = 12
METER_CELLS = 10
LOW, HIGH = 30, 80


def _load_style(percent: float) -> str:
    return "bold green" if percent < LOW else "bold yellow" if percent < HIGH else "bold red"


def _level(percent: float) -> str:
    return LEVELS[min(len(LEVELS) - 1, max(0, int(percent / 100 * len(LEVELS))))]


def gauge(label: str, percent: float, extra: str = "") -> Text:
    """tmux-cpu style, `cpu [▃] 51%`: one level glyph and the number in the load's colour."""
    style = _load_style(percent)
    return Text.assemble((f"{label} [", "dim"), (_level(percent), style), ("] ", "dim"), (f"{percent:.0f}%", style), (f" {extra}" if extra else "", "dim"))


def sparkline(label: str, history: list[float], extra: str = "") -> Text:
    """btop style, `cpu ▂▃▅▃▂ 33%`: the recent samples as vertical bars, each in its own load
    colour, padded on the left to HISTORY cells so the bar does not move as samples arrive."""
    bars = Text("".join(" " for _ in range(HISTORY - len(history))), "dim")
    for value in history:
        bars.append(_level(value), _load_style(value))
    current = history[-1] if history else 0.0
    return Text.assemble((f"{label} ", "dim"), bars, (f" {current:.0f}%", _load_style(current)), (f" {extra}" if extra else "", "dim"))


def meter(label: str, percent: float, extra: str = "") -> Text:
    """btop style, `ram [▪▪▪▪▪▪    ] 55% 14.1G/32.0G`: a bracketed track, filled cells coloured
    by their own position along it (green, then yellow, then red), the rest blank."""
    filled = round(percent / 100 * METER_CELLS)
    cells = Text("[", "dim")
    for i in range(METER_CELLS):
        position = (i + 0.5) / METER_CELLS * 100
        cells.append("▪" if i < filled else " ", _load_style(position) if i < filled else "dim")
    cells.append("]", "dim")
    return Text.assemble((f"{label} ", "dim"), cells, (f" {percent:.0f}%", _load_style(percent)), (f" {extra}" if extra else "", "dim"))


def enabled_modules(settings: dict) -> list[str]:
    """The modules the config leaves on, in layout order; unknown names are skipped."""
    layout = settings.get("status.layout", DEFAULT_LAYOUT)
    names = layout.split() if isinstance(layout, str) else list(MODULES)
    return [name for name in names if name in MODULES and settings.get(f"status.{name}", True) is not False]


def _gigabytes(n: int) -> str:
    return f"{n / 1024 ** 3:.1f}G"


def sample_cpu_ram(primed: bool) -> dict[str, tuple[float, str]]:
    """(percent, size text) for cpu and ram from psutil, imported here so start-up never pays
    for it. psutil's first cpu reading is always 0 (it measures since the previous call), so
    the first sample only primes it and reports ram."""
    import psutil

    memory = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=None)
    readings = {"ram": (memory.percent, f"{_gigabytes(memory.used)}/{_gigabytes(memory.total)}")}
    if primed:
        readings["cpu"] = (cpu, "")
    return readings


def sample_gpu() -> tuple[float, str] | None:
    """(utilisation, `1.2G/8.0G`) from nvidia-smi; None when there is no such tool or it fails."""
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
    return util, f"{used / 1024:.1f}G/{total / 1024:.1f}G"


class StatusBar(Static):
    """One row: `[MODE]  …  clock · host · cpu · ram · gpu`, right part right-aligned."""

    def __init__(self, settings: dict, **kwargs) -> None:
        super().__init__(**kwargs)
        self.modules = enabled_modules(settings)
        self.clock_format = settings.get("status.clock_format", DEFAULT_CLOCK_FORMAT)
        style = settings.get("status.style", "btop")
        self.style_name = style if style in STYLES else "btop"
        self.mode: Text | None = None
        self.values: dict[str, str | Text] = {}
        self.history: dict[str, deque[float]] = {m: deque(maxlen=HISTORY) for m in ("cpu", "gpu")}
        self._text: Text | None = None
        self._started = False
        self._primed = False
        self._stop: threading.Event | None = None
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
            self._stop = threading.Event()
            threading.Thread(target=self._sample_loop, name="statusbar", daemon=True).start()

    def on_unmount(self) -> None:
        if self._stop is not None:
            self._stop.set()

    def set_mode(self, mode: Text | None) -> None:
        self.mode = mode
        self.repaint()

    def _tick_clock(self) -> None:
        now = time.strftime(self.clock_format)
        if self.values.get("clock") != now:
            self.values["clock"] = now
            self.repaint()

    def _sample_loop(self) -> None:
        while self._stop is not None and not self._stop.is_set():
            readings = self._sample()
            try:
                self.app.call_from_thread(self._apply, readings)
            except Exception:
                return
            self._stop.wait(SAMPLE_SECONDS)

    def _sample(self) -> dict[str, tuple[float, str]]:
        readings: dict[str, tuple[float, str]] = {}
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
        return readings

    def _apply(self, readings: dict[str, tuple[float, str]]) -> None:
        for name, (percent, extra) in readings.items():
            if name in self.history:
                self.history[name].append(percent)
            self.values[name] = self.draw(name, percent, extra)
        self.repaint()

    def draw(self, name: str, percent: float, extra: str) -> Text:
        if self.style_name == "tmux":
            return gauge(name, percent, extra)
        if name in self.history:
            return sparkline(name, list(self.history[name]), extra)
        return meter(name, percent, extra)

    def on_resize(self) -> None:
        self.repaint()

    def compose_text(self, width: int) -> Text:
        left = self.mode if "mode" in self.modules and self.mode is not None else Text("")
        parts = [self.values[m] for m in self.modules if m != "mode" and m in self.values]
        right = Text(SEPARATOR, "dim").join(part if isinstance(part, Text) else Text(part, "dim") for part in parts)
        gap = max(2, width - left.cell_len - right.cell_len - 1)
        return Text.assemble(left, " " * gap, right)

    def repaint(self) -> None:
        text = self.compose_text(self.size.width)
        if self._text is not None and text.plain == self._text.plain and text.spans == self._text.spans:
            return
        self._text = text
        self.update(text, layout=False)
