"""A second window on the same notebook (vim: two windows, one buffer) and mouse-resized splits."""

import asyncio

import pytest

from gpython.app import GPython
from gpython.vim import VimTextArea


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    VimTextArea.enabled = True
    yield
    VimTextArea.enabled = True


def notebook(tmp_path, cells=3):
    path = tmp_path / "sc.py"
    path.write_text("".join(f"# %%\nv{n} = {n}\n\n" for n in range(1, cells + 1)))
    return path


def ui(test, *args, size=(140, 40), **kwargs):
    async def main():
        app = GPython(*args, **kwargs)
        async with app.run_test(size=size) as pilot:
            await app.loaded.wait()
            await pilot.pause()
            await test(app, pilot)

    asyncio.run(asyncio.wait_for(main(), 120))


async def until(pilot, check, tries=60):
    for _ in range(tries):
        if check():
            return
        await pilot.pause(0.05)
    assert check()


async def ex(app, pilot, command):
    await app._run_ex(command)
    await pilot.pause()
    await pilot.pause()


async def two_views(app, pilot, path):
    """The user's steps: a vertical split, then :e of the file the first window shows."""
    first = app.pane
    await ex(app, pilot, "vsplit")
    assert app.pane is not first and app.pane.is_blank
    await ex(app, pilot, f"e {path}")
    second = app.pane
    await until(pilot, second.loaded.is_set)
    await pilot.pause()
    return first, second


def test_edit_in_a_split_opens_the_same_notebook_there(tmp_path):
    path = notebook(tmp_path)

    async def test(app, pilot):
        first, second = await two_views(app, pilot, path)
        assert second is not first
        assert second.buffer is first.buffer and second.kernel is first.kernel
        assert app.workspace.tab_panes() == [first, second]
        assert [p.name for p in app.workspace.buffers] == ["sc.py", "sc.py"]
        assert len(second.cells()) == len(first.cells()) == 3
        assert [c.source for c in second.cells()] == [c.source for c in first.cells()]
        assert all(len(state.widgets) == 2 for state in first.states)

    ui(test, path)


def test_text_typed_in_one_window_shows_in_the_other_and_undo_is_shared(tmp_path):
    path = notebook(tmp_path)

    async def test(app, pilot):
        first, second = await two_views(app, pilot, path)
        mine, theirs = second.cells()[0], first.cells()[0]
        second.select(mine, "insert", row=0, column=0)
        await pilot.pause()
        await pilot.press("x", "y", "space")
        await pilot.pause()
        assert mine.editor.text == theirs.editor.text == "xy v1 = 1"
        assert first.dirty and second.dirty
        await pilot.press("escape", "u")
        await pilot.pause()
        assert mine.editor.text == theirs.editor.text == "v1 = 1"
        app.workspace.set_active(first)
        first.select(theirs, "normal")
        await pilot.pause()
        await pilot.press("ctrl+r")
        await pilot.pause()
        assert mine.editor.text == theirs.editor.text == "xy v1 = 1"

    ui(test, path)


def test_cells_added_moved_and_deleted_follow_in_the_other_window(tmp_path):
    path = notebook(tmp_path)

    async def test(app, pilot):
        first, second = await two_views(app, pilot, path)
        second.select(second.cells()[0], "cell")
        await pilot.pause()
        await pilot.press("b")
        await pilot.pause()
        await pilot.press("n", "e", "w", "escape", "escape")
        await until(pilot, lambda: len(first.cells()) == 4)
        assert [c.source for c in first.cells()] == ["v1 = 1", "new", "v2 = 2", "v3 = 3"]
        assert [c.label for c in first.cells()] == ["c1", "c2", "c3", "c4"]
        await pilot.press("J")
        await until(pilot, lambda: [c.source for c in first.cells()] == ["v1 = 1", "v2 = 2", "new", "v3 = 3"])
        await pilot.press("d", "d")
        await until(pilot, lambda: [c.source for c in first.cells()] == ["v1 = 1", "v2 = 2", "v3 = 3"])
        await pilot.press("u")
        await until(pilot, lambda: [c.source for c in first.cells()] == ["v1 = 1", "v2 = 2", "new", "v3 = 3"])
        assert [c.source for c in second.cells()] == [c.source for c in first.cells()]
        await pilot.press("m")
        await until(pilot, lambda: first.cells()[2].kind_shown == "markdown")
        assert first.cells()[2].rendered and second.cells()[2].kind == "markdown"

    ui(test, path)


