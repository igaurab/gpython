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


def ui(tmp_path, test, with_image=True, pngs=(PNG,)):
    path = tmp_path / "nb.py"
    path.write_text("# %%\nx = 1\n\n# %%\ny = 2\n")

    async def main():
        app = GPython(path)
        async with app.run_test(size=(120, 40)) as pilot:
            await app.loaded.wait()
            await pilot.pause()
            for data in pngs if with_image else ():
                mime = {"image/png": base64.b64encode(data).decode(), "text/plain": "<Figure>"}
                app.cells()[0].add_output("display_data", {"data": mime, "metadata": {}})
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


SECOND = png(300, 200)


def test_img_with_several_images_numbers_them(tmp_path, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(imageview.webbrowser, "open", lambda url: opened.append(url) or True)

    async def test(app, pilot):
        app._ex_image("2")
        await pilot.pause()
        view = viewer(app)
        assert view.index == 1 and view.png == SECOND and view.current_stem == "nb-c1-2"
        await pilot.press("p")
        await pilot.pause()
        assert view.index == 0 and view.png == PNG
        await pilot.press("q")
        await pilot.pause()
        app._ex_image("save")
        assert (tmp_path / "nb-c1-1.png").read_bytes() == PNG and (tmp_path / "nb-c1-2.png").read_bytes() == SECOND
        app._ex_image("save 2 " + str(tmp_path / "second.png"))
        assert (tmp_path / "second.png").read_bytes() == SECOND
        app._ex_image("save 3")
        assert not (tmp_path / "nb-c1-3.png").exists()
        app._ex_image("open")
        assert len(opened) == 2
        app._ex_image("open 1")
        assert len(opened) == 3 and "nb-c1-1" in opened[-1]

    ui(tmp_path, test, pngs=(PNG, SECOND))


def test_card_buttons_view_and_download(tmp_path, monkeypatch):
    from textual.widgets import Button

    from gpython.app import Prompt
    from gpython.imageview import ImageCard

    monkeypatch.setattr(images, "enabled_override", False)
    opened: list[str] = []
    monkeypatch.setattr(imageview.webbrowser, "open", lambda url: opened.append(url) or True)

    async def test(app, pilot):
        card = app.cells()[0].query_one(ImageCard)
        card.query_one(".view", Button).press()
        await pilot.pause()
        await pilot.pause()
        assert len(opened) == 1 and "nb-c1" in opened[0]
        card.query_one(".download", Button).press()
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, Prompt)
        assert app.screen.query_one(Input).value == str(tmp_path / "nb-c1.png")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert (tmp_path / "nb-c1.png").read_bytes() == PNG
        assert not isinstance(app.screen, Prompt)

    ui(tmp_path, test)


def test_img_all_covers_every_cell(tmp_path, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(imageview.webbrowser, "open", lambda url: opened.append(url) or True)

    async def test(app, pilot):
        mime = {"image/png": base64.b64encode(SECOND).decode()}
        app.cells()[1].add_output("display_data", {"data": mime, "metadata": {}})
        await pilot.pause()
        app._ex_image("save all")
        assert (tmp_path / "nb-c1.png").read_bytes() == PNG and (tmp_path / "nb-c2.png").read_bytes() == SECOND
        app._ex_image("open all")
        assert len(opened) == 2 and "nb-c1" in opened[0] and "nb-c2" in opened[1]
        app.cells()[1].focus()
        await pilot.pause()
        app._ex_image("save " + str(tmp_path / "only-c2.png"))
        assert (tmp_path / "only-c2.png").read_bytes() == SECOND

    ui(tmp_path, test)
