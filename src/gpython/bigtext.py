"""Big markdown headings: h1–h3 drawn with a real font, as images, where the terminal can show them.

A terminal cell grid has one text size. Terminals that speak the kitty graphics protocol
(kitty, Ghostty) can show an image across a block of cells through Unicode placeholders:
each cell holds U+10EEEE with diacritics for its row and column, coloured with the image id.
Those cells are ordinary text to Textual, so they scroll, clip and repaint like any other.

Experimental and off by default: `experimental.true_markdown = true` in the config turns
it on. `heading(text, level, max_width)` returns that block of placeholder cells at once and
draws, encodes and sends the image on a background thread, so loading never waits on
Pillow. The terminal fills the cells in when the image arrives. Everywhere else (no kitty
protocol, no font, tests) it returns None and the caller draws the heading as text.
"""

from __future__ import annotations

import base64
import io
import math
import os
import queue
import sys
import threading
from functools import cache
from itertools import count
from random import randint
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from rich.console import Console, ConsoleOptions, RenderResult
    from textual.app import App

LEVELS = {1: (3, 2.0), 2: (2, 1.5), 3: (2, 1.2)}
FONTS = (
    ("/System/Library/Fonts/Helvetica.ttc", 1),
    ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0),
    ("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", 0),
    ("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf", 0),
)
PLACEHOLDER = "\U0010eeee"
CHUNK = 4096
MAX_COLS = 297

_ids = count(randint(1, 2**24 - 2**16))
_blocks: dict[tuple, Block] = {}
_jobs: queue.Queue[tuple[App, int, str, tuple[int, int, int], int, int, int]] = queue.Queue()
_worker: threading.Thread | None = None
enabled = False
enabled_override: bool | None = None


class Block(NamedTuple):
    """The cells of one heading image: `cols` × `rows`, coloured with its id."""

    image_id: int
    cols: int
    rows: int

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        from rich.segment import Segment
        from rich.style import Style

        from textual_image.renderable.tgp import _NUMBER_TO_DIACRITIC as marks

        i = self.image_id
        style = Style(color=f"rgb({(i >> 16) & 255},{(i >> 8) & 255},{i & 255})")
        for row in range(self.rows):
            cells = "".join(f"{PLACEHOLDER}{chr(marks[row])}{chr(marks[col])}" for col in range(self.cols))
            yield Segment(cells, style)
            yield Segment.line()


def supported() -> bool:
    """True when headings can be images: kitty graphics with placeholders, a font, a real terminal."""
    if enabled_override is not None:
        return enabled_override
    return enabled and _terminal_has_tgp() and _font_path() is not None


@cache
def _terminal_has_tgp() -> bool:
    if os.environ.get("GPYTHON_BIG_HEADINGS") == "0" or "TMUX" in os.environ:
        return False
    renderable = sys.modules.get("textual_image.renderable")
    if renderable is None:
        return False
    from textual_image.renderable.tgp import Image as TGPImage

    return renderable.Image is TGPImage


@cache
def _font_path() -> tuple[str, int] | None:
    return next(((path, index) for path, index in FONTS if os.path.exists(path)), None)


@cache
def _font(size: int):
    from PIL import ImageFont

    path, index = _font_path()
    return ImageFont.truetype(path, size, index=index)


def cell_pixels() -> tuple[int, int]:
    from textual_image._terminal import get_cell_size

    size = get_cell_size()
    return (size.width or 10, size.height or 20)


def foreground(app: App) -> tuple[int, int, int]:
    """The theme's text colour as RGB (an ANSI "default" foreground has none: white or black)."""
    from textual.color import Color

    theme = app.current_theme
    try:
        color = Color.parse(theme.foreground) if theme.foreground else None
    except Exception:
        color = None
    if color is None or color.ansi is not None or color.a == 0:
        return (224, 224, 224) if theme.dark else (32, 32, 32)
    return (color.r, color.g, color.b)


def heading(text: str, level: int, max_width: int) -> Block | None:
    """The placeholder block for a heading, or None to draw it as text."""
    from textual._context import active_app

    if level not in LEVELS or not text.strip() or not supported():
        return None
    try:
        app = active_app.get()
    except LookupError:
        return None
    rows, scale = LEVELS[level]
    color = foreground(app)
    cell_w, cell_h = cell_pixels()
    key = (text, level, color, cell_w, cell_h)
    block = _blocks.get(key)
    if block is None:
        size = round(cell_h * scale)
        cols = math.ceil((_font(size).getlength(text) + cell_w / 2) / cell_w)
        block = _blocks[key] = Block(next(_ids), cols, rows)
        _send_later(app, block.image_id, text, color, size, cols, rows)
    return block if block.cols <= min(max_width, MAX_COLS) else None


def _send_later(app: App, image_id: int, text: str, color: tuple[int, int, int], size: int, cols: int, rows: int) -> None:
    global _worker
    _jobs.put((app, image_id, text, color, size, cols, rows))
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_work, name="gpython-big-headings", daemon=True)
        _worker.start()


def _work() -> None:
    while True:
        try:
            app, image_id, text, color, size, cols, rows = _jobs.get(timeout=5)
        except queue.Empty:
            return
        driver = getattr(app, "_driver", None)
        if driver is None:
            continue
        try:
            driver.write(_sequence(image_id, _png(text, color, size, cols, rows), cols, rows))
        except Exception:
            continue


def _png(text: str, color: tuple[int, int, int], size: int, cols: int, rows: int) -> bytes:
    from PIL import Image, ImageDraw

    cell_w, cell_h = cell_pixels()
    image = Image.new("RGBA", (cols * cell_w, rows * cell_h), (0, 0, 0, 0))
    font = _font(size)
    top, bottom = font.getbbox("Hg")[1], font.getbbox("Hg")[3]
    y = (rows * cell_h - (bottom - top)) / 2 - top
    ImageDraw.Draw(image).text((0, y), text, font=font, fill=(*color, 255))
    out = io.BytesIO()
    image.save(out, format="PNG", compress_level=1)
    return out.getvalue()


def _sequence(image_id: int, png: bytes, cols: int, rows: int) -> str:
    """kitty graphics: transmit the PNG in chunks and make a virtual placement (U=1) of cols × rows."""
    from textual_image._terminal import prepare_terminal_sequence

    data = base64.standard_b64encode(png).decode()
    parts = []
    first = True
    while True:
        chunk, data = data[:CHUNK], data[CHUNK:]
        more = 1 if data else 0
        keys = f"a=T,U=1,f=100,i={image_id},c={cols},r={rows},q=2,m={more}" if first else f"m={more}"
        parts.append(prepare_terminal_sequence(f"\x1b_G{keys};{chunk}\x1b\\"))
        first = False
        if not more:
            return "".join(parts)
