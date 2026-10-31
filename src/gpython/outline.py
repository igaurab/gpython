"""The outline panel (ctrl+o, :outline): one node per cell with the names it defines, the kernel's
types and values once the cell has run, and the dependency edges of the current cell. The
notebook's tagbar, on the right like the file explorer is on the left.

Symbols come from the AST, so the tree is right before anything runs; `Kernel.variables`
fills in type and repr afterwards. The panel is built on first use and costs nothing hidden.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static, Tree
from textual.widgets.tree import TreeNode

from .graph import Graph, strip_magics

Values = dict[str, tuple[str, str]]


@dataclass(frozen=True)
class Symbol:
    """One top-level name of a cell. kind: import, variable, function, class or heading."""

    name: str
    kind: str
    detail: str
    line: int
    end: int = -1


def symbols(source: str, kind: str = "code") -> list[Symbol]:
    """The names a cell defines at its top level, in source order, each name once.

    A markdown cell gives its first heading. Unparsable code gives nothing."""
    if kind == "markdown":
        return _heading(source)
    try:
        tree = ast.parse(strip_magics(source))
    except (SyntaxError, ValueError):
        return []
    found: list[Symbol] = []
    seen: set[str] = set()

    def add(name: str, kind: str, detail: str, node: ast.stmt) -> None:
        if name not in seen:
            seen.add(name)
            found.append(Symbol(name, kind, detail, node.lineno - 1, (node.end_lineno or node.lineno) - 1))

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add(node.name, "function", _signature(node), node)
        elif isinstance(node, ast.ClassDef):
            bases = ", ".join(ast.unparse(base) for base in node.bases)
            add(node.name, "class", f"({bases})" if bases else "", node)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                add((alias.asname or alias.name).split(".")[0], "import", "", node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for name in _names(target):
                    add(name, "variable", "", node)
    return found


def _names(target: ast.AST):
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, (ast.Tuple, ast.List)):
        for element in target.elts:
            yield from _names(element)
    elif isinstance(target, ast.Starred):
        yield from _names(target.value)


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    text = f"({ast.unparse(node.args)})"
    if node.returns is not None:
        text += f" -> {ast.unparse(node.returns)}"
    return f"async {text}" if isinstance(node, ast.AsyncFunctionDef) else text


def _heading(source: str) -> list[Symbol]:
    for line, text in enumerate(source.splitlines()):
        stripped = text.strip()
        if stripped.startswith("#") and (title := stripped.lstrip("#").strip()):
            return [Symbol(title, "heading", "", line)]
    return []


def cell_label(cell) -> Text:
    """`c2  defines np, xs`: the header's info; a markdown cell shows its first heading."""
    info = cell.info
    if cell.kind == "markdown":
        heads = symbols(cell.source, "markdown")
        info = heads[0].name if heads else "markdown"
    return Text.assemble((cell.label, "bold"), "  ", (info, "dim")) if info else Text(cell.label, style="bold")


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
        self.tree_view: Tree = Tree("notebook")
        self.tree_view.show_root = False
        self.tree_view.guide_depth = 2
        self.tree_view.auto_expand = False
        self.edges = Static(classes="outline-edges")
        self._values: Values = {}
        self._graph: Graph | None = None
        self._cell_nodes: dict[str, TreeNode] = {}
        self._labels: dict[str, str] = {}
        self._order: list[str] = []
        self._current: tuple[str | None, int | None] = (None, None)

    def compose(self) -> ComposeResult:
        yield self.tree_view
        yield self.edges

    def rebuild(self, cells: list, graph: Graph, values: Values, current: str | None, row: int | None = None) -> None:
        """One node per cell with its symbols, the cursor on `current` (at `row`, the editor's line).

        Nodes get their line numbers when the tree first renders, so the cursor is placed after
        the next refresh."""
        self._values, self._graph = values, graph
        self._order = [cell.cid for cell in cells]
        self._labels = {cell.cid: cell.label for cell in cells}
        self.tree_view.clear()
        self._cell_nodes.clear()
        for cell in cells:
            node = self.tree_view.root.add(cell_label(cell), ("cell", cell.cid), expand=True)
            self._cell_nodes[cell.cid] = node
            for symbol in symbols(cell.source, cell.kind):
                if symbol.kind != "heading":
                    data = ("symbol", cell.cid, symbol.name, symbol.line, symbol.kind, symbol.detail, symbol.end)
                    node.add_leaf(self._symbol_label(symbol), data)
        self._current = (None, None)
        self.call_after_refresh(self.follow, current, row)

    def set_values(self, values: Values) -> None:
        """The kernel's types and values after a run (or none after a restart), without rebuilding."""
        self._values = values
        for cell_node in self._cell_nodes.values():
            for node in cell_node.children:
                _, _, name, line, kind, detail, end = node.data
                node.set_label(self._symbol_label(Symbol(name, kind, detail, line, end)))

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
                    if child.data[3] <= row <= child.data[6] and child.line != -1:
                        target = child
            if self.tree_view.cursor_node is not target:
                self.tree_view.move_cursor(target)
        self._show_edges(cid)

    def _symbol_label(self, symbol: Symbol) -> Text:
        if symbol.kind == "function":
            return Text.assemble(symbol.name, (symbol.detail, "dim"))
        if symbol.kind == "class":
            return Text.assemble(("class ", "dim"), symbol.name, (symbol.detail, "dim"))
        value = self._values.get(symbol.name)
        if value is not None:
            kind, shown = value
            parts = [symbol.name, "  ", (kind, "dim")]
            if kind != "module":
                parts += ["  ", (shown, "dim italic")]
            return Text.assemble(*parts)
        if symbol.kind == "import":
            return Text.assemble(symbol.name, "  ", ("import", "dim"))
        return Text(symbol.name)

    def _show_edges(self, cid: str | None) -> None:
        graph = self._graph
        if cid is None or graph is None or cid not in graph.parents:
            self.edges.update("")
            return
        uses = [self._labels[c] for c in self._order if c in graph.parents.get(cid, ())]
        used = [self._labels[c] for c in self._order if c in graph.children.get(cid, ())]
        parts = []
        if uses:
            parts.append("← uses " + ", ".join(uses))
        if used:
            parts.append("→ used by " + ", ".join(used))
        self.edges.update(Text("   ".join(parts) or "no edges", style="dim"))

    def action_cursor(self, step: int) -> None:
        self.tree_view.action_cursor_down() if step > 0 else self.tree_view.action_cursor_up()

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
        data = event.node.data
        if data:
            self.post_message(self.Jump(data[1], None if data[0] == "cell" else data[3]))
