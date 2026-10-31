"""Output images (matplotlib figures, any `image/png` display) drawn with the kitty graphics protocol.

Terminals that speak the protocol (kitty, Ghostty, Rex, WezTerm, Konsole) receive the PNG once
and show it with a *direct* placement: an `a=p` command issued with the cursor on the image's
top-left cell. Unicode placeholders, which textual_image uses, scroll for free but only kitty
and Ghostty draw them; libghostty-based terminals such as Rex do not. Direct placements work
in all of them, so every output image uses one.

A direct placement is pinned to screen cells and does not follow Textual's scrolling, so after
every frame `sync` (called from `GPython.post_display_hook`) compares where each live
`Placement` is on screen with where its placement is, and re-places, crops or hides it. A
`KittyImage` widget paints blank cells and places its image over them; an `InlinePlacement`
does the same over blank lines inside another widget's text (images in markdown cells, see
`mdimages.py`). Terminals without the protocol keep textual_image's widget (Sixel, half cells
or Unicode blocks).
"""

from __future__ import annotations

import base64
import io
import os
import sys
from functools import cache
from itertools import count
from random import randint
from typing import TYPE_CHECKING

from textual.errors import NoWidget
from textual.geometry import Region
from textual.strip import Strip
from textual.widget import Widget

from . import bigtext

if TYPE_CHECKING:
    from textual.app import App
    from textual.screen import Screen

CHUNK = 4096
MAX_ROWS = 30
MAX_COLS = 400

_ids = count(randint(1, 2**24 - 2**16))
_live: set[Placement] = set()
_any_sent = False
enabled_override: bool | None = None


def supported() -> bool:
    """True when output images can be direct kitty placements: the terminal said so when probed."""
    if enabled_override is not None:
        return enabled_override
    return _terminal_has_tgp()


@cache
def _terminal_has_tgp() -> bool:
    if os.environ.get("GPYTHON_KITTY_IMAGES") == "0" or "TMUX" in os.environ:
        return False
    if sys.modules.get("textual_image._terminal") is None:
        return False
    from textual_image._terminal import TerminalError, probe_terminal

    try:
        return probe_terminal().tgp
    except TerminalError:
        return False


def sixel_supported() -> bool:
    """True when textual_image can draw real pictures with Sixel (iTerm2, WezTerm, foot…)."""
    if enabled_override is not None:
        return False
    return _terminal_has_sixel()


@cache
def _terminal_has_sixel() -> bool:
    if sys.modules.get("textual_image._terminal") is None:
        return False
    from textual_image._terminal import TerminalError, probe_terminal

    try:
        return probe_terminal().sixel
    except TerminalError:
        return False


def png_size(png: bytes) -> tuple[int, int]:
    """Width and height in pixels, from the PNG header alone."""
    from PIL import Image as PILImage

    with PILImage.open(io.BytesIO(png)) as image:
        return image.size


