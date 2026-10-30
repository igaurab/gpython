"""File explorer, tabs and splits (vim-style windows)."""

import asyncio

import pytest

from gpython.app import GPython, KeyCapture
from gpython.explorer import Explorer
from gpython.pane import NotebookPane
from gpython.vim import VimTextArea
from gpython.windows import Branch, Leaf, arrange, remove_leaf, split_leaf
from textual.geometry import Region


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    VimTextArea.enabled = True
    yield
    VimTextArea.enabled = True


def project(tmp_path):
    (tmp_path / "a.py").write_text("# %%\na = 1\n")
    (tmp_path / "b.py").write_text("# %%\nb = 2\n\n# %%\nprint(b)\n")
    (tmp_path / "notes.txt").write_text("not a notebook")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.py").write_text("# %%\nc = 3\n")
    (tmp_path / ".hidden.py").write_text("# %%\nh = 1\n")
    return tmp_path


async def loaded(explorer):
    """Wait for the directory loader worker to fill the root."""
    for _ in range(50):
        if explorer.root.children:
            break
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.05)


def ui(test, *args, **kwargs):
    async def main():
        app = GPython(*args, **kwargs)
        async with app.run_test(size=(140, 40)) as pilot:
            await app.loaded.wait()
            await pilot.pause()
            await test(app, pilot)

    asyncio.run(asyncio.wait_for(main(), 120))


def names(app):
    return [p.name for p in app.workspace.panes()]


def visible_names(app):
    return [p.name for p in app.workspace.tab_panes()]


# ── the split tree ───────────────────────────────────────────────────────


class FakePane:
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return self.name


def test_split_tree_arranges_and_removes():
    a, b, c = FakePane("a"), FakePane("b"), FakePane("c")
    root = Leaf(a)
    root = split_leaf(root, a, b, row=True, after=True)  # a | b
    assert isinstance(root, Branch) and root.row and [l.pane for l in root.children] == [a, b]
    root = split_leaf(root, b, c, row=False, after=True)  # a | (b / c)
    rects = {}
    arrange(root, Region(0, 0, 101, 40), rects)
    assert rects[a] == Region(0, 0, 50, 40)
    assert rects[b].x == 51 and rects[b].width == 50 and rects[b].height == 20
    assert rects[c].y == 20 and rects[c].height == 20
    root = remove_leaf(root, b)  # a | c
    assert isinstance(root, Branch) and [l.pane for l in root.children] == [a, c]
    root = remove_leaf(root, a)
    assert isinstance(root, Leaf) and root.pane is c
    assert remove_leaf(root, c) is None


# ── explorer ─────────────────────────────────────────────────────────────


