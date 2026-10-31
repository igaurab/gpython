"""The outline panel (ctrl+o, :outline): each cell with what it defines, live types after a run, edges, jump-to."""

import asyncio

from textual.widgets import Tree

from gpython import notebook
from gpython.app import GPython
from gpython.notebook import CellSpec
from gpython.outline import Outline, Symbol, symbols

CELLS = [
    CellSpec("# Analysis\n\nSome text", kind="markdown"),
    CellSpec("import numpy as np\n\nxs = [1, 2, 3]"),
    CellSpec("def scale(x: float) -> float:\n    return x * 2\n\n\nclass Thing:\n    pass"),
    CellSpec("print(scale(xs[0]))"),
]


def ui(tmp_path, test, kernel=False):
    path = tmp_path / "nb.py"
    notebook.save(path, CELLS)

    async def main():
        app = GPython(path)
        async with app.run_test(size=(140, 40)) as pilot:
            await app.loaded.wait()
            if kernel:
                await asyncio.wait_for(app.kernel.ready.wait(), 60)
            await pilot.pause()
            await test(app, pilot)

    asyncio.run(asyncio.wait_for(main(), 120))


async def settle(app, pilot):
    for _ in range(3):
        await pilot.pause()
        await app.workers.wait_for_complete()
    await pilot.pause()


def cell_nodes(outline: Outline) -> list:
    return list(outline.tree_view.root.children)


def labels(nodes) -> list[str]:
    return [str(node.label) for node in nodes]


def node_for(outline: Outline, cid: str, name: str | None = None):
    for cell_node in cell_nodes(outline):
        if cell_node.data == ("cell", cid):
            if name is None:
                return cell_node
            return next(n for n in cell_node.children if n.data[2] == name)
    raise AssertionError(f"no node for {cid} {name}")


def edges(outline: Outline) -> str:
    return str(outline.edges.render())


def test_symbols_lists_top_level_definitions_in_order():
    source = (
        "import numpy as np\nfrom os import path as p\nxs = [1]\na, b = 1, 2\nn: int = 3\n\n"
        "def scale(x: float, *, k=2) -> float:\n    return x\n\nclass Thing(Base):\n    inner = 1\n\nasync def fetch(url): ...\n"
    )
    found = symbols(source)
    assert found[6].end == 7 and found[7].end == 10 and found[2].end == 2
    assert [(s.name, s.kind, s.line) for s in found] == [
        ("np", "import", 0), ("p", "import", 1), ("xs", "variable", 2), ("a", "variable", 3), ("b", "variable", 3),
        ("n", "variable", 4), ("scale", "function", 6), ("Thing", "class", 9), ("fetch", "function", 12),
    ]
    by = {s.name: s for s in found}
    assert by["scale"].detail == "(x: float, *, k=2) -> float"
    assert by["Thing"].detail == "(Base)" and by["fetch"].detail == "async (url)" and by["xs"].detail == ""
    assert symbols("x = (") == []
    assert symbols("%time x = 1\nx = 2")[0].line == 1
    assert [s.name for s in symbols("x = 1\nx = 2")] == ["x"]
    assert symbols("# Title\n\ntext", kind="markdown") == [Symbol("Title", "heading", "", 0)]
    assert symbols("no heading", kind="markdown") == []


def test_f2_mounts_the_outline_lazily_and_lists_cells_and_symbols(tmp_path):
    async def test(app, pilot):
        assert not app.query(Outline) and not app.pane.outline_visible
        await pilot.press("ctrl+o")
        await pilot.pause()
        outline = app.query_one(Outline)
        assert outline.display and app.pane.outline_visible
        md, c2, c3, c4 = app.cells()
        nodes = cell_nodes(outline)
        assert [n.data for n in nodes] == [("cell", c.cid) for c in app.cells()]
        assert outline.tree_view.cursor_node is node_for(outline, app.pane.current_cell().cid)
        assert labels(nodes) == ["c1  Analysis", "c2  defines np, xs", "c3  defines Thing, scale", "c4  uses scale, xs"]
        assert labels(nodes[1].children) == ["np  import", "xs"]
        assert labels(nodes[2].children) == ["scale(x: float) -> float", "class Thing"]
        assert len(nodes[3].children) == 0 and nodes[1].is_expanded
        assert app.focused is not outline.tree_view

        await pilot.press("ctrl+o")
        await pilot.pause()
        assert not outline.display and not app.pane.outline_visible
        await app._run_ex("outline")
        await pilot.pause()
        assert outline.display and len(app.query(Outline)) == 1

    ui(tmp_path, test)


