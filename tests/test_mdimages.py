"""Pictures in markdown cells: `![alt](url-or-path)` fetched in the background and drawn in place."""

from __future__ import annotations

import asyncio
import base64
import io
import re
import threading

import pytest
from PIL import Image as PILImage
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.geometry import Region

from gpython import bigtext, images, mdimages
from gpython.markdown import MarkdownView


def encoded(fmt: str, size=(200, 100), color="blue") -> bytes:
    out = io.BytesIO()
    PILImage.new("RGB", size, color).save(out, fmt)
    return out.getvalue()


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(mdimages, "_pictures", {})
    monkeypatch.setattr(mdimages, "_waiting", {})
    monkeypatch.setattr(bigtext, "cell_pixels", lambda: (10, 20))


@pytest.fixture
def kitty(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(images, "enabled_override", True)
    monkeypatch.setattr(images, "_write", lambda app, data: sent.append(data))
    yield sent


@pytest.fixture
def plain(monkeypatch):
    monkeypatch.setattr(images, "enabled_override", False)


@pytest.fixture
def web(monkeypatch):
    """A fake network: url -> body, and the urls asked for."""
    class Bodies(dict):
        asked: list[str] = []
        gate: threading.Event | None = None

    bodies = Bodies()
    asked = bodies.asked

    def download(url: str) -> bytes:
        asked.append(url)
        if bodies.gate is not None:
            bodies.gate.wait()
        if url not in bodies:
            import urllib.error

            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return bodies[url]

    monkeypatch.setattr(mdimages, "download", download)
    return bodies


def commands(sent: list[str]) -> list[str]:
    return re.findall(r"\x1b_G([^;\x1b]*)(?:;[^\x1b]*)?\x1b\\", "".join(sent))


class MdApp(App):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text

    def post_display_hook(self) -> None:
        images.sync(self)

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield MarkdownView(self.text)


def shown(view: MarkdownView) -> str:
    return "\n".join(strip.text.rstrip() for strip in view.render_lines(Region(0, 0, view.size.width, view.size.height)))


async def settled(pilot, view: MarkdownView, src: str, tries: int = 40) -> None:
    """Wait for `src` to load (or fail) and the view to redraw."""
    folder = mdimages.folder_of(view.app)
    for _ in range(tries):
        if mdimages.key_for(src, folder) in mdimages._pictures:
            break
        await asyncio.sleep(0.02)
    await pilot.pause()
    await pilot.pause()


def run(text: str, test, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    async def main():
        app = MdApp(text)
        async with app.run_test(size=(60, 40)) as pilot:
            await pilot.pause()
            await test(app, pilot, app.query_one(MarkdownView))

    asyncio.run(main())


def animated_gif(colors=("red", "green", "blue"), duration=100, size=(30, 20)) -> bytes:
    frames = [PILImage.new("RGB", size, c) for c in colors]
    out = io.BytesIO()
    frames[0].save(out, "GIF", save_all=True, append_images=frames[1:], duration=duration)
    return out.getvalue()


def test_to_png_keeps_png_and_decodes_every_gif_frame():
    png = encoded("PNG")
    assert mdimages.to_png(png) == mdimages.Picture(png, (200, 100))
    assert mdimages.to_png(encoded("GIF")).frames == ()
    picture = mdimages.to_png(animated_gif(duration=[100, 10, 250]))
    assert picture.pixels == (30, 20) and picture.png == picture.frames[0][0]
    assert [seconds for _, seconds in picture.frames] == [0.1, 0.02, 0.25]
    colours = []
    for png, _ in picture.frames:
        with PILImage.open(io.BytesIO(png)) as frame:
            colours.append(frame.convert("RGB").getpixel((0, 0)))
    assert colours == [(255, 0, 0), (0, 128, 0), (0, 0, 255)]


def test_to_png_caps_the_frames_of_long_animations(monkeypatch):
    monkeypatch.setattr(mdimages, "MAX_FRAMES", 2)
    assert len(mdimages.to_png(animated_gif()).frames) == 2


def test_links_to_pictures_become_images_but_other_links_and_bare_urls_stay():
    from markdown_it import MarkdownIt

    tokens = MarkdownIt().parse("[cat](a/cat.GIF?x=1) and [site](https://e.com/) and https://e.com/b.png")
    mdimages.promote_links(tokens)
    kinds = [(c.type, c.content) for c in tokens[1].children]
    assert kinds[0] == ("image", "cat") and tokens[1].children[0].attrs["src"] == "a/cat.GIF?x=1"
    assert [k for k, _ in kinds[1:]] == ["text", "link_open", "text", "link_close", "text"]
    assert kinds[-1][1] == " and https://e.com/b.png"


def test_to_png_shrinks_huge_pictures(monkeypatch):
    monkeypatch.setattr(mdimages, "MAX_SIDE", 100)
    assert mdimages.to_png(encoded("JPEG", (400, 200))).pixels == (100, 50)


def test_explain_is_short():
    import urllib.error

    from PIL import UnidentifiedImageError

    assert mdimages.explain(urllib.error.HTTPError("u", 404, "x", {}, None)) == "HTTP 404"
    assert mdimages.explain(FileNotFoundError()) == "file not found"
    assert mdimages.explain(UnidentifiedImageError()) == mdimages.NOT_A_PICTURE
    assert mdimages.explain(ValueError("x" * 100)).endswith("...")


def test_frame_timing():
    placement = images.Placement(b"", (1, 1), ((b"a", 0.1), (b"b", 0.2), (b"c", 0.3)))
    assert [placement.frame_at(t) for t in (0.0, 0.05, 0.15, 0.4, 0.65, 0.8)] == [0, 0, 1, 2, 0, 1]
    images._animated.discard(placement)


def test_url_picture_is_fetched_once_cached_on_disk_and_placed(kitty, web, tmp_path, monkeypatch):
    web["https://example.com/pic.png"] = encoded("PNG", (400, 300), "red")
    web.gate = threading.Event()

    async def test(app, pilot, view):
        assert "⌛ a red one" in shown(view) and view.size.height == 3
        web.gate.set()
        await settled(pilot, view, "https://example.com/pic.png")
        assert view.size.height == 15 + 2
        assert [(p.line, p.x, p.cols, p.rows) for p in view._placements] == [(2, 0, 40, 15)]
        sent = commands(kitty)
        image_id = view._placements[0].image_id
        assert sent[0].startswith(f"a=t,f=100,i={image_id}")
        assert sent[-1] == f"a=p,i={image_id},p=1,c=40,r=15,C=1,q=2"
        region, visible = view._placements[0]._placed
        assert (region.x, region.y) == (1, 2) and region == visible
        assert view.pictures == [web["https://example.com/pic.png"]]
        assert web.asked == ["https://example.com/pic.png"]
        assert len(list(mdimages.cache_dir().glob("*.raw"))) == 1

    run("text\n\n![a red one](https://example.com/pic.png)", test, tmp_path, monkeypatch)
    mdimages._pictures.clear()
    web.asked.clear()
    web.gate = None

    async def again(app, pilot, view):
        await settled(pilot, view, "https://example.com/pic.png")
        assert web.asked == [] and view.pictures == [web["https://example.com/pic.png"]]

    run("![a red one](https://example.com/pic.png)", again, tmp_path, monkeypatch)


def test_failed_fetch_shows_the_reason(kitty, web, tmp_path, monkeypatch):
    async def test(app, pilot, view):
        await settled(pilot, view, "https://example.com/gone.gif")
        assert "🌆 gone (HTTP 404)" in shown(view)
        assert view._placements == [] and view.pictures == []

    run("![gone](https://example.com/gone.gif)", test, tmp_path, monkeypatch)


def test_local_gif_relative_to_the_folder_and_missing_file(kitty, tmp_path, monkeypatch):
    (tmp_path / "anim.gif").write_bytes(encoded("GIF", (200, 100)))

    async def test(app, pilot, view):
        await settled(pilot, view, "anim.gif")
        await settled(pilot, view, "nope.png")
        assert [(p.line, p.cols, p.rows) for p in view._placements] == [(0, 20, 5)]
        assert "🌆 nope (file not found)" in shown(view)
        assert "after  end" in shown(view)

    run("![anim](anim.gif)\n\nafter ![nope](nope.png) end", test, tmp_path, monkeypatch)


def test_data_url(kitty, tmp_path, monkeypatch):
    src = "data:image/png;base64," + base64.b64encode(encoded("PNG", (100, 100))).decode()

    async def test(app, pilot, view):
        await settled(pilot, view, src)
        assert [(p.cols, p.rows) for p in view._placements] == [(10, 5)]

    run(f"![dot]({src})", test, tmp_path, monkeypatch)


def test_placements_are_freed_when_the_view_goes_and_moved_when_text_changes(kitty, web, tmp_path, monkeypatch):
    web["https://example.com/pic.png"] = encoded("PNG", (400, 300))

    async def test(app, pilot, view):
        await settled(pilot, view, "https://example.com/pic.png")
        placement = view._placements[0]
        assert placement.line == 0
        await view.update("one\n\ntwo\n\n![p](https://example.com/pic.png)")
        await pilot.pause()
        await pilot.pause()
        assert view._placements == [placement] and placement.line == 4
        assert commands(kitty)[-1].startswith(f"a=p,i={placement.image_id}")
        await view.remove()
        await pilot.pause()
        assert commands(kitty)[-1] == f"a=d,d=I,i={placement.image_id},q=2"
        assert placement not in images._live

    run("![p](https://example.com/pic.png)", test, tmp_path, monkeypatch)


def test_without_kitty_the_picture_is_drawn_in_half_cells(plain, web, tmp_path, monkeypatch):
    web["https://example.com/pic.png"] = encoded("PNG", (200, 100), "red")

    async def test(app, pilot, view):
        await settled(pilot, view, "https://example.com/pic.png")
        text = shown(view)
        assert text.count("▀" * 20) == 5 and view._placements == []
        assert view.pictures == [web["https://example.com/pic.png"]]

    run("![p](https://example.com/pic.png)", test, tmp_path, monkeypatch)


def test_cell_images_include_markdown_pictures(kitty, web, tmp_path, monkeypatch):
    from gpython.cell import Cell

    web["https://example.com/pic.png"] = encoded("PNG", (200, 100))

    class CellApp(App):
        def post_display_hook(self) -> None:
            images.sync(self)

        def compose(self) -> ComposeResult:
            with VerticalScroll():
                yield Cell("![p](https://example.com/pic.png)", "markdown")

    async def main():
        app = CellApp()
        async with app.run_test(size=(60, 40)) as pilot:
            await pilot.pause()
            cell = app.query_one(Cell)
            await cell.show_rendered()
            await settled(pilot, cell._view, "https://example.com/pic.png")
            assert cell.images == [web["https://example.com/pic.png"]]

    monkeypatch.chdir(tmp_path)
    asyncio.run(main())


def test_animated_gif_cycles_its_frames_and_frees_them(kitty, tmp_path, monkeypatch):
    (tmp_path / "anim.gif").write_bytes(animated_gif(duration=100))
    clock = [0.0]
    monkeypatch.setattr(images, "clock", lambda: clock[0])

    async def test(app, pilot, view):
        await settled(pilot, view, "anim.gif")
        placement = view._placements[0]
        assert len(placement.frames) == 3 and placement.frame == 0 and images._ticker is not None
        first = placement.frame_ids[0]
        assert commands(kitty)[-1] == f"a=p,i={first},p=1,c=3,r=1,C=1,q=2"
        clock[0] = 0.15
        images.tick(app)
        second = placement.frame_ids[1]
        assert placement.frame == 1 and placement.image_id == second
        assert commands(kitty)[-3:] == [f"a=t,f=100,i={second},q=2,m=0", f"a=p,i={second},p=1,c=3,r=1,C=1,q=2", f"a=d,d=i,i={first},q=2"]
        before = len(commands(kitty))
        images.tick(app)
        assert len(commands(kitty)) == before
        clock[0] = 0.25
        images.tick(app)
        third = placement.frame_ids[2]
        assert placement.frame == 2 and commands(kitty)[-3:] == [f"a=t,f=100,i={third},q=2,m=0", f"a=p,i={third},p=1,c=3,r=1,C=1,q=2", f"a=d,d=i,i={second},q=2"]
        clock[0] = 0.75
        images.tick(app)
        assert placement.frame == 1 and commands(kitty)[-2:] == [f"a=p,i={second},p=1,c=3,r=1,C=1,q=2", f"a=d,d=i,i={third},q=2"]
        await view.remove()
        await pilot.pause()
        assert commands(kitty)[-3:] == [f"a=d,d=I,i={i},q=2" for i in placement.frame_ids]
        assert placement not in images._animated
        images.tick(app)
        assert images._ticker is None

    run("[loop](anim.gif)", test, tmp_path, monkeypatch)


def test_a_link_to_something_that_is_not_a_picture_stays_a_link(kitty, web, tmp_path, monkeypatch):
    web["https://example.com/page.png"] = b"<html>not a picture</html>"

    async def test(app, pilot, view):
        await settled(pilot, view, "https://example.com/page.png")
        assert "read me (https://example.com/page.png)" in shown(view) and view._placements == []

    run("[read me](https://example.com/page.png)", test, tmp_path, monkeypatch)

