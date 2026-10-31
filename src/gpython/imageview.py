"""Full-screen view of a cell's output image: zoom, pan, save as PNG, open in the browser.

Opened with `v` in cell mode or `:img`. The picture is the same widget the output area uses
(a kitty placement, or textual_image's fallback) inside a scrolling container: zooming sets the
widget's size in cells and panning scrolls the container. `s` shows a one-line input for the
file name at the bottom; `o` writes the PNG to a temporary folder and opens it in the browser.
`:img save [file]` and `:img open` do the same without opening the screen.
"""

from __future__ import annotations

import io
import tempfile
import webbrowser
from pathlib import Path

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.containers import ScrollableContainer
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from . import bigtext, images

ZOOM_STEP = 1.25
MIN_SCALE, MAX_SCALE = 0.05, 20.0
PAN_COLS, PAN_ROWS = 4, 2
KEYS = "+ - zoom · 0 fit · 1 actual size · h j k l pan · n p next / previous · s save · o browser · q close"


def image_widget(png: bytes):
    """The widget the output area would use for `png`."""
    if images.supported():
        return images.KittyImage(png, classes="output-image")
    from PIL import Image as PILImage
    from textual_image.widget import Image

    return Image(PILImage.open(io.BytesIO(png)), classes="output-image")


def unused_path(folder: Path, stem: str) -> Path:
    """`stem.png` in `folder`, or `stem-2.png`, `stem-3.png`… when that name is taken."""
    path = folder / f"{stem}.png"
    n = 2
    while path.exists():
        path = folder / f"{stem}-{n}.png"
        n += 1
    return path


def save_png(png: bytes, path: Path) -> Path:
    path = path.expanduser()
    if not path.suffix:
        path = path.with_suffix(".png")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)
    return path


def open_in_browser(png: bytes, stem: str) -> Path:
    """Write the PNG under the temp folder and open it with the default browser."""
    folder = Path(tempfile.gettempdir()) / "gpython"
    folder.mkdir(parents=True, exist_ok=True)
    path = unused_path(folder, stem)
    path.write_bytes(png)
    webbrowser.open(path.as_uri())
    return path


class ImageViewer(ModalScreen[None]):
    """`pngs` are a cell's images; `stem` and `folder` name the file `s` offers to save."""

    def __init__(self, pngs: list[bytes], index: int, stem: str, folder: Path) -> None:
        super().__init__()
        self.pngs = list(pngs)
        self.index = index
        self.stem, self.folder = stem, folder
        self.scale = 1.0
        self.widget = None

    @property
    def png(self) -> bytes:
        return self.pngs[self.index]

    @property
    def pixels(self) -> tuple[int, int]:
        return images.png_size(self.png)

    def compose(self) -> ComposeResult:
        yield ScrollableContainer(id="image-view")
        yield Input(id="image-save", placeholder="file name")
        yield Static(id="image-bar")

    def on_mount(self) -> None:
        self._show()

    def _show(self) -> None:
        """Mount the current image already sized to fit, so the terminal places it once."""
        if self.widget is not None:
            self.widget.remove()
        self.widget = image_widget(self.png)
        self.fit()
        self.query_one("#image-view").mount(self.widget)

    def _view_cells(self) -> tuple[int, int]:
        view = self.query_one("#image-view")
        return max(view.size.width or self.app.size.width, 1), max(view.size.height or self.app.size.height - 1, 1)

    def fit(self) -> None:
        """Scale so the whole image shows, as large as the screen allows."""
        width, height = self.pixels
        cell_w, cell_h = bigtext.cell_pixels()
        cols, rows = self._view_cells()
        self.set_scale(min(cols * cell_w / width, rows * cell_h / height))

    def set_scale(self, scale: float) -> None:
        view = self.query_one("#image-view")
        self.scale = max(MIN_SCALE, min(scale, MAX_SCALE))
        cols, rows = self.cells()
        old_w, old_h = max(self.widget.size.width, 1), max(self.widget.size.height, 1)
        fx = (view.scroll_x + view.size.width / 2) / old_w
        fy = (view.scroll_y + view.size.height / 2) / old_h
        self.widget.styles.width = cols
        self.widget.styles.height = rows
        self.call_after_refresh(
            lambda: view.scroll_to(fx * cols - view.size.width / 2, fy * rows - view.size.height / 2, animate=False)
        )
        self._update_bar()

    def cells(self) -> tuple[int, int]:
        width, height = self.pixels
        cell_w, cell_h = bigtext.cell_pixels()
        return max(1, round(width * self.scale / cell_w)), max(1, round(height * self.scale / cell_h))

    def _update_bar(self) -> None:
        width, height = self.pixels
        which = f" · {self.index + 1}/{len(self.pngs)}" if len(self.pngs) > 1 else ""
        status = f"{width}×{height} px · {round(self.scale * 100)}%{which}"
        self.query_one("#image-bar", Static).update(Text.assemble((status, "bold"), "   ", (KEYS, "dim")))

    def _pan(self, dx: int, dy: int) -> None:
        self.query_one("#image-view").scroll_relative(x=dx or None, y=dy or None, animate=False)

    def _step(self, step: int) -> None:
        self.index = (self.index + step) % len(self.pngs)
        self._show()

    def _ask_name(self) -> None:
        field = self.query_one("#image-save", Input)
        field.value = str(unused_path(self.folder, self.stem))
        field.display = True
        field.focus()
        field.cursor_position = len(field.value)

    def _hide_name(self) -> None:
        field = self.query_one("#image-save", Input)
        field.display = False
        self.set_focus(None)

    @on(Input.Submitted, "#image-save")
    def _save(self, event: Input.Submitted) -> None:
        self._hide_name()
        if not event.value.strip():
            return
        try:
            path = save_png(self.png, Path(event.value.strip()))
        except OSError as error:
            self.notify(str(error), severity="error")
            return
        self.notify(f"Saved {path}")

    def _open(self) -> None:
        try:
            open_in_browser(self.png, self.stem)
        except OSError as error:
            self.notify(str(error), severity="error")

    def on_key(self, event: events.Key) -> None:
        if self.query_one("#image-save").display:
            if event.key == "escape":
                event.stop()
                self._hide_name()
            return
        key = event.character if event.is_printable and event.character else event.key
        match key:
            case "+" | "=":
                self.set_scale(self.scale * ZOOM_STEP)
            case "-":
                self.set_scale(self.scale / ZOOM_STEP)
            case "0":
                self.fit()
            case "1":
                self.set_scale(1.0)
            case "h" | "left":
                self._pan(-PAN_COLS, 0)
            case "l" | "right":
                self._pan(PAN_COLS, 0)
            case "k" | "up":
                self._pan(0, -PAN_ROWS)
            case "j" | "down":
                self._pan(0, PAN_ROWS)
            case "n":
                self._step(1)
            case "p":
                self._step(-1)
            case "s":
                self._ask_name()
            case "o":
                self._open()
            case "q" | "escape":
                self.dismiss(None)
            case _:
                return
        event.stop()
        event.prevent_default()
