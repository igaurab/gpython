"""Window management: zoom, swap, rotate, move to an edge, tab out and back, :tabmove, and
the floating window manager (ctrl+w space)."""

import asyncio

import pytest

from gpython.app import GPython
from gpython.manager import WindowManager, build_rows, draw_map
from gpython.vim import VimTextArea
from gpython.windows import Branch, Leaf, Tab, panes_of


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    VimTextArea.enabled = True
    yield
    VimTextArea.enabled = True


def project(tmp_path):
    for name in "abcd":
        (tmp_path / f"{name}.py").write_text(f"# %%\n{name} = 1\n")
    return tmp_path


def ui(test, *args, **kwargs):
    async def main():
        app = GPython(*args, **kwargs)
        async with app.run_test(size=(140, 40)) as pilot:
            await app.loaded.wait()
            await pilot.pause()
            await test(app, pilot)

    asyncio.run(asyncio.wait_for(main(), 120))


async def ex(pilot, command):
    await pilot.press(":", *command, "enter")
    await pilot.pause()


async def three_windows(app, pilot):
    """a | b over c: a on the left, b above c on the right."""
    await pilot.press("escape")
    await ex(pilot, "vs b.py")
    await ex(pilot, "sp c.py")
    names = {p.name: p for p in app.workspace.tab_panes()}
    return names["a.py"], names["b.py"], names["c.py"]


def shown(app):
    return [p.name for p in app.workspace.buffers if p.display]


def test_zoom_toggles_and_navigation_restores(tmp_path):
    root = project(tmp_path)

    async def test(app, pilot):
        a, b, c = await three_windows(app, pilot)
        assert app.pane is c
        await pilot.press("ctrl+w", "z")
        await pilot.pause()
        assert app.workspace.tab.zoom is c and shown(app) == ["c.py"]
        assert app.workspace.tab.rects[c].width == app.workspace.size.width
        assert c.zoomed and "zoomed" in str(c._status.render())
        assert "⤢" in app._tabbar.render().plain or len(app.workspace.tabs) == 1
        await pilot.press("ctrl+w", "z")
        await pilot.pause()
        assert app.workspace.tab.zoom is None and sorted(shown(app)) == ["a.py", "b.py", "c.py"]
        assert not c.zoomed

        await ex(pilot, "zoom")
        assert shown(app) == ["c.py"]
        await pilot.press("ctrl+w", "k")
        await pilot.pause()
        assert app.pane is b and app.workspace.tab.zoom is None and len(shown(app)) == 3

        await pilot.press("ctrl+w", "z")
        await pilot.press("ctrl+w", "s")
        await pilot.pause()
        assert app.workspace.tab.zoom is None and len(shown(app)) == 4

    ui(test, root / "a.py")


def test_exchange_rotate_and_edges(tmp_path):
    root = project(tmp_path)

    async def test(app, pilot):
        a, b, c = await three_windows(app, pilot)
        tab = app.workspace.tab
        await pilot.press("ctrl+w", "x")
        await pilot.pause()
        assert panes_of(tab.root) == [a, c, b] and app.pane is c
        await pilot.press("ctrl+w", "r")
        await pilot.pause()
        assert panes_of(tab.root) == [a, b, c]

        await pilot.press("ctrl+w", "K")
        await pilot.pause()
        assert isinstance(tab.root, Branch) and not tab.root.row
        assert tab.root.children[0] == Leaf(c, tab.root.children[0].weight)
        rects = tab.rects
        assert rects[c].width == app.workspace.size.width and rects[c].y == 0

        await pilot.press("ctrl+w", "L")
        await pilot.pause()
        assert tab.root.row and panes_of(tab.root)[-1] is c
        assert tab.rects[c].height == app.workspace.size.height
        assert app.pane is c

    ui(test, root / "a.py")


def test_to_tab_and_back(tmp_path):
    root = project(tmp_path)

    async def test(app, pilot):
        a, b, c = await three_windows(app, pilot)
        before = panes_of(app.workspace.tab.root)
        await pilot.press("ctrl+w", "k")
        assert app.pane is b
        await pilot.press("ctrl+w", "T")
        await pilot.pause()
        assert len(app.workspace.tabs) == 2 and app.workspace.tab_panes() == [b]
        await pilot.press("ctrl+w", "M")
        await pilot.pause()
        assert len(app.workspace.tabs) == 1 and panes_of(app.workspace.tab.root) == before
        rects = app.workspace.tab.rects
        assert rects[b].y < rects[c].y and rects[b].x == rects[c].x and app.pane is b

        await ex(pilot, "tabnew d.py")
        d = app.pane
        await ex(pilot, "tabmerge")
        assert len(app.workspace.tabs) == 1 and d in app.workspace.tab_panes() and app.pane is d

    ui(test, root / "a.py")


