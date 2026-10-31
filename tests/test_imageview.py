"""The image viewer: v opens a cell's figure full screen; zoom, pan, save, open in the browser, :img."""

from __future__ import annotations

import asyncio
import base64
import io
from pathlib import Path

import pytest
from PIL import Image as PILImage
from textual.widgets import Input

from gpython import bigtext, images, imageview
from gpython.app import GPython
from gpython.imageview import ImageViewer, unused_path


def png(width: int = 640, height: int = 480) -> bytes:
    out = io.BytesIO()
    PILImage.new("RGB", (width, height), "blue").save(out, "PNG")
    return out.getvalue()


PNG = png()


@pytest.fixture(autouse=True)
def kitty(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(images, "enabled_override", True)
    monkeypatch.setattr(bigtext, "cell_pixels", lambda: (10, 20))
    monkeypatch.setattr(images, "_write", lambda app, data: None)


def ui(tmp_path, test, with_image=True):
    path = tmp_path / "nb.py"
    path.write_text("# %%\nx = 1\n\n# %%\ny = 2\n")

    async def main():
        app = GPython(path)
        async with app.run_test(size=(120, 40)) as pilot:
            await app.loaded.wait()
            await pilot.pause()
            if with_image:
                data = {"image/png": base64.b64encode(PNG).decode(), "text/plain": "<Figure>"}
                app.cells()[0].add_output("display_data", {"data": data, "metadata": {}})
                await pilot.pause()
            app.cells()[0].focus()
            await pilot.pause()
            await test(app, pilot)

    asyncio.run(main())


def viewer(app) -> ImageViewer:
    assert isinstance(app.screen, ImageViewer)
    return app.screen


def test_unused_path_counts_up(tmp_path):
    assert unused_path(tmp_path, "nb-c1") == tmp_path / "nb-c1.png"
    (tmp_path / "nb-c1.png").write_bytes(b"")
    (tmp_path / "nb-c1-2.png").write_bytes(b"")
    assert unused_path(tmp_path, "nb-c1") == tmp_path / "nb-c1-3.png"


def test_v_opens_the_viewer_fitted_and_zooms(tmp_path):
    async def test(app, pilot):
        await pilot.press("v")
        await pilot.pause()
        view = viewer(app)
        await pilot.pause()
        assert view.widget.gp_png if hasattr(view.widget, "gp_png") else True
        assert (view.widget.size.width, view.widget.size.height) == (104, 39)
        await pilot.press("1")
        await pilot.pause()
        assert (view.widget.size.width, view.widget.size.height) == (64, 24)
        assert "100%" in str(app.screen.query_one("#image-bar").render())
        await pilot.press("plus", "plus")
        await pilot.pause()
        assert view.widget.size.width == 100 and view.scale == pytest.approx(1.5625)
        await pilot.press("plus", "plus")
        await pilot.pause()
        scroll = view.query_one("#image-view")
        assert view.widget.size.width > scroll.size.width
        before = scroll.scroll_x
        await pilot.press("l")
        await pilot.pause()
        assert scroll.scroll_x == before + imageview.PAN_COLS
        await pilot.press("0")
        await pilot.pause()
        assert view.widget.size.width == 104
        await pilot.press("q")
        await pilot.pause()
        assert not isinstance(app.screen, ImageViewer)

    ui(tmp_path, test)


def test_s_saves_next_to_the_notebook_and_o_opens_the_browser(tmp_path, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(imageview.webbrowser, "open", lambda url: opened.append(url) or True)

    async def test(app, pilot):
        await pilot.press("v")
        await pilot.pause()
        view = viewer(app)
        await pilot.press("s")
        await pilot.pause()
        field = view.query_one("#image-save", Input)
        assert field.display and field.value == str(tmp_path / "nb-c1.png")
        await pilot.press("enter")
        await pilot.pause()
        assert (tmp_path / "nb-c1.png").read_bytes() == PNG
        assert not field.display
        await pilot.press("o")
        await pilot.pause()
        assert len(opened) == 1 and opened[0].startswith("file://")
        saved = Path(opened[0].removeprefix("file://"))
        assert saved.read_bytes() == PNG and saved.name.startswith("nb-c1")
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ImageViewer)

    ui(tmp_path, test)


def test_v_without_an_image_only_notifies(tmp_path):
    async def test(app, pilot):
        await pilot.press("v")
        await pilot.pause()
        assert not isinstance(app.screen, ImageViewer)

    ui(tmp_path, test, with_image=False)


def test_img_command_saves_and_views(tmp_path, monkeypatch):
    async def test(app, pilot):
        app._ex_image("save " + str(tmp_path / "out" / "figure"))
        assert (tmp_path / "out" / "figure.png").read_bytes() == PNG
        app._ex_image("save")
        assert (tmp_path / "nb-c1.png").read_bytes() == PNG
        app._ex_image("")
        await pilot.pause()
        viewer(app)

    ui(tmp_path, test)