def fit(pixels: tuple[int, int], max_cols: int, cell: tuple[int, int]) -> tuple[int, int]:
    """Cells for an image of `pixels`: never larger than life, at most `max_cols` wide and MAX_ROWS tall."""
    width, height = pixels
    cell_w, cell_h = cell
    cols = max(1, min(-(-width // cell_w), max_cols, MAX_COLS))
    rows = max(1, round(cols * cell_w * height / width / cell_h))
    if rows > MAX_ROWS:
        rows = MAX_ROWS
        cols = max(1, round(rows * cell_h * width / height / cell_w))
    return cols, rows


def _escape(keys: str, payload: str = "") -> str:
    return f"\x1b_G{keys};{payload}\x1b\\" if payload else f"\x1b_G{keys}\x1b\\"


def transmit(image_id: int, png: bytes) -> str:
    """The chunked `a=t` transfer of a PNG, kept by the terminal under `image_id`."""
    data = base64.standard_b64encode(png).decode()
    parts = []
    first = True
    while True:
        chunk, data = data[:CHUNK], data[CHUNK:]
        more = 1 if data else 0
        keys = f"a=t,f=100,i={image_id},q=2,m={more}" if first else f"m={more}"
        parts.append(_escape(keys, chunk))
        first = False
        if not more:
            return "".join(parts)


def place(image_id: int, pixels: tuple[int, int], region: Region, visible: Region) -> str:
    """Show `image_id` over `visible`, the part of `region` (the whole image's cells) that is on screen."""
    width, height = pixels
    scale_x, scale_y = width / region.width, height / region.height
    x = round((visible.x - region.x) * scale_x)
    y = round((visible.y - region.y) * scale_y)
    w = min(width - x, round(visible.width * scale_x))
    h = min(height - y, round(visible.height * scale_y))
    crop = f",x={x},y={y},w={w},h={h}" if visible != region else ""
    keys = f"a=p,i={image_id},p=1,c={visible.width},r={visible.height},C=1,q=2{crop}"
    return f"\x1b7\x1b[{visible.y + 1};{visible.x + 1}H{_escape(keys)}\x1b8"


def unplace(image_id: int) -> str:
    return _escape(f"a=d,d=i,i={image_id},q=2")


def forget(image_id: int) -> str:
    return _escape(f"a=d,d=I,i={image_id},q=2")


def forget_all(app: App) -> None:
    """On exit: free every image this app sent, so nothing lingers in the terminal's memory."""
    global _any_sent
    if _any_sent:
        _any_sent = False
        _write(app, _escape("a=d,d=A,q=2"))


class Placement:
    """A PNG the terminal keeps under `image_id`, shown over the cells `_on_screen` reports."""

    def __init__(self, png: bytes, pixels: tuple[int, int] | None = None) -> None:
        self.png = png
        self.pixels = pixels or png_size(png)
        self.image_id = next(_ids)
        self._sent = False
        self._placed: tuple[Region, Region] | None = None

    def _on_screen(self, screen: Screen) -> tuple[Region, Region] | None:
        """(all cells, visible cells) of this image on `screen`, or None when it is not showing."""
        raise NotImplementedError

    def update_placement(self, screen: Screen) -> str:
        """The escape sequences that bring the terminal's placement in line with the screen, if any."""
        wanted = self._on_screen(screen)
        if wanted == self._placed:
            return ""
        self._placed = wanted
        if wanted is None:
            return unplace(self.image_id)
        out = ""
        if not self._sent:
            global _any_sent
            self._sent = _any_sent = True
            out = transmit(self.image_id, self.png)
        return out + place(self.image_id, self.pixels, *wanted)

    def release(self) -> str:
        """Stop syncing; the sequence that frees the terminal's copy ("" when it was never sent)."""
        _live.discard(self)
        if not self._sent:
            return ""
        self._sent = False
        self._placed = None
        return forget(self.image_id)


def _widget_on_screen(widget: Widget, screen: Screen) -> tuple[Region, Region] | None:
    """(region, clip) of `widget` on `screen`, or None when it is not showing."""
    try:
        if widget.screen is not screen:
            return None
        return screen._compositor.visible_widgets[widget]
    except (KeyError, NoWidget, Exception):
        return None


def _visible(region: Region, clip: Region) -> tuple[Region, Region] | None:
    visible = region.intersection(clip)
    if not visible.width or not visible.height or not region.width or not region.height:
        return None
    return region, visible


class KittyImage(Placement, Widget):
    """One output image: blank cells on screen, the picture placed over them by the terminal."""

    DEFAULT_CSS = """
    KittyImage {
        width: auto;
        height: auto;
    }
    """

    def __init__(self, png: bytes, **kwargs) -> None:
        Widget.__init__(self, **kwargs)
        Placement.__init__(self, png)

    def get_content_width(self, container, viewport) -> int:
        return fit(self.pixels, container.width, bigtext.cell_pixels())[0]

    def get_content_height(self, container, viewport, width: int) -> int:
        return fit(self.pixels, width, bigtext.cell_pixels())[1]

    def render_line(self, y: int) -> Strip:
        return Strip.blank(self.size.width, self.rich_style)

    def on_mount(self) -> None:
        _live.add(self)

    def on_unmount(self) -> None:
        data = self.release()
        if data:
            _write(self.app, data)

    def _on_screen(self, screen: Screen) -> tuple[Region, Region] | None:
        found = _widget_on_screen(self, screen)
        return _visible(*found) if found else None


class InlinePlacement(Placement):
    """An image over `rows` blank lines of `widget`'s content, starting at line `line`, column `x`."""

    def __init__(self, widget: Widget, png: bytes, pixels: tuple[int, int], line: int, x: int, cols: int, rows: int) -> None:
        super().__init__(png, pixels)
        self.widget = widget
        self.line, self.x, self.cols, self.rows = line, x, cols, rows
        _live.add(self)

    def _on_screen(self, screen: Screen) -> tuple[Region, Region] | None:
        found = _widget_on_screen(self.widget, screen)
        if found is None:
            return None
        region, clip = found
        content = region.shrink(self.widget.gutter)
        mine = Region(content.x + self.x, content.y + self.line, self.cols, self.rows)
        return _visible(mine, clip.intersection(content))


def sync(app: App) -> None:
    """After a frame: move, crop, hide or show every live image's placement."""
    if not _live:
        return
    screen = app.screen
    data = "".join(image.update_placement(screen) for image in tuple(_live))
    if data:
        _write(app, data)


def _write(app: App, data: str) -> None:
    driver = getattr(app, "_driver", None)
    if driver is None or driver.is_headless:
        return
    try:
        driver.write(data)
        driver.flush()
    except Exception:
        pass
