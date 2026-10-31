"""Pictures in markdown cells: `![alt](url-or-path)` drawn in place of the text.

A link `[text](x.png)` whose target has a picture's extension is a picture too
(`promote_links`); it falls back to a link when what arrives is not one.

A picture is fetched (http, https, data URLs) or read (a path, relative to the notebook) on
a background thread, decoded with Pillow into PNG frames (one for a still picture, up to
MAX_FRAMES with their durations for an animated GIF; anything over MAX_SIDE pixels is shrunk)
and kept in memory for the session; downloads are also kept in `~/.cache/gpython/images/`
so a notebook opens with its pictures the second time. Until a picture arrives the cell
shows "⌛ alt"; when it fails, "🌆 alt" with the reason. `picture` is what the markdown
renderer asks; `render` draws one.

Where the terminal has kitty graphics the renderer leaves blank rows for the picture, tagged
in their style's meta, and `MarkdownView` places the picture over them with an
`images.InlinePlacement`, which also runs the animation. Elsewhere textual_image's half-cell
renderable draws the first frame in colour blocks.
"""

from __future__ import annotations

import io
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from . import images

if TYPE_CHECKING:
    from rich.console import RenderResult
    from textual.app import App

    from .markdown import MarkdownView

MAX_BYTES = 32 * 2**20
MAX_SIDE = 2048
MAX_SIDE_ANIMATED = 1024
MAX_FRAMES = 120
MIN_FRAME_SECONDS = 0.02
TIMEOUT = 20
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
META_KEY = "gp_picture"
NOT_A_PICTURE = "not a picture"
PICTURE_SUFFIXES = (".png", ".gif", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".ico")


class Picture(NamedTuple):
    """`png` is the (first) frame; `frames` are (png, seconds) for each frame of an animation, else ()."""

    png: bytes
    pixels: tuple[int, int]
    frames: tuple[tuple[bytes, float], ...] = ()


_pictures: dict[str, Picture | str] = {}
_waiting: dict[str, set[MarkdownView]] = {}
_lock = threading.Lock()


def is_url(src: str) -> bool:
    return src.startswith(("http://", "https://"))


def looks_like_picture(src: str) -> bool:
    """True when `src` ends in a picture's file extension (before any ?query or #fragment)."""
    return src.split("?", 1)[0].split("#", 1)[0].lower().endswith(PICTURE_SUFFIXES)


def promote_links(tokens: list) -> None:
    """Turn, in place, every `[text](x.png)` link whose target looks like a picture into an image token."""
    from markdown_it.token import Token

    for token in tokens:
        children = token.children
        if token.type != "inline" or not children:
            continue
        out: list[Token] = []
        i = 0
        while i < len(children):
            child = children[i]
            is_link = child.type == "link_open" and i + 2 < len(children) and children[i + 1].type == "text" and children[i + 2].type == "link_close"
            if is_link and looks_like_picture(str(child.attrs.get("href", ""))):
                src, alt = str(child.attrs["href"]), children[i + 1].content
                out.append(Token("image", "img", 0, attrs={"src": src, "alt": ""}, content=alt, children=[Token("text", "", 0, content=alt)]))
                i += 3
            else:
                out.append(child)
                i += 1
        token.children = out


def folder_of(app: App | None) -> Path:
    """Where a relative path in a markdown cell starts: the notebook's folder, else the cwd."""
    path = getattr(getattr(app, "pane", None), "path", None)
    return Path(path).parent if path else Path.cwd()


def key_for(src: str, folder: Path) -> str:
    if is_url(src) or src.startswith("data:"):
        return src
    return str(local_path(src, folder))


def local_path(src: str, folder: Path) -> Path:
    path = Path(src.removeprefix("file://")).expanduser()
    return path if path.is_absolute() else folder / path


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "gpython" / "images"


def picture(src: str, view: MarkdownView) -> Picture | str | None:
    """`src` as a Picture, the reason it could not be loaded, or None while it is on its way.

    A None answer starts the load (once per source); `view.pictures_changed()` is called on
    the app thread when it ends.
    """
    folder = folder_of(view.app)
    key = key_for(src, folder)
    found = _pictures.get(key)
    if found is not None:
        return found
    with _lock:
        found = _pictures.get(key)
        if found is not None:
            return found
        waiting = _waiting.get(key)
        if waiting is None:
            _waiting[key] = {view}
            threading.Thread(target=_load, args=(key, src, folder, view.app), daemon=True, name="gpython-picture").start()
        else:
            waiting.add(view)
    return None


def cached(src: str, app: App | None) -> Picture | None:
    """`src` if it has already been loaded, else None (nothing is started)."""
    found = _pictures.get(key_for(src, folder_of(app)))
    return found if isinstance(found, Picture) else None