def test_a_run_in_one_window_shows_its_output_in_both(tmp_path):
    path = tmp_path / "sc.py"
    path.write_text("# %%\nprint('hello from the kernel')\n")

    async def test(app, pilot):
        first, second = await two_views(app, pilot, path)
        await second.run_cells([second.states[0].cid])
        await pilot.pause()
        assert "hello from the kernel" in first.cells()[0].output_text
        assert "hello from the kernel" in second.cells()[0].output_text
        assert first.cells()[0].status == second.cells()[0].status == "ok"

    ui(test, path)


def test_closing_a_view_keeps_the_notebook_and_its_kernel(tmp_path):
    path = notebook(tmp_path)

    async def test(app, pilot):
        first, second = await two_views(app, pilot, path)
        kernel = first.kernel
        await ex(app, pilot, "q")
        await pilot.pause()
        assert app.workspace.buffers == [first] and app.pane is first
        assert first.buffer.views == [first]
        assert all(state.widgets == [cell] for state, cell in zip(first.states, first.cells()))
        assert first.kernel is kernel and not first._shutting_down
        first.select(first.cells()[0], "insert", row=0, column=0)
        await pilot.pause()
        await pilot.press("z")
        await pilot.pause()
        assert first.states[0].source == "zv1 = 1"

    ui(test, path)


def test_closing_the_first_window_leaves_the_second_working(tmp_path):
    path = notebook(tmp_path)

    async def test(app, pilot):
        first, second = await two_views(app, pilot, path)
        app.workspace.set_active(first)
        first.focus_pane()
        await pilot.pause()
        await ex(app, pilot, "q")
        await pilot.pause()
        assert app.workspace.buffers == [second] and app.pane is second
        assert second.buffer.views == [second]
        await ex(app, pilot, "w")
        assert path.read_text().count("v2 = 2") == 1

    ui(test, path)


def test_split_with_the_name_of_the_shown_file_is_a_second_view_and_a_tab_jumps(tmp_path):
    path = notebook(tmp_path)

    async def test(app, pilot):
        first = app.pane
        await ex(app, pilot, f"split {path}")
        second = app.pane
        assert second is not first and second.buffer is first.buffer
        await ex(app, pilot, f"e {path}")
        assert app.pane is second and len(app.workspace.buffers) == 2
        await app.open_file(path, "tab")
        await pilot.pause()
        assert len(app.workspace.buffers) == 2 and len(app.workspace.tabs) == 1

    ui(test, path)


def test_the_mouse_drags_the_border_between_windows(tmp_path):
    path = notebook(tmp_path)

    async def test(app, pilot):
        first, second = await two_views(app, pilot, path)
        workspace = app.workspace
        left = workspace.tab.rects[first]
        assert left.width == 69
        border = workspace.border_at(left.right, 5)
        assert border is not None and workspace.border_at(left.right - 1, 5) is None
        assert workspace.drag_border(border.branch, border.index, 40, 5)
        await pilot.pause()
        assert workspace.tab.rects[first].width == 40
        assert workspace.tab.rects[second].x == 41 and workspace.tab.rects[second].right == 140
        workspace.drag_border(border.branch, border.index, 2, 5)
        await pilot.pause()
        assert workspace.tab.rects[first].width == 8
        origin = workspace.region
        await pilot.mouse_down(workspace, offset=(8, 5))
        await pilot.hover(workspace, offset=(90, 7))
        await pilot.mouse_up(workspace, offset=(90, 7))
        await pilot.pause()
        assert workspace.tab.rects[first].width == 90, origin

    ui(test, path)