def test_tabmove(tmp_path):
    root = project(tmp_path)

    async def test(app, pilot):
        await pilot.press("escape")
        for name in "bcd":
            await ex(pilot, f"tabnew {name}.py")
        order = lambda: [panes_of(t.root)[0].name for t in app.workspace.tabs]
        assert order() == ["a.py", "b.py", "c.py", "d.py"] and app.pane.name == "d.py"
        await ex(pilot, "tabmove 0")
        assert order() == ["d.py", "a.py", "b.py", "c.py"] and app.workspace.index == 0
        await ex(pilot, "tabmove +2")
        assert order() == ["a.py", "b.py", "d.py", "c.py"]
        await ex(pilot, "tabmove")
        assert order() == ["a.py", "b.py", "c.py", "d.py"] and app.pane.name == "d.py"
        await ex(pilot, "tabmove 1")
        assert order() == ["a.py", "d.py", "b.py", "c.py"]

    ui(test, root / "a.py")


def test_manager_lists_moves_and_closes(tmp_path):
    root = project(tmp_path)

    async def test(app, pilot):
        a, b, c = await three_windows(app, pilot)
        await ex(pilot, "tabnew d.py")
        d = app.pane
        await pilot.press("ctrl+w", "space")
        await pilot.pause()
        manager = app.screen
        assert isinstance(manager, WindowManager)
        kinds = [(r.kind, r.pane.name if r.pane else r.tab) for r in manager.rows]
        assert kinds == [("tab", 0), ("window", "a.py"), ("window", "b.py"), ("window", "c.py"), ("tab", 1), ("window", "d.py")]
        assert manager.selected.pane is d
        text = manager.query_one("#wm-list").render().plain
        assert "Tab 1" in text and "you were here" in text

        await pilot.press("k", "k")
        assert manager.selected.pane is c
        await pilot.press("v")
        await pilot.pause()
        assert not isinstance(app.screen, WindowManager)
        assert app.workspace.tab_panes() == [d, c] and app.pane is c

        await pilot.press("ctrl+w", "space")
        await pilot.pause()
        manager = app.screen
        await pilot.press("g")
        assert manager.selected.kind == "tab"
        await pilot.press("j", "x")
        await pilot.pause()
        assert a not in app.workspace.panes() and a in app.workspace.hidden()
        assert [r.kind for r in manager.rows][-2:] == ["hidden", "buffer"]

        await pilot.press("G")
        assert manager.selected.pane is a
        await pilot.press("enter")
        await pilot.pause()
        assert app.pane is a and a in app.workspace.panes()

        await pilot.press("ctrl+w", "space")
        await pilot.pause()
        manager = app.screen
        await pilot.press("slash", "b", "full_stop", "p")
        await pilot.pause()
        assert [r.pane.name for r in manager.rows if r.pane] == ["b.py"] and manager.selected.pane is b
        await pilot.press("enter", "d")
        await pilot.pause()
        assert b not in app.workspace.buffers
        await pilot.press("escape")
        assert isinstance(app.screen, WindowManager) and manager.query_text == ""
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, WindowManager)

    ui(test, root / "a.py")


def test_manager_zoom_tab_keys_and_unsaved_delete(tmp_path):
    root = project(tmp_path)

    async def test(app, pilot):
        a, b, c = await three_windows(app, pilot)
        await ex(pilot, "tabnew d.py")
        await ex(pilot, "windows")
        manager = app.screen
        assert isinstance(manager, WindowManager)
        await pilot.press("K")
        await pilot.pause()
        assert [panes_of(t.root)[0].name for t in app.workspace.tabs] == ["d.py", "a.py"]
        await pilot.press("2")
        await pilot.pause()
        assert app.workspace.index == 1 and not isinstance(app.screen, WindowManager)

        b.mark_dirty()
        await pilot.press("ctrl+w", "space")
        await pilot.pause()
        manager = app.screen
        steps = next(i for i, r in enumerate(manager.rows) if r.pane is b) - manager.cursor
        await pilot.press(*(["j"] * steps if steps > 0 else ["k"] * -steps))
        assert manager.selected.pane is b
        await pilot.press("d")
        await pilot.pause()
        assert b in app.workspace.buffers
        await pilot.press("z")
        await pilot.pause()
        assert app.workspace.tab.zoom is b and app.pane is b and shown(app) == ["b.py"]

    ui(test, root / "a.py")


class FakePane:
    def __init__(self, name, dirty=False):
        self.name = name
        self.dirty = dirty


def test_draw_map_boxes():
    a, b, c = FakePane("a.py"), FakePane("b.py", dirty=True), FakePane("c.py")
    tab = Tab(Branch(True, [Leaf(a), Branch(False, [Leaf(b), Leaf(c)])]), a)
    text = draw_map(tab, b, a, "red", width=30, height=10).plain.split("\n")
    assert len(text) == 10 and all(len(line) == 30 for line in text)
    assert text[0].startswith("╭") and "┏" in text[0]
    assert any("◆ a.py" in line for line in text)
    assert any("b.py" in line for line in text) and any("unsaved" in line for line in text)
    assert any("c.py" in line for line in text)


def test_resize_applies_at_once(tmp_path):
    root = project(tmp_path)

    async def test(app, pilot):
        await pilot.press("escape", "ctrl+w", "v")
        await pilot.pause()
        left, right = app.workspace.tab_panes()
        before = app.workspace.tab.rects[right].width
        await pilot.press("ctrl+w", ">")
        await pilot.pause()
        assert app.workspace.tab.rects[right].width > before

    ui(test, root / "a.py")
