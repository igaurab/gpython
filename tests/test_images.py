"""Output images as direct kitty placements: sized to fit, moved with scrolling, removed with the widget."""

from __future__ import annotations

import asyncio
import base64
import io
import re

import pytest
from PIL import Image as PILImage
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.geometry import Region
from textual.widgets import Static

from gpython import bigtext, images
from gpython.images import KittyImage, fit, place, transmit, unplace
from gpython.outputs import OutputArea


def png(width: int = 640, height: int = 480) -> bytes:
    out = io.BytesIO()
    PILImage.new("RGB", (width, height), "red").save(out, "PNG")
    return out.getvalue()


@pytest.fixture
def kitty(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(images, "enabled_override", True)
    monkeypatch.setattr(bigtext, "cell_pixels", lambda: (10, 20))
    monkeypatch.setattr(images, "_write", lambda app, data: sent.append(data))
    yield sent


def commands(sent: list[str]) -> list[str]:
    return re.findall(r"\x1b_G([^;\x1b]*)(?:;[^\x1b]*)?\x1b\\", "".join(sent))


def test_fit_keeps_native_size_and_aspect_when_it_fits():
    assert fit((640, 480), 120, (10, 20)) == (64, 24)


def test_fit_shrinks_to_the_container_width():
    assert fit((640, 480), 40, (10, 20)) == (40, 15)


def test_fit_caps_tall_images_at_max_rows():
    cols, rows = fit((800, 2000), 200, (10, 20))
    assert rows == images.MAX_ROWS and cols == 24


def test_transmit_chunks_the_png_and_keeps_the_id():
    data = b"x" * 5000
    seq = transmit(7, data)
    chunks = re.findall(r"\x1b_G([^;]*);([^\x1b]*)\x1b\\", seq)
    assert chunks[0][0] == "a=t,f=100,i=7,q=2,m=1"
    assert chunks[-1][0] == "m=0"
    assert base64.standard_b64decode("".join(c[1] for c in chunks)) == data


def test_place_moves_the_cursor_and_crops_the_hidden_part():
    region = Region(4, 10, 64, 24)
    full = place(7, (640, 480), region, region)
    assert full.startswith("\x1b7\x1b[11;5H\x1b_Ga=p,i=7,p=1,c=64,r=24,C=1,q=2\x1b\\") and full.endswith("\x1b8")
    half = place(7, (640, 480), region, Region(4, 22, 64, 12))
    assert "\x1b[23;5H" in half and "c=64,r=12" in half and "x=0,y=240,w=640,h=240" in half
    assert unplace(7) == "\x1b_Ga=d,d=i,i=7,q=2\x1b\\"


class ScrollApp(App):
    CSS = ".cell-output { height: auto; }"

    def post_display_hook(self) -> None:
        images.sync(self)

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="scroll"):
            yield Static("\n".join(f"line {i}" for i in range(30)), id="filler")
            yield OutputArea()


def run(test):
    async def main():
        app = ScrollApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await test(app, pilot)

    asyncio.run(main())


def test_output_area_uses_kitty_image_and_places_it_when_visible(kitty):
    async def test(app, pilot):
        out = app.query_one(OutputArea)
        out.add("display_data", {"data": {"image/png": base64.b64encode(png(640, 800)).decode(), "text/plain": "<Figure>"}})
        await pilot.pause()
        image = out._blocks[0]
        assert isinstance(image, KittyImage)
        assert (image.size.width, image.size.height) == (48, 30)
        assert not any(c.startswith("a=p") for c in commands(kitty))
        app.query_one("#scroll").scroll_end(animate=False)
        await pilot.pause()
        await pilot.pause()
        sent = commands(kitty)
        assert sent[0].startswith(f"a=t,f=100,i={image.image_id}")
        assert sent[-1].startswith(f"a=p,i={image.image_id},p=1,c={image.size.width},r=")
        assert "x=" in sent[-1]
        region, visible = image._placed
        assert visible.height < region.height

    run(test)


def test_placement_is_removed_when_scrolled_away_and_freed_on_removal(kitty):
    async def test(app, pilot):
        out = app.query_one(OutputArea)
        out.add("display_data", {"data": {"image/png": base64.b64encode(png(200, 100)).decode()}})
        scroll = app.query_one("#scroll")
        scroll.scroll_end(animate=False)
        await pilot.pause()
        await pilot.pause()
        image = out._blocks[0]
        assert image._placed is not None
        before = len(commands(kitty))
        scroll.scroll_home(animate=False)
        await pilot.pause()
        await pilot.pause()
        assert image._placed is None
        assert commands(kitty)[before:] == [f"a=d,d=i,i={image.image_id},q=2"]
        out.clear()
        await pilot.pause()
        await pilot.pause()
        assert commands(kitty)[-1] == f"a=d,d=I,i={image.image_id},q=2"
        assert image not in images._live

    run(test)


def test_gpython_syncs_placements_after_each_frame():
    from gpython.app import GPython

    assert GPython.post_display_hook is not App.post_display_hook


def test_without_kitty_graphics_the_fallback_widget_is_used(monkeypatch):
    monkeypatch.setattr(images, "enabled_override", False)

    async def test(app, pilot):
        out = app.query_one(OutputArea)
        out.add("display_data", {"data": {"image/png": base64.b64encode(png(100, 50)).decode()}})
        await pilot.pause()
        assert not isinstance(out._blocks[0], KittyImage)
        assert out._blocks[0].has_class("output-image")

    run(test)
