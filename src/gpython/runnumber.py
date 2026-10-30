"""Act on a cell by its number: `r 6` runs c6 (cell mode and vim normal mode, where `space r 6` is a synonym), `g 6` goes to c6 (cell mode).

While a sequence waits, the app sends every key here (GPython.on_event) and the bottom bar
shows what has been typed. The number is used as soon as no longer number can name a
cell, on enter, or after a short pause; with no digit at all it gives up after
TIMEOUT seconds, like a vim key sequence. Before any digit, a sequence may also take
letters. After `r`: `r` or `c` runs the current cell, `C` the cells from the first one
down to it, `a` the cells after it, `A` all of them, `s` the stale ones, `k` restarts the
kernel, `R` restarts it and runs all, `i` interrupts; the focus never moves. After `g`:
`g` goes to the first cell (or the counted one), `t` / `T` switch tabs.
"""

from __future__ import annotations

from rich.text import Text

TIMEOUT = 3.0
PAUSE = 0.8


class CellNumberSequence:
    """One pending "<verb> cell number …" sequence for `pane`; subclasses say what the number does."""

    badge = ""
    badge_style = ""
    verb = ""
    letters_hint = ""
    finish_hint = "enter"

    def __init__(self, app, pane, count: int | None = None) -> None:
        self.app = app
        self.pane = pane
        self.count = count
        self.digits = ""
        self._timer = app.set_timer(TIMEOUT, self.cancel)

    def status(self) -> Text:
        hint = f"   number · {self.letters_hint} · {self.finish_hint} · esc cancels" if not self.digits else f"   {self.finish_hint} · esc cancels"
        return Text.assemble((f" {self.badge} ", self.badge_style), f"  {self.verb} cell: c{self.digits}_", (hint, "dim"))

    def key(self, key: str) -> None:
        if not self.digits and self.letter(key):
            return
        if key.isdigit():
            self.digits += key
            number = int(self.digits)
            if number == 0 or number * 10 > self.pane.cell_count():
                self.finish()
                return
            self._restart_timer(PAUSE, self.finish)
            self.app.update_status()
        elif key == "backspace" and self.digits:
            self.digits = self.digits[:-1]
            self._restart_timer(PAUSE if self.digits else TIMEOUT, self.finish if self.digits else self.cancel)
            self.app.update_status()
        elif key == "enter" and self.digits:
            self.finish()
        else:
            self.cancel()

    def letter(self, key: str) -> bool:
        """A letter typed before any digit; True if it ended the sequence with an action."""
        return False

    def _restart_timer(self, delay: float, callback) -> None:
        self._timer.stop()
        self._timer = self.app.set_timer(delay, callback)

    def finish(self) -> None:
        number = int(self.digits)
        self.cancel()
        if self.pane.is_attached:
            self.perform(number)

    def perform(self, number: int) -> None:
        raise NotImplementedError

    def cancel(self) -> None:
        self._timer.stop()
        self.app.end_number_sequence(self)


class RunByNumber(CellNumberSequence):
    badge = "RUN"
    badge_style = "bold black on green"
    verb = "run"
    letters_hint = "r/c this · C to here · a after · A all · s stale · k restart · R restart+all · i stop"
    finish_hint = "enter runs"

    def letter(self, key: str) -> bool:
        action = RUN_LETTERS.get(key)
        if action is None:
            return False
        self.cancel()
        if self.pane.is_attached:
            action(self.pane)
        return True

    def perform(self, number: int) -> None:
        self.app.run_worker(self.pane.run_number(number), group="run-number")


def _in_worker(method_name: str, group: str = "run"):
    def start(pane) -> None:
        pane.run_worker(getattr(pane, method_name)(), group=group)

    return start


RUN_LETTERS = {
    "r": _in_worker("run_cell"),
    "c": _in_worker("run_cell"),
    "C": lambda pane: pane.run_relative(False, inclusive=True),
    "a": lambda pane: pane.run_relative(True),
    "A": lambda pane: pane.run_all(),
    "s": lambda pane: pane.run_stale(),
    "k": _in_worker("restart_kernel", "kernel"),
    "R": _in_worker("restart_and_run_all", "kernel"),
    "i": _in_worker("interrupt", "kernel"),
}


class GotoByNumber(CellNumberSequence):
    badge = "GOTO"
    badge_style = "bold black on cyan"
    verb = "go to"
    letters_hint = "g first · t / T next / previous tab"
    finish_hint = "enter goes"

    def letter(self, key: str) -> bool:
        if key not in ("g", "t", "T"):
            return False
        self.cancel()
        if not self.pane.is_attached:
            return True
        if key == "g":
            self.pane.goto_cell(self.count or 1, "cell")
        elif key == "t":
            self.app.tab_switch(self.count, 1)
        else:
            self.app.tab_switch(None, -(self.count or 1))
        return True

    def perform(self, number: int) -> None:
        self.pane.goto_cell(number, "cell", clamp=False)