def test_explorer_toggles_and_opens_files(tmp_path, monkeypatch):
    root = project(tmp_path)
    monkeypatch.chdir(root)  # an untitled notebook: the explorer opens at the working directory

    async def test(app, pilot):
        assert app.explorer is None  # mounted on first use
        await pilot.press("ctrl+b")
        await pilot.pause()
        explorer = app.explorer
        assert isinstance(explorer, Explorer) and explorer.display and app.focused is explorer
        assert app.ui_mode == "files"
        await loaded(explorer)
        shown = [n.data.path.name for n in explorer.root.children]
        assert shown == ["sub", "a.py", "b.py"]  # folders first; .txt and dotfiles hidden

        # j/k move, enter on a file opens it: the blank untitled window is reused.
        await pilot.press("j", "j")  # a.py (sub is first)
        assert explorer.cursor_node.data.path.name == "a.py"
        await pilot.press("enter")
        await pilot.pause()
        assert names(app) == ["a.py"] and len(app.workspace.tabs) == 1
        assert app.path == root / "a.py" and app.cells()[0].source == "a = 1"
        assert isinstance(app.focused, VimTextArea)  # focus moved into the notebook
        assert explorer.display  # the explorer stays open
        assert [p.name for p in app.workspace.buffers] == ["a.py"]  # the blank one was dropped

        # ctrl+b from the notebook hides it; ctrl+b again shows and focuses it.
        await pilot.press("ctrl+b")
        assert not explorer.display and isinstance(app.focused, VimTextArea)
        await pilot.press("ctrl+b")
        assert explorer.display and app.focused is explorer

        # v opens in a vertical split, t in a new tab.
        await pilot.press("j")  # b.py
        assert explorer.cursor_node.data.path.name == "b.py"
        await pilot.press("v")
        await pilot.pause()
        assert visible_names(app) == ["a.py", "b.py"] and app.pane.name == "b.py"
        ra, rb = (app.workspace.tab.rects[p] for p in app.workspace.tab_panes())
        assert ra.x < rb.x and ra.y == rb.y  # side by side
        assert isinstance(app.focused, VimTextArea) and app.focused.text == "b = 2"
        # ctrl+w w cycles explorer → windows → explorer; ctrl+w h from the leftmost window too.
        await pilot.press("ctrl+w", "w")
        assert app.focused is explorer
        await pilot.press("ctrl+w", "l")
        assert app.pane.name == "a.py" and isinstance(app.focused, VimTextArea)
        await pilot.press("ctrl+w", "h")
        assert app.focused is explorer
        await pilot.press("g", "g", "l")  # expand sub
        await loaded(explorer)
        await pilot.pause(0.2)
        await pilot.press("j", "enter")  # c.py: enter opens a file in its own tab
        await pilot.pause()
        assert len(app.workspace.tabs) == 2 and visible_names(app) == ["c.py"]
        assert app.has_class("-tabs")

        # Opening a file that is already open just goes there.
        await pilot.press("ctrl+w", "p")
        assert app.focused is explorer
        await pilot.press("g", "g", "j", "j", "enter")  # a.py again (sub is expanded: sub, c.py, a.py)
        await pilot.pause()
        assert len(app.workspace.panes()) == 3 and app.pane.name == "a.py" and app.workspace.index == 0
        assert len(app.workspace.tabs) == 2

        # q hides the explorer.
        await pilot.press("ctrl+w", "p", "q")
        assert not explorer.display and isinstance(app.focused, VimTextArea)

    ui(test, None)


def test_explorer_from_cli_directory_and_hidden_toggle(tmp_path):
    root = project(tmp_path)

    async def test(app, pilot):
        explorer = app.explorer
        assert explorer is not None and explorer.display and app.focused is explorer
        assert str(explorer.path) == str(root)
        await loaded(explorer)
        await pilot.press(".")  # show hidden and non-notebook files
        await pilot.pause(0.3)
        shown = [n.data.path.name for n in explorer.root.children]
        assert ".hidden.py" in shown and "notes.txt" in shown
        # '-' goes up, 'C' makes the folder under the cursor the root.
        await pilot.press("-")
        await pilot.pause(0.3)
        assert str(explorer.path) == str(root.parent)
        await loaded(explorer)
        await pilot.press("/", *root.name, "enter")
        await pilot.pause(0.3)
        assert explorer.cursor_node.data.path.name == root.name
        await pilot.press("C")
        await pilot.pause(0.3)
        assert str(explorer.path) == str(root)

    ui(test, root)


def test_explorer_new_file_and_click(tmp_path):
    root = project(tmp_path)

    async def test(app, pilot):
        await pilot.press("ctrl+b")
        await pilot.pause()
        explorer = app.explorer
        await loaded(explorer)
        await pilot.press("%", *"fresh", "enter")
        await pilot.pause(0.3)
        assert (root / "fresh.py").exists()
        assert app.pane.name == "fresh.py" and app.pane.path == root / "fresh.py"
        # Click a file in the tree (the explorer stayed open).
        await loaded(explorer)
        await pilot.pause(0.2)
        line = next(i for i, l in enumerate(explorer._tree_lines) if l.path[-1].data.path.name == "b.py")
        await pilot.click(explorer, offset=(4, line - explorer.scroll_offset.y))
        await pilot.pause(0.3)
        assert app.pane.name == "b.py"

    ui(test, root / "a.py")


# ── windows and tabs ─────────────────────────────────────────────────────