def test_the_mouse_drags_the_status_line_of_stacked_windows(tmp_path):
    path = notebook(tmp_path)

    async def test(app, pilot):
        top = app.pane
        await ex(app, pilot, f"split {path}")
        bottom = app.pane
        workspace = app.workspace
        rect = workspace.tab.rects[top]
        border = workspace.border_at(3, rect.bottom - 1)
        assert border is not None and not border.branch.row
        workspace.drag_border(border.branch, border.index, 3, 9)
        await pilot.pause()
        assert workspace.tab.rects[top].height == 10
        assert workspace.tab.rects[bottom].y == 10

    ui(test, path)


def test_two_windows_on_a_long_notebook_keep_their_own_place(tmp_path):
    path = notebook(tmp_path, cells=300)

    async def test(app, pilot):
        first, second = await two_views(app, pilot, path)
        await until(pilot, first.loaded.is_set)
        second.goto_cell(300, "cell")
        await until(pilot, lambda: second.widget_of(second.states[299]) is not None and second.loaded.is_set())
        await pilot.pause(0.2)
        assert first.widget_of(first.states[299]) is None
        assert first.widget_of(first.states[0]) is not None and second.widget_of(second.states[0]) is None
        await pilot.press("b")
        await pilot.pause()
        await pilot.press("l", "a", "s", "t", "escape", "escape")
        await until(pilot, lambda: len(first.states) == 301 and not first._following)
        assert first.states[300].source == "last" and first.states[300].label == "c301"
        first_window = first._window
        assert [c.state for c in first.cells()] == first.states[first_window[0] : first_window[1]]
        app.workspace.set_active(first)
        first.select(first.cells()[0], "cell")
        await pilot.pause()
        await pilot.press("d", "d")
        await until(pilot, lambda: len(second.states) == 300 and not second._following)
        await pilot.pause(0.2)
        lo, hi = second._window
        assert [c.state for c in second.cells()] == second.states[lo:hi]
        assert second.states[299].source == "last" and second.widget_of(second.states[299]) is not None
        await app._run_ex("%s/v/w/")
        await pilot.pause()
        assert first.states[0].source == "w2 = 2"
        far = second.widget_of(second.states[298])
        assert far is not None and far.editor.text == "w300 = 300" == second.states[298].source

    ui(test, path)


def test_markdown_edited_in_one_window_renders_again_in_the_other(tmp_path):
    path = tmp_path / "sc.py"
    path.write_text('# %% [markdown]\n# # Title\n\n# %%\nx = 1\n')

    async def test(app, pilot):
        first, second = await two_views(app, pilot, path)
        mine, theirs = second.cells()[0], first.cells()[0]
        assert mine.rendered and theirs.rendered
        second.select(mine, "cell")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert not mine.rendered and theirs.rendered
        await pilot.press("A", "s", "escape")
        await until(pilot, lambda: theirs._view.text == mine.editor.text == theirs.editor.text and "Titles" in theirs._view.text)
        assert theirs.rendered

    ui(test, path)


def test_bd_closes_every_window_of_the_notebook(tmp_path):
    path = notebook(tmp_path)
    (tmp_path / "other.py").write_text("# %%\no = 1\n")

    async def test(app, pilot):
        await ex(app, pilot, f"tabnew {tmp_path / 'other.py'}")
        other = app.pane
        await app.open_file(path, "tab")
        await pilot.pause()
        first, second = await two_views(app, pilot, path)
        await ex(app, pilot, "bd")
        await pilot.pause()
        assert app.workspace.buffers == [other] and app.pane is other
        assert first.buffer.views == []

    ui(test, path)


def test_a_window_closed_while_its_run_goes_on_leaves_when_the_run_ends(tmp_path):
    path = tmp_path / "sc.py"
    path.write_text("# %%\nimport time\ntime.sleep(1.5)\nprint('done')\n")

    async def test(app, pilot):
        first, second = await two_views(app, pilot, path)
        await until(pilot, lambda: first.kernel.ready.is_set(), 200)
        second.run_all()
        await until(pilot, lambda: first.states[0].status == "running", 200)
        await ex(app, pilot, "q")
        assert app.workspace.buffers == [first] and app.pane is first
        assert second in first.buffer.views and not second.display
        await until(pilot, lambda: first.states[0].status == "ok", 200)
        await until(pilot, lambda: first.buffer.views == [first])
        assert "done" in first.cells()[0].output_text
        assert first.buffer.runner is None

    ui(test, path)
