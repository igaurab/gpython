"""Big headings: h1–h3 as kitty-graphics images where supported, text everywhere else."""

import asyncio
import base64
import io
import time

import pytest
from PIL import Image
from textual.geometry import Region

from gpython import bigtext, notebook
from gpython.app import GPython
from gpython.markdown import MarkdownView
from gpython.notebook import CellSpec


@pytest.fixture
def big(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(bigtext, "enabled_override", True)
    monkeypatch.setattr(bigtext, "cell_pixels", lambda: (10, 20))
    bigtext._blocks.clear()
    yield
    bigtext._blocks.clear()


def screen_lines(view: MarkdownView) -> list[str]:
    return [strip.text for strip in view.render_lines(Region(0, 0, view.size.width, view.size.height))]


def run(tmp_path, cells, test):
    path = tmp_path / "nb.py"
    notebook.save(path, cells)

    async def main():
        app = GPython(path)
        async with app.run_test(size=(120, 40)) as pilot:
            await app.loaded.wait()
            await pilot.pause()
            await test(app, pilot)

    asyncio.run(main())


def test_off_by_default_in_tests(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert not bigtext.supported()

    async def test(app, pilot):
        lines = screen_lines(app.cells()[0].query_one(MarkdownView))
        assert any("Title" in line for line in lines)
        assert not any(bigtext.PLACEHOLDER in line for line in lines)

    run(tmp_path, [CellSpec("# Title\n\nbody", "markdown")], test)


def test_headings_become_placeholder_blocks_and_images_are_sent(tmp_path, big):
    sent: list[str] = []

    async def test(app, pilot):
        app._driver.write = sent.append
        view = app.cells()[0].query_one(MarkdownView)
        await view.update("# Title\n\n## Sub\n\n### Small\n\n#### four\n\nbody")
        await pilot.pause()
        lines = screen_lines(view)
        rows = [line for line in lines if bigtext.PLACEHOLDER in line]
        assert len(rows) == 3 + 2 + 2
        assert any("four" in line for line in lines) and any("body" in line for line in lines)
        for _ in range(100):
            if len(sent) >= 3:
                break
            await asyncio.sleep(0.02)
        assert len(sent) == 3 and all(s.startswith("\x1b_Ga=T,U=1,f=100,i=") for s in sent)

    run(tmp_path, [CellSpec("x", "markdown")], test)


def test_block_is_cached_and_too_wide_falls_back(tmp_path, big):
    async def test(app, pilot):
        first = bigtext.heading("Hello", 1, 100)
        assert first is bigtext.heading("Hello", 1, 100) and first.rows == 3
        assert bigtext.heading("Hello", 1, first.cols - 1) is None
        assert bigtext.heading("Hello", 4, 100) is None and bigtext.heading("  ", 1, 100) is None

    run(tmp_path, [CellSpec("x")], test)


def test_png_and_sequence(big):
    png = bigtext._png("Hello", (200, 200, 200), 40, 12, 3)
    image = Image.open(io.BytesIO(png))
    assert image.size == (120, 60) and image.mode == "RGBA"
    assert image.getbbox() is not None
    sequence = bigtext._sequence(7, b"x" * 5000, 12, 3)
    chunks = sequence.split("\x1b\\")[:-1]
    assert chunks[0].startswith("\x1b_Ga=T,U=1,f=100,i=7,c=12,r=3,q=2,m=1;")
    assert chunks[-1].startswith("\x1b_Gm=0;")
    payload = "".join(c.split(";", 1)[1] for c in chunks)
    assert base64.standard_b64decode(payload) == b"x" * 5000


def test_block_rows_carry_row_and_column_marks(big):
    from rich.console import Console
    from textual_image.renderable.tgp import _NUMBER_TO_DIACRITIC as marks

    lines = Console(width=80).render_lines(bigtext.Block(0x010203, 3, 2), pad=False)
    assert len(lines) == 2
    text = "".join(seg.text for seg in lines[1])
    assert text == "".join(f"{bigtext.PLACEHOLDER}{chr(marks[1])}{chr(marks[c])}" for c in range(3))
    assert lines[1][0].style.color.triplet == (1, 2, 3)


def test_setting_turns_it_on(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(bigtext, "enabled", False)
    monkeypatch.setattr(bigtext, "_terminal_has_tgp", lambda: True)
    config_file = tmp_path / "xdg" / "gpython" / "config.toml"
    config_file.parent.mkdir(parents=True)
    path = tmp_path / "nb.py"
    notebook.save(path, [CellSpec("x")])
    for text, on in (("", False), ("[experimental]\ntrue_markdown = true\n", True), ('"experimental.true_markdown" = true\n', True)):
        config_file.write_text(text)
        GPython(path)
        assert bigtext.supported() is on
