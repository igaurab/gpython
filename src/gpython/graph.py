"""Static analysis of cells: which names each cell defines and uses.

Cells form a directed graph: an edge A -> B means B uses a name A defines.
Names starting with "_" are private to their cell and never create edges.
"""

from __future__ import annotations

import ast
import builtins
import symtable
from dataclasses import dataclass, field

_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
_BUILTINS = set(dir(builtins))
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _module_stores(tree: ast.Module) -> tuple[set[str], set[str]]:
    """Names stored at module level, split into (real, comprehension-only).

    Python 3.12+ inlines comprehensions (PEP 709), so symtable reports their
    loop variables as module-level assignments. We use the AST to undo that.
    """
    real: set[str] = set()
    comp: set[str] = set()

    def visit(node: ast.AST, in_comp: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _SCOPES):
                continue
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                (comp if in_comp else real).add(child.id)
            # Walrus inside a comprehension binds in the enclosing scope.
            if isinstance(child, ast.NamedExpr):
                real.add(child.target.id)
            visit(child, in_comp or isinstance(child, _COMPREHENSIONS))

    visit(tree, False)
    return real, comp - real


def analyze(source: str) -> tuple[set[str], set[str]]:
    """Return (defs, refs) for one cell. Raises SyntaxError."""
    table = symtable.symtable(source, "<cell>", "exec")
    _, comp_only = _module_stores(ast.parse(source))
    defs: set[str] = set()
    refs: set[str] = set()

    for sym in table.get_symbols():
        if sym.get_name() in comp_only:
            continue
        if sym.is_assigned() or sym.is_imported():
            defs.add(sym.get_name())
        elif sym.is_referenced():
            refs.add(sym.get_name())

    # Functions, classes and comprehensions have their own scopes; any
    # global they read counts as a use of the cell, and `global x; x = ...`
    # counts as a definition.
    stack = list(table.get_children())
    while stack:
        child = stack.pop()
        stack.extend(child.get_children())
        for sym in child.get_symbols():
            if not sym.is_global():
                continue
            if sym.is_declared_global() and sym.is_assigned():
                defs.add(sym.get_name())
            elif sym.is_referenced():
                refs.add(sym.get_name())

    public = lambda names: {n for n in names if not n.startswith("_")}
    defs = public(defs)
    # Builtins are not cell dependencies (unless a cell shadows one).
    return defs, public(refs) - defs - _BUILTINS


@dataclass
class Graph:
    order: list[str]  # cell ids in display order
    defs: dict[str, set[str]] = field(default_factory=dict)
    refs: dict[str, set[str]] = field(default_factory=dict)
    parents: dict[str, set[str]] = field(default_factory=dict)
    children: dict[str, set[str]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def descendants(self, roots: list[str]) -> set[str]:
        seen = set(roots)
        stack = list(roots)
        while stack:
            for child in self.children[stack.pop()]:
                if child not in seen:
                    seen.add(child)
                    stack.append(child)
        return seen

    def run_order(self, roots: list[str]) -> list[str]:
        """Roots plus everything downstream, parents before children.

        Ties are broken by display order, so independent cells run top to bottom.
        Cells stuck in a cycle are appended last (they carry an error anyway).
        """
        wanted = self.descendants(roots)
        indegree = {c: len(self.parents[c] & wanted) for c in wanted}
        result: list[str] = []
        ready = [c for c in self.order if c in wanted and indegree[c] == 0]
        while ready:
            cell = ready.pop(0)
            result.append(cell)
            for child in self.children[cell]:
                if child in wanted:
                    indegree[child] -= 1
                    if indegree[child] == 0:
                        ready.append(child)
                        ready.sort(key=self.order.index)
        result += [c for c in self.order if c in wanted and c not in result]
        return result


def build(cells: dict[str, str]) -> Graph:
    """Build the graph for cells given as {cell_id: source} in display order."""
    g = Graph(order=list(cells))
    for cid, source in cells.items():
        try:
            g.defs[cid], g.refs[cid] = analyze(source)
        except SyntaxError as e:
            g.defs[cid], g.refs[cid] = set(), set()
            g.errors[cid] = f"SyntaxError: {e.msg} (line {e.lineno})"

    owners: dict[str, list[str]] = {}
    for cid in g.order:
        for name in g.defs[cid]:
            owners.setdefault(name, []).append(cid)
    for name, cids in owners.items():
        if len(cids) > 1:
            for cid in cids:
                g.errors.setdefault(
                    cid, f"'{name}' is defined in {len(cids)} cells; define it in one"
                )

    for cid in g.order:
        g.parents[cid] = set()
        g.children.setdefault(cid, set())
    for cid in g.order:
        for name in g.refs[cid]:
            for owner in owners.get(name, []):
                if owner != cid:
                    g.parents[cid].add(owner)
                    g.children[owner].add(cid)

    # Kahn's algorithm over the whole graph; leftovers are in or behind a cycle.
    indegree = {c: len(g.parents[c]) for c in g.order}
    ready = [c for c in g.order if indegree[c] == 0]
    done = set()
    while ready:
        cell = ready.pop()
        done.add(cell)
        for child in g.children[cell]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    for cid in g.order:
        if cid not in done:
            g.errors.setdefault(cid, "cell is part of (or depends on) a cycle")
    return g