def test_outline_follows_the_current_cell_and_shows_its_edges(tmp_path):
    async def test(app, pilot):
        await pilot.press("ctrl+o")
        await pilot.pause()
        outline = app.query_one(Outline)
        _, c2, c3, c4 = app.cells()
        app.pane.select(c4, "cell")
        await pilot.pause()
        assert outline.tree_view.cursor_node is node_for(outline, c4.cid)
        assert "← uses c2, c3" in edges(outline) and "used by" not in edges(outline)
        assert app.focused is c4

        app.pane.select(c3, "normal", row=0)
        await pilot.pause()
        assert outline.tree_view.cursor_node is node_for(outline, c3.cid, "scale")
        assert "→ used by c4" in edges(outline) and "uses" not in edges(outline)
        assert app.focused is c3.editor

        c3.editor.move_cursor((1, 4))
        await pilot.pause()
        assert outline.tree_view.cursor_node is node_for(outline, c3.cid, "scale")
        c3.editor.move_cursor((5, 0))
        await pilot.pause()
        assert outline.tree_view.cursor_node is node_for(outline, c3.cid, "Thing")
        c3.editor.move_cursor((3, 0))
        await pilot.pause()
        assert outline.tree_view.cursor_node is node_for(outline, c3.cid)
        await pilot.press("escape")
        await pilot.pause()
        assert app.focused is c3 and outline.tree_view.cursor_node is node_for(outline, c3.cid)

    ui(tmp_path, test)


def test_ctrl_o_in_insert_mode_stays_vims_one_shot_command(tmp_path):
    async def test(app, pilot):
        _, c2, _, _ = app.cells()
        c2.editor.focus()
        await pilot.press("i", "ctrl+o")
        await pilot.pause()
        assert not app.pane.outline_visible and app.ui_mode == "normal"
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert app.pane.outline_visible

    ui(tmp_path, test)


def test_enter_jumps_to_the_symbol_line_or_the_cell_and_q_closes(tmp_path):
    async def test(app, pilot):
        await pilot.press("ctrl+o")
        await pilot.pause()
        outline = app.query_one(Outline)
        _, c2, c3, c4 = app.cells()
        await pilot.press("ctrl+w", "l")
        await pilot.pause()
        assert app.focused is outline.tree_view
        outline.tree_view.move_cursor(node_for(outline, c3.cid, "Thing"))
        await pilot.press("enter")
        await pilot.pause()
        assert app.focused is c3.editor and c3.editor.cursor_location == (4, 0) and app.ui_mode == "normal"

        await pilot.press("ctrl+w", "l")
        await pilot.pause()
        outline.tree_view.move_cursor(node_for(outline, c2.cid))
        await pilot.press("enter")
        await pilot.pause()
        assert app.focused is c2 and app.cell_mode

        await pilot.press("ctrl+w", "l")
        await pilot.pause()
        assert app.focused is outline.tree_view
        await pilot.press("escape")
        await pilot.pause()
        assert app.focused is c2 and outline.display
        await pilot.press("ctrl+w", "l")
        await pilot.press("q")
        await pilot.pause()
        assert app.focused is c2 and not outline.display and not app.pane.outline_visible

    ui(tmp_path, test)


def test_edits_update_the_outline_after_a_pause(tmp_path):
    async def test(app, pilot):
        await pilot.press("ctrl+o")
        await pilot.pause()
        outline = app.query_one(Outline)
        _, c2, _, _ = app.cells()
        c2.editor.text = "import numpy as np\n\nxs = [1, 2, 3]\nys = 4"
        await pilot.pause(0.7)
        assert labels(node_for(outline, c2.cid).children) == ["np  import", "xs", "ys"]

    ui(tmp_path, test)


def test_variables_show_their_live_type_and_value_after_a_run(tmp_path):
    async def test(app, pilot):
        await pilot.press("ctrl+o")
        await pilot.pause()
        outline = app.query_one(Outline)
        _, c2, c3, _ = app.cells()
        await pilot.press("f5")
        await settle(app, pilot)
        assert labels(node_for(outline, c2.cid).children) == ["np  module", "xs  list  [1, 2, 3]"]
        assert str(node_for(outline, c3.cid, "scale").label) == "scale(x: float) -> float"
        await app.pane.restart_kernel()
        await pilot.pause()
        assert labels(node_for(outline, c2.cid).children) == ["np  import", "xs"]

    ui(tmp_path, test, kernel=True)