def test_ctrl_w_splits_moves_and_closes(tmp_path):
    root = project(tmp_path)

    async def test(app, pilot):
        first = app.pane
        await pilot.press("escape")  # normal mode: ctrl+w is the window prefix
        await pilot.press("ctrl+w")
        assert isinstance(app.screen, KeyCapture)
        await pilot.press("v")
        await pilot.pause()
        assert len(visible_names(app)) == 2 and app.pane is not first and app.pane.path is None
        second = app.pane
        await pilot.press("ctrl+w", "s")
        await pilot.pause()
        assert len(visible_names(app)) == 3
        third = app.pane
        rects = app.workspace.tab.rects
        assert rects[second].y < rects[third].y and rects[second].x == rects[third].x  # stacked under second
        assert rects[first].x < rects[second].x

        await pilot.press("ctrl+w", "k")
        assert app.pane is second
        await pilot.press("ctrl+w", "h")
        assert app.pane is first
        await pilot.press("ctrl+w", "l")
        assert app.pane is second
        await pilot.press("ctrl+w", "j")
        assert app.pane is third
        await pilot.press("ctrl+w", "w")
        assert app.pane is first
        await pilot.press("ctrl+w", "p")
        assert app.pane is third

        # The per-window status lines show only with several windows.
        assert all(p.has_class("-multi") for p in app.workspace.tab_panes())
        assert third.has_class("-active") and not first.has_class("-active")

        # Typing in a window marks only that notebook dirty. :q closes the window; the
        # notebook stays as a hidden buffer (vim's 'hidden'), so nothing is lost.
        await pilot.press("i", "x", "escape")
        assert third.dirty and not first.dirty
        await pilot.press(":", *"q", "enter")
        await pilot.pause()
        assert len(visible_names(app)) == 2 and app.pane is second
        assert third.is_attached and not third.display and third in app.workspace.hidden() and third.dirty
        assert any(line.startswith("  3  h+") for line in app.buffer_lines())
        # :bd on the dirty hidden buffer asks; y deletes it for good.
        await pilot.press(":", *"bd 3", "enter")
        await pilot.pause()
        assert third.is_attached  # a prompt is waiting
        await pilot.press("y", "enter")
        await pilot.pause()
        assert not third.is_attached and third not in app.workspace.buffers

        await pilot.press("ctrl+w", "o")
        await pilot.pause()
        assert visible_names(app) == [second.name] and not any(p.has_class("-multi") for p in app.workspace.tab_panes())

        # Insert mode: ctrl+w deletes a word, as in vim.
        await pilot.press("i", *"hello world", "ctrl+w")
        assert app.cells()[0].source == "hello "

    ui(test, root / "a.py")


def test_tabs_from_cli_and_gt(tmp_path):
    root = project(tmp_path)

    async def test(app, pilot):
        assert names(app) == ["a.py", "b.py"] and len(app.workspace.tabs) == 2
        assert visible_names(app) == ["a.py"]  # the first file is the current tab, like vim -p
        assert app.has_class("-tabs")
        bar = str(app.query_one("#tabbar").render())
        assert "1: a.py" in bar and "2: b.py" in bar
        assert isinstance(app.focused, VimTextArea) and app.ui_mode == "normal"
        await pilot.press("g", "t")  # normal mode gt
        await pilot.pause()
        assert app.pane.name == "b.py" and isinstance(app.focused, VimTextArea)
        await pilot.press("g", "T")
        await pilot.pause()
        assert app.pane.name == "a.py"
        await pilot.press("escape", "2", "g", "t")  # cell mode: 2gt
        await pilot.pause()
        assert app.pane.name == "b.py"
        assert isinstance(app.focused, VimTextArea)  # each window remembers what had focus in it
        await pilot.press("escape")
        await pilot.press("g", "g", "g", "t")  # wraps around to tab 1
        await pilot.pause()
        assert app.pane.name == "a.py"
        await pilot.press(":", *"tabnew", "enter")
        await pilot.pause()
        assert len(app.workspace.tabs) == 3 and app.pane.path is None
        await pilot.press(":", *"tabclose", "enter")
        await pilot.pause()
        assert len(app.workspace.tabs) == 2
        await pilot.press(":", *"tabn 2", "enter")
        await pilot.pause()
        assert app.pane.name == "b.py"
        # Each notebook has its own kernel and its own cells.
        assert app.workspace.panes()[0].kernel is not app.workspace.panes()[1].kernel
        assert [c.source for c in app.cells()] == ["b = 2", "print(b)"]

    ui(test, root / "a.py", paths=[root / "b.py"])