def _load(key: str, src: str, folder: Path, app: App) -> None:
    try:
        result: Picture | str = _fetch(src, folder)
    except Exception as error:
        result = explain(error)
    with _lock:
        _pictures[key] = result
        views = _waiting.pop(key, set())
    if views:
        try:
            app.call_from_thread(_wake, views)
        except Exception:
            pass


def _wake(views: set[MarkdownView]) -> None:
    for view in views:
        view.pictures_changed()


def _fetch(src: str, folder: Path) -> Picture:
    if src.startswith("data:"):
        return to_png(_data_url(src))
    if not is_url(src):
        return to_png(local_path(src, folder).read_bytes())
    import hashlib

    path = cache_dir() / (hashlib.sha1(src.encode()).hexdigest() + ".raw")
    if path.is_file():
        return to_png(path.read_bytes())
    raw = download(src)
    found = to_png(raw)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    except OSError:
        pass
    return found


def download(url: str) -> bytes:
    """The body at `url`, at most MAX_BYTES; a web page is not a picture (tests replace this)."""
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": "gpython"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        if response.headers.get_content_maintype() == "text":
            raise ValueError(NOT_A_PICTURE)
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"larger than {MAX_BYTES // 2**20} MB")
    return data


def _data_url(src: str) -> bytes:
    import base64
    from urllib.parse import unquote_to_bytes

    header, _, body = src.partition(",")
    return base64.b64decode(body) if header.endswith(";base64") else unquote_to_bytes(body)


def to_png(raw: bytes) -> Picture:
    """`raw` (PNG, GIF, JPEG, WebP… any format Pillow reads) as PNG frames, shrunk to MAX_SIDE.

    An animation keeps up to MAX_FRAMES frames with their durations (never under
    MIN_FRAME_SECONDS, as browsers do), each at most MAX_SIDE_ANIMATED wide or tall.
    """
    from PIL import Image as PILImage, ImageSequence

    with PILImage.open(io.BytesIO(raw)) as image:
        if raw[:8] == PNG_MAGIC and max(image.size) <= MAX_SIDE:
            return Picture(raw, image.size)
        if getattr(image, "n_frames", 1) < 2:
            frame = _encode(image, MAX_SIDE)
            return Picture(frame.png, frame.pixels)
        frames = []
        for member in ImageSequence.Iterator(image):
            if len(frames) == MAX_FRAMES:
                break
            seconds = max(member.info.get("duration", 100) / 1000, MIN_FRAME_SECONDS)
            frames.append((_encode(member, MAX_SIDE_ANIMATED), seconds))
    return Picture(frames[0][0].png, frames[0][0].pixels, tuple((f.png, s) for f, s in frames))


def _encode(image, max_side: int) -> Picture:
    frame = image.convert("RGBA")
    if max(frame.size) > max_side:
        frame.thumbnail((max_side, max_side))
    out = io.BytesIO()
    frame.save(out, format="PNG", compress_level=1)
    return Picture(out.getvalue(), frame.size)


def explain(error: Exception) -> str:
    """A few words on why a picture did not load."""
    import urllib.error

    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code}"
    if isinstance(error, urllib.error.URLError):
        return f"no connection: {getattr(error.reason, 'strerror', None) or error.reason}"
    if isinstance(error, FileNotFoundError):
        return "file not found"
    if isinstance(error, TimeoutError):
        return "timed out"
    if type(error).__name__ == "UnidentifiedImageError":
        return NOT_A_PICTURE
    text = str(error) or type(error).__name__
    return text if len(text) <= 60 else text[:57] + "..."


def cell_pixels() -> tuple[int, int]:
    from . import bigtext

    try:
        return bigtext.cell_pixels()
    except Exception:
        return (10, 20)


def render(found: Picture, registry: list[tuple[Picture, int, int]], max_width: int) -> RenderResult:
    """Draw `found` at most `max_width` wide: blank tagged rows for a kitty placement
    (appended to `registry`, the tag is its index) or half-cell colour blocks."""
    cols, rows = images.fit(found.pixels, max_width, cell_pixels())
    if images.supported():
        from rich.segment import Segment
        from rich.style import Style

        style = Style(meta={META_KEY: len(registry)})
        registry.append((found, cols, rows))
        blank = " " * cols
        for _ in range(rows):
            yield Segment(blank, style)
            yield Segment.line()
    else:
        from textual_image.renderable.halfcell import Image

        yield Image(io.BytesIO(found.png), width=cols, height=rows)


def find_tagged(lines: list[list]) -> list[tuple[int, int, int]]:
    """(line, column, tag) for the first row of every tagged block in rendered `lines`."""
    found: list[tuple[int, int, int]] = []
    seen: set[int] = set()
    for y, line in enumerate(lines):
        x = 0
        for segment in line:
            style = segment.style
            if style is not None and style._meta is not None:
                tag = style.meta.get(META_KEY)
                if tag is not None and tag not in seen:
                    seen.add(tag)
                    found.append((y, x, tag))
            x += segment.cell_length
    return found
