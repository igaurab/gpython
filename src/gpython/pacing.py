"""Pace the screen's repaints while a run streams results.

Textual re-arranges every widget on the screen whenever any cell changes size, and a run
changes one cell after another, so painting at 60 fps during a run of a 500-cell notebook
cost ten times more than the run itself. A PaintPacer holds repaints and lets one through
at a time: the gap before the next is a few times what the last paint took, so a small
notebook still paints almost every frame and a big one a few times a second. A key or a
click lets the next paint through at once (`GPython.on_event`), so what the user does is
never held, and the hold only ever starts right after a paint, so a callback scheduled
after a refresh still sees every region current.
"""

from __future__ import annotations

import time
from typing import Callable

MIN_GAP = 0.03
MAX_GAP = 0.4
GAPS_PER_PAINT = 4.0


class PaintPacer:
    """`with PaintPacer(app):` around work that changes many widgets."""

    def __init__(self, app) -> None:
        self.app = app
        self._active = False
        self._held = False
        self._timer = None
        self._released_at = 0.0
        self._gap = MIN_GAP
        self._after_paint: list[Callable[[], None]] = []

    def __enter__(self) -> PaintPacer:
        self._active = True
        self.app.pacers.add(self)
        self._released_at = time.perf_counter()
        self.app.screen.refresh()
        self.app.call_after_refresh(self._painted)
        return self

    def __exit__(self, *_exc) -> None:
        self._active = False
        self.app.pacers.discard(self)
        self.release()

    def after_paint(self, callback: Callable[[], None]) -> None:
        """Run `callback` once the screen has been painted and every region is current."""
        if self._active:
            self._after_paint.append(callback)
        else:
            self.app.call_after_refresh(callback)

    def release(self) -> None:
        """Let the next paint through now; the hold starts again once it is on screen."""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        if not self._held:
            return
        self._held = False
        self.app._end_batch()
        self._released_at = time.perf_counter()
        self.app.screen.refresh()
        self.app.call_after_refresh(self._painted)

    def _painted(self) -> None:
        callbacks, self._after_paint = self._after_paint, []
        for callback in callbacks:
            callback()
        took = time.perf_counter() - self._released_at
        self._gap = min(MAX_GAP, max(MIN_GAP, GAPS_PER_PAINT * took))
        self._hold()

    def _hold(self) -> None:
        if not self._active or self._held:
            return
        self.app._begin_batch()
        self._held = True
        self._timer = self.app.set_timer(self._gap, self.app.release_paints)