def test_edit_split_and_quit_commands(tmp_path):
    root = project(tmp_path)

    async def test(app, pilot):
        await pilot.press("escape", ":", *f"e {root / 'b.py'}", "enter")
        await pilot.pause()
        assert names(app) == ["b.py"]  # the blank untitled window was replaced
        await pilot.press("i", "z", "escape", ":", *f"e {root / 'a.py'}", "enter")
        await pilot.pause()
        assert names(app) == ["b.py"]  # refused: unsaved changes
        await pilot.press(":", *f"e! {root / 'a.py'}", "enter")
        await pilot.pause()
        assert names(app) == ["a.py"]
        assert [p.name for p in app.workspace.buffers] == ["b.py", "a.py"]  # b.py is a hidden buffer now
        assert [p.name for p in app.workspace.hidden()] == ["b.py"] and app.has_class("-tabs")
        await pilot.press(":", *f"sp {root / 'b.py'}", "enter")
        await pilot.pause()
        assert visible_names(app) == ["a.py", "b.py"]
        ra, rb = (app.workspace.tab.rects[p] for p in app.workspace.tab_panes())
        assert ra.y < rb.y and ra.x == rb.x
        await pilot.press(":", *"ls", "enter")
        await pilot.pause()
        await pilot.press(":", *"only", "enter")
        await pilot.pause()
        assert visible_names(app) == ["b.py"] and [p.name for p in app.workspace.hidden()] == ["a.py"]
        await pilot.press(":", *"q", "enter")  # last window: quits (b.py is unsaved: asks)
        await pilot.pause()
        assert app.return_code is None
        await pilot.press("y", "enter")
        await pilot.pause()
        assert app.return_code == 0

    ui(test, None)


def test_buffers(tmp_path):
    root = project(tmp_path)

    async def test(app, pilot):
        ws = app.workspace
        a, b = ws.buffers
        assert names(app) == ["a.py", "b.py"] and ws.number(b) == 2
        # :q hides a window's notebook instead of closing it; it stays a buffer.
        await pilot.press("escape", ":", *"tabn 2", "enter")
        await pilot.pause()
        assert app.pane is b
        await pilot.press(":", *"q", "enter")
        await pilot.pause()
        assert visible_names(app) == ["a.py"] and ws.hidden() == [b] and b.is_attached and not b.display
        assert ws.buffers == [a, b]
        bar = str(app.query_one("#tabbar").render())
        assert "hidden:" in bar and "2 b.py" in bar
        lines = app.buffer_lines()
        assert lines[0].startswith("  1 %a") and lines[1].startswith("  2  h") and "hidden" in lines[1]
        # :b 2 shows it in the current window; a.py becomes hidden.
        await pilot.press(":", *"b 2", "enter")
        await pilot.pause()
        assert app.pane is b and visible_names(app) == ["b.py"] and ws.hidden() == [a]
        assert [c.source for c in app.cells()] == ["b = 2", "print(b)"]
        assert isinstance(app.focused, VimTextArea)
        # :bn / :bp cycle, :b name matches by name.
        await pilot.press(":", *"bn", "enter")
        await pilot.pause()
        assert app.pane is a
        await pilot.press(":", *"bp", "enter")
        await pilot.pause()
        assert app.pane is b
        await pilot.press(":", *"b a.py", "enter")
        await pilot.pause()
        assert app.pane is a
        # Opening a hidden buffer's file again brings it back (in a new tab), no second kernel.
        await pilot.press("ctrl+b")
        await loaded(app.explorer)
        await pilot.press("/", *"b.py", "enter")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.pane is b and len(ws.tabs) == 2 and ws.buffers == [a, b] and ws.hidden() == []
        # :bd deletes a buffer (its window closes); the last buffer deleted leaves an untitled one.
        kernel_b = b.kernel
        await pilot.press(":", *"bd", "enter")
        await pilot.pause()
        assert not b.is_attached and ws.buffers == [a] and len(ws.tabs) == 1 and app.pane is a
        await pilot.pause(0.5)
        assert app.workers  # the shutdown runs in the background
        await pilot.press(":", *"bd 1", "enter")
        await pilot.pause()
        assert not a.is_attached and len(ws.buffers) == 1 and ws.buffers[0].path is None
        assert app.return_code is None  # still running, with a fresh untitled notebook
        # A dirty buffer asks before :bd; :bd! does not.
        await pilot.press("i", "x", "escape", ":", *"bd", "enter")
        await pilot.pause()
        assert ws.buffers and ws.buffers[0].dirty  # the prompt is up, nothing deleted
        await pilot.press("n", "enter")
        await pilot.pause()
        untitled = ws.buffers[0]
        await pilot.press(":", *"bd!", "enter")
        await pilot.pause()
        assert not untitled.is_attached and ws.buffers[0] is not untitled

    ui(test, root / "a.py", paths=[root / "b.py"])
