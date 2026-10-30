"""Run a cell by its number: `r 6` in cell mode, `space r 6` in vim normal mode.

While it waits, the app sends every key here (GPython.on_event) and the bottom bar
shows what has been typed. The number runs as soon as no longer number can name a
cell, on enter, or after a short pause; with no digit at all it gives up after
TIMEOUT seconds, like a vim key sequence.
"""

from __future__ import annotations

from rich.text import Text

TIMEOUT = 3.0
PAUSE = 0.8


class RunByNumber:
    """One pending "run cell number …" sequence for `pane`."""

    def __init__(self, app, pane) -> None:
        self.app = app
        self.pane = pane
        self.digits = ""
        self._timer = app.set_timer(TIMEOUT, self.cancel)

    def status(self) -> Text:
        return Text.assemble(
            (" RUN ", "bold black on green"),
            f"  run cell: c{self.digits}_",
            ("   press a cell number to run that cell · enter runs · esc cancels", "dim"),
        )

    def key(self, key: str) -> None:
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

    def _restart_timer(self, delay: float, callback) -> None:
        self._timer.stop()
        self._timer = self.app.set_timer(delay, callback)

    def finish(self) -> None:
        number = int(self.digits)
        self.cancel()
        if self.pane.is_attached:
            self.app.run_worker(self.pane.run_number(number), group="run-number")

    def cancel(self) -> None:
        self._timer.stop()
        self.app.end_run_by_number(self)
