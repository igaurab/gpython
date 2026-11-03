"""The outline panel (ctrl+o, :outline): each cell with what it defines, live types after a run, edges, jump-to."""

import asyncio

from textual.widgets import Tree

from gpython import notebook
from gpython.app import GPython
from gpython.notebook import CellSpec
from types import SimpleNamespace

from gpython.graph import build
from gpython.outline import Outline, Symbol, Value, children_of, symbols

CELLS = [
    CellSpec("# Analysis\n\nSome text", kind="markdown"),
    CellSpec("import json\n\nxs = [1, 2, 3]"),
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


def marked(outline: Outline) -> list[str]:
    """The label drawn with the ● current marker."""
    node = outline.tree_view.marked
    if node is None:
        return []
    return ["● " + str(node.label)]


def node_for(outline: Outline, cid: str, name: str | None = None):
    for cell_node in cell_nodes(outline):
        if cell_node.data == (cid, None):
            if name is None:
                return cell_node
            return next(n for n in cell_node.children if n.data[1].name == name)
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
    assert symbols("x = (") == ()
    assert symbols("%time x = 1\nx = 2")[0].line == 1
    assert [s.name for s in symbols("x = 1\nx = 2")] == ["x"]
    assert symbols("# Title\n\ntext", kind="markdown") == (Symbol("Title", "heading", "", 0),)
    assert symbols("no heading", kind="markdown") == ()


def test_markdown_heading_skips_fenced_code_and_needs_a_space():
    source = "Intro\n\n```python\n# not a heading\n```\n\n#tag\n~~~\n# no\n~~~\n## Real heading ##"
    assert symbols(source, "markdown") == (Symbol("Real heading", "heading", "", 10),)
    assert symbols("````\n```\n# inside\n````\n# out", "markdown")[0].name == "out"


def test_children_match_the_names_the_graph_says_a_cell_defines():
    source = (
        "for i in range(3): pass\nwith open('f') as fh: pass\nimport json\nx = 1\n"
        "try:\n    import yaml\nexcept ImportError as err:\n    yaml = None\n"
        "if x:\n    def helper(): pass\nys = [k for k in range(2)]\n_private = 2\n(n := 5)\n"
    )
    cell = SimpleNamespace(cid="c1", kind="code", source=source)
    graph = build({"c1": source})
    found = children_of(cell, graph)
    assert {s.name for s in found} == graph.defs["c1"]
    assert {s.name for s in found} == {"i", "fh", "json", "x", "yaml", "err", "helper", "ys", "n"}
    by = {s.name: s for s in found}
    assert by["i"].line == 0 and by["helper"].kind == "function" and by["yaml"].kind == "import"
    glob = "def f():\n    global g\n    g = 1"
    assert [s.name for s in children_of(SimpleNamespace(cid="c", kind="code", source=glob), build({"c": glob}))] == ["f", "g"]


def test_f2_mounts_the_outline_lazily_and_lists_cells_and_symbols(tmp_path):
    async def test(app, pilot):
        assert not app.query(Outline) and not app.pane.outline_visible
        await pilot.press("ctrl+o")
        await pilot.pause()
        outline = app.query_one(Outline)
        assert outline.display and app.pane.outline_visible
        md, c2, c3, c4 = app.cells()
        nodes = cell_nodes(outline)
        assert [n.data for n in nodes] == [(c.cid, None) for c in app.cells()]
        assert outline.tree_view.cursor_node is node_for(outline, app.pane.current_cell().cid)
        assert marked(outline) == ["● " + str(node_for(outline, app.pane.current_cell().cid).label)]
        assert labels(nodes) == ["c1  Analysis", "c2  defines json, xs", "c3  defines Thing, scale", "c4  uses scale, xs"]
        assert labels(nodes[1].children) == ["json  import", "xs"]
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
        assert marked(outline) == ["● scale(x: float) -> float"]
        c3.editor.move_cursor((5, 0))
        await pilot.pause()
        assert outline.tree_view.cursor_node is node_for(outline, c3.cid, "Thing")
        c3.editor.move_cursor((3, 0))
        await pilot.pause()
        assert outline.tree_view.cursor_node is node_for(outline, c3.cid)
        await pilot.press("escape")
        await pilot.pause()
        assert app.focused is c3 and outline.tree_view.cursor_node is node_for(outline, c3.cid)
        assert marked(outline) == ["● c3  defines Thing, scale"]

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
        c3_node = node_for(outline, app.cells()[2].cid)
        c3_leaf = c3_node.children[0]
        c2.editor.text = "import json\n\nxs = [1, 2, 3]\nys = 4"
        await pilot.pause(0.7)
        assert labels(node_for(outline, c2.cid).children) == ["json  import", "xs", "ys"]
        assert node_for(outline, app.cells()[2].cid) is c3_node and c3_node.children[0] is c3_leaf

    ui(tmp_path, test)


def test_variables_show_their_live_type_and_value_after_a_run(tmp_path):
    async def test(app, pilot):
        await pilot.press("ctrl+o")
        await pilot.pause()
        outline = app.query_one(Outline)
        _, c2, c3, _ = app.cells()
        await pilot.press("f5")
        await settle(app, pilot)
        assert labels(node_for(outline, c2.cid).children) == ["json  module", "xs  list  [1, 2, 3]"]
        assert len(marked(outline)) == 1
        assert str(node_for(outline, c3.cid, "scale").label) == "scale(x: float) -> float"
        await app.pane.restart_kernel()
        await pilot.pause()
        assert labels(node_for(outline, c2.cid).children) == ["json  import", "xs"]

    ui(tmp_path, test, kernel=True)


def test_inserts_deletes_and_moves_keep_the_other_nodes(tmp_path):
    async def test(app, pilot):
        await pilot.press("ctrl+o")
        await pilot.pause()
        outline = app.query_one(Outline)
        pane = app.pane

        def in_order():
            return [n.data for n in cell_nodes(outline)] == [(c.cid, None) for c in pane.cells()]

        md, c2, c3, c4 = pane.cells()
        kept = node_for(outline, c3.cid)
        pane.select(c4, "cell")
        await pane.delete_cell()
        await pilot.pause()
        assert in_order() and node_for(outline, c3.cid) is kept
        pane.select(c2, "cell")
        await pane.add_cell(above=True)
        await pilot.pause()
        assert in_order() and node_for(outline, c3.cid) is kept and len(pane.cells()) == 4
        assert [label.split()[0] for label in labels(cell_nodes(outline))] == [c.label for c in pane.cells()]
        pane.select(c3, "cell")
        pane.move_cell(-1)
        await pilot.pause()
        assert in_order() and labels(node_for(outline, c3.cid).children) == ["scale(x: float) -> float", "class Thing"]

    ui(tmp_path, test)


def test_the_marker_is_drawn_not_stored_in_the_label(tmp_path):
    async def test(app, pilot):
        await pilot.press("ctrl+o")
        await pilot.pause()
        outline = app.query_one(Outline)
        _, c2, _, _ = app.cells()
        app.pane.select(c2, "cell")
        await pilot.pause()
        node = node_for(outline, c2.cid)
        assert outline.tree_view.marked is node and not str(node.label).startswith("●")
        style = outline.tree_view.rich_style
        assert outline.tree_view.render_label(node, style, style).plain.endswith("● c2  defines json, xs")

    ui(tmp_path, test)


def test_values_belong_to_the_cell_that_bound_the_name_last(tmp_path):
    async def test(app, pilot):
        pane = app.pane
        _, c2, c3, _ = app.cells()
        pane._values = {"xs": Value("list", "[1]"), "json": Value("module", "")}
        c2.last_defs, c2.exec_count = {"xs", "json"}, 1
        c3.last_defs, c3.exec_count = {"xs"}, 2
        assert pane._cell_values(app.cells()) == {(c3.cid, "xs"): Value("list", "[1]"), (c2.cid, "json"): Value("module", "")}

    ui(tmp_path, test)
