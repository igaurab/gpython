"""The outline panel (ctrl+o, :outline): one node per cell with the names it defines, the kernel's
types and values once the cell has run, and the dependency edges of the current cell. The
notebook's tagbar, on the right like the file explorer is on the left.

Symbols come from the AST, so the tree is right before anything runs; `Kernel.variables`
fills in type and repr afterwards. The panel is built on first use and costs nothing hidden.
A change updates only the nodes whose cell changed, and the cursor marker is drawn in
`render_label`, so following the cursor repaints two lines.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import NamedTuple

from rich.style import Style
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static, Tree
from textual.widgets.tree import TreeNode

from .graph import Graph, strip_magics


class Value(NamedTuple):
    """A name's live state in the kernel after a run."""

    kind: str
    shown: str


Values = dict[tuple[str, str], Value]

_MARK_STYLE = Style(bold=True)
_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})")
_HEADING = re.compile(r" {0,3}#{1,6}(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


@dataclass(frozen=True)
class Symbol:
    """One module-level name of a cell. kind: import, variable, function, class or heading."""

    name: str
    kind: str
    detail: str
    line: int
    end: int = -1


@lru_cache(maxsize=4096)
def symbols(source: str, kind: str = "code") -> tuple[Symbol, ...]:
    """The names a cell binds at module level, in source order, each name once.

    The walk covers the same stores as `graph.analyze` (for and with targets, except names,
    imports and definitions nested in if or try), so the node's children match its label.
    A markdown cell gives its first heading. Unparsable code gives nothing. Memoised: an
    unchanged cell costs a dictionary lookup."""
    if kind == "markdown":
        return _heading(source)
    try:
        tree = ast.parse(strip_magics(source))
    except (SyntaxError, ValueError):
        return ()
    found: dict[str, Symbol] = {}
    for statement in tree.body:
        for symbol in _bindings(statement, statement, in_comprehension=False):
            found.setdefault(symbol.name, symbol)
    return tuple(found.values())


def _bindings(node: ast.AST, statement: ast.stmt, in_comprehension: bool):
    """Symbols bound by `node` at module level; `statement` is the innermost statement around it."""
    if isinstance(node, ast.stmt):
        statement = node
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        yield _symbol(node.name, "function", _signature(node), node)
        return
    if isinstance(node, ast.ClassDef):
        bases = ", ".join(ast.unparse(base) for base in node.bases)
        yield _symbol(node.name, "class", f"({bases})" if bases else "", node)
        return
    if isinstance(node, ast.Lambda):
        return
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        for alias in node.names:
            yield _symbol((alias.asname or alias.name).split(".")[0], "import", "", node)
        return
    if isinstance(node, ast.NamedExpr):
        yield _symbol(node.target.id, "variable", "", statement)
    elif not in_comprehension:
        name = _bound_name(node)
        if name:
            yield _symbol(name, "variable", "", statement)
    inner = in_comprehension or isinstance(node, _COMPREHENSIONS)
    for child in ast.iter_child_nodes(node):
        yield from _bindings(child, statement, inner)


def _bound_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
        return node.id
    if isinstance(node, ast.ExceptHandler):
        return node.name
    if isinstance(node, (ast.MatchAs, ast.MatchStar)):
        return node.name
    if isinstance(node, ast.MatchMapping):
        return node.rest
    return None


def _symbol(name: str, kind: str, detail: str, node: ast.AST) -> Symbol:
    return Symbol(name, kind, detail, node.lineno - 1, (node.end_lineno or node.lineno) - 1)


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    text = f"({ast.unparse(node.args)})"
    if node.returns is not None:
        text += f" -> {ast.unparse(node.returns)}"
    return f"async {text}" if isinstance(node, ast.AsyncFunctionDef) else text


def _heading(source: str) -> tuple[Symbol, ...]:
    """The first ATX heading outside fenced code blocks."""
    fence = ""
    for line, text in enumerate(source.splitlines()):
        opening = _FENCE.match(text)
        if fence:
            if opening and opening.group(1).startswith(fence) and not text[opening.end():].strip():
                fence = ""
            continue
        if opening:
            fence = opening.group(1)
            continue
        heading = _HEADING.match(text)
        if heading and heading.group(1):
            return (Symbol(heading.group(1), "heading", "", line),)
    return ()


def children_of(cell, graph: Graph) -> tuple[Symbol, ...]:
    """A code cell's leaves: exactly the names `graph.defs` gives it (which its label lists)."""
    if cell.kind == "markdown":
        return ()
    defs = graph.defs.get(cell.cid, set())
    found = tuple(symbol for symbol in symbols(cell.source) if symbol.name in defs)
    if len(found) == len(defs):
        return found
    listed = {symbol.name for symbol in found}
    return found + tuple(Symbol(name, "variable", "", 0) for name in sorted(defs - listed))


def cell_label(cell) -> Text:
    """`c2  defines np, xs`: the header's info; a markdown cell shows its first heading."""
    info = cell.info
    if cell.kind == "markdown":
        heads = symbols(cell.source, "markdown")
        info = heads[0].name if heads else "markdown"
    return Text.assemble((cell.label, "bold"), "  ", (info, "dim")) if info else Text(cell.label, style="bold")


class OutlineTree(Tree):
    """A Tree that draws ● before the node at the notebook's cursor: the blurred tree cursor is
    invisible in some themes. The marker lives in the rendering, not the label, so moving it
    repaints the two lines involved and nothing else."""

    def __init__(self) -> None:
        super().__init__("notebook")
        self.marked: TreeNode | None = None

    def mark(self, node: TreeNode | None) -> None:
        previous, self.marked = self.marked, node
        if previous is node:
            return
        for each in (previous, node):
            if each is not None and each.tree is self and each.line != -1:
                each.refresh()

    def render_label(self, node: TreeNode, base_style: Style, style: Style) -> Text:
        text = super().render_label(node, base_style, style)
        if node is not self.marked:
            return text
        icon = len(text) - len(node.label)
        return Text.assemble(text[:icon], Text("● ", style=style + _MARK_STYLE), text[icon:])


class Outline(Vertical):
    """A Tree of cells and their symbols over a one-line footer with the current cell's edges."""

    BINDINGS = [
        Binding("j", "cursor(1)", show=False),
        Binding("k", "cursor(-1)", show=False),
        Binding("h", "fold(False)", show=False),
        Binding("l", "fold(True)", show=False),
        Binding("q", "close(True)", show=False),
        Binding("escape", "close(False)", show=False),
    ]

    class Jump(Message):
        """Enter on a node: go to the cell; `line` None selects it, a line opens its editor there."""

        def __init__(self, cid: str, line: int | None) -> None:
            super().__init__()
            self.cid, self.line = cid, line

    class Closed(Message):
        """q (hide the panel) or escape (keep it): the notebook takes the focus back."""

        def __init__(self, hide: bool) -> None:
            super().__init__()
            self.hide = hide

    def __init__(self) -> None:
        super().__init__(id="outline")
        self.tree_view = OutlineTree()
        self.tree_view.show_root = False
        self.tree_view.guide_depth = 2
        self.tree_view.auto_expand = False
        self.edges = Static(classes="outline-edges")
        self._values: Values = {}
        self._graph: Graph | None = None
        self._cell_nodes: dict[str, TreeNode] = {}
        self._leaf_nodes: dict[tuple[str, str], TreeNode] = {}
        self._label_keys: dict[str, tuple] = {}
        self._leaf_keys: dict[str, tuple[Symbol, ...]] = {}
        self._labels: dict[str, str] = {}
        self._order: list[str] = []
        self._current: tuple[str | None, int | None] = (None, None)
        self._edges_shown: str | None = None
        self._edges_lines = 0

    def compose(self) -> ComposeResult:
        yield self.tree_view
        yield self.edges

    def rebuild(self, cells: list, graph: Graph, values: Values, current: str | None, row: int | None = None) -> None:
        """Bring the tree in line with the cells, the cursor on `current` (at `row`, the editor's line).

        Only cells whose label or symbols changed touch their node; inserts, deletes and moves
        add or remove single nodes. Nodes get their line numbers when the tree renders, so the
        cursor is placed after the next refresh."""
        self._graph = graph
        self._order = [cell.cid for cell in cells]
        self._labels = {cell.cid: cell.label for cell in cells}
        self._place(cells)
        previous, self._values = self._values, values
        for cell in cells:
            self._fill(cell, graph)
        self._relabel_values(previous)
        self._current = (None, None)
        self._edges_shown = None
        self.call_after_refresh(self.follow, current, row)

    def _place(self, cells: list) -> None:
        """Cell nodes in display order: drop the gone ones, add the new ones, re-add the moved ones."""
        root = self.tree_view.root
        wanted = set(self._order)
        for cid in [cid for cid in self._cell_nodes if cid not in wanted]:
            self._drop(cid)
        for index, cell in enumerate(cells):
            node = self._cell_nodes.get(cell.cid)
            children = root.children
            if node is not None and index < len(children) and children[index] is node:
                continue
            expand = node.is_expanded if node is not None else True
            if node is not None:
                self._drop(cell.cid)
            before = index if index < len(root.children) else None
            self._cell_nodes[cell.cid] = root.add(cell_label(cell), (cell.cid, None), before=before, expand=expand)

    def _drop(self, cid: str) -> None:
        node = self._cell_nodes.pop(cid)
        for symbol in self._leaf_keys.pop(cid, ()):
            self._leaf_nodes.pop((cid, symbol.name), None)
        self._label_keys.pop(cid, None)
        if self.tree_view.marked is not None and self.tree_view.marked in (node, *node.children):
            self.tree_view.marked = None
        node.remove()

    def _fill(self, cell, graph: Graph) -> None:
        cid, node = cell.cid, self._cell_nodes[cell.cid]
        label_key = (cell.label, cell.info, cell.kind, cell.source if cell.kind == "markdown" else "")
        if self._label_keys.get(cid) != label_key:
            self._label_keys[cid] = label_key
            node.set_label(cell_label(cell))
        leaves = children_of(cell, graph)
        if self._leaf_keys.get(cid) == leaves:
            return
        for symbol in self._leaf_keys.get(cid, ()):
            self._leaf_nodes.pop((cid, symbol.name), None)
        if self.tree_view.marked is not None and self.tree_view.marked.parent is node:
            self.tree_view.marked = None
        self._leaf_keys[cid] = leaves
        node.remove_children()
        for symbol in leaves:
            self._leaf_nodes[(cid, symbol.name)] = node.add_leaf(self._symbol_label(cid, symbol), (cid, symbol))

    def set_values(self, values: Values) -> None:
        """The kernel's types and values after a run (or none after a restart), without rebuilding."""
        previous, self._values = self._values, values
        self._relabel_values(previous)

    def _relabel_values(self, previous: Values) -> None:
        values = self._values
        for key in previous.keys() | values.keys():
            if previous.get(key) != values.get(key) and (leaf := self._leaf_nodes.get(key)) is not None:
                leaf.set_label(self._symbol_label(*leaf.data))

    def follow(self, cid: str | None, row: int | None = None) -> None:
        """Put the cursor on the cell's node, or on the symbol whose lines hold the editor's
        `row`, and show the cell's edges; the focus stays where it is."""
        if (cid, row) == self._current:
            return
        self._current = (cid, row)
        node = self._cell_nodes.get(cid) if cid else None
        if node is not None:
            target = node
            if row is not None:
                for child in node.children:
                    symbol = child.data[1]
                    if symbol.line <= row <= symbol.end and child.line != -1:
                        target = child
            if self.tree_view.cursor_node is not target:
                self.tree_view.move_cursor(target)
            self.tree_view.mark(target)
        self._show_edges(cid)

    def _symbol_label(self, cid: str, symbol: Symbol) -> Text:
        if symbol.kind == "function":
            return Text.assemble(symbol.name, (symbol.detail, "dim"))
        if symbol.kind == "class":
            return Text.assemble(("class ", "dim"), symbol.name, (symbol.detail, "dim"))
        value = self._values.get((cid, symbol.name))
        if value is not None:
            parts = [symbol.name, "  ", (value.kind, "dim")]
            if value.kind != "module":
                parts += ["  ", (value.shown, "dim italic")]
            return Text.assemble(*parts)
        if symbol.kind == "import":
            return Text.assemble(symbol.name, "  ", ("import", "dim"))
        return Text(symbol.name)

    def _show_edges(self, cid: str | None) -> None:
        """The footer's text; a layout pass only when its wrapped height changes."""
        text = self._edges_text(cid)
        if text.plain == self._edges_shown:
            return
        self._edges_shown = text.plain
        width = max(1, self.edges.content_size.width)
        lines = len(text.wrap(self.app.console, width)) if text.plain else 0
        self.edges.update(text, layout=lines != self._edges_lines)
        self._edges_lines = lines

    def _edges_text(self, cid: str | None) -> Text:
        graph = self._graph
        if cid is None or graph is None or cid not in graph.parents:
            return Text("")
        uses = [self._labels[c] for c in self._order if c in graph.parents.get(cid, ())]
        used = [self._labels[c] for c in self._order if c in graph.children.get(cid, ())]
        parts = []
        if uses:
            parts.append("← uses " + ", ".join(uses))
        if used:
            parts.append("→ used by " + ", ".join(used))
        return Text("   ".join(parts) or "no links to other cells", style="dim")

    def action_cursor(self, step: int) -> None:
        if step > 0:
            self.tree_view.action_cursor_down()
        else:
            self.tree_view.action_cursor_up()

    def action_fold(self, open: bool) -> None:
        node = self.tree_view.cursor_node
        if node is None:
            return
        target = node if node.allow_expand else node.parent
        if target is None or target is self.tree_view.root:
            return
        if open:
            target.expand()
        else:
            target.collapse()
            self.tree_view.move_cursor(target)

    def action_close(self, hide: bool) -> None:
        self.post_message(self.Closed(hide))

    @on(Tree.NodeSelected)
    def _selected(self, event: Tree.NodeSelected) -> None:
        event.stop()
        if not event.node.data:
            return
        cid, symbol = event.node.data
        self.post_message(self.Jump(cid, None if symbol is None else symbol.line))
