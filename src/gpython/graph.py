"""Static analysis of cells: which names each cell defines and uses.

Cells form a directed graph: an edge A -> B means B uses a name A defines.
Names starting with "_" are private to their cell and never create edges.
"""

from __future__ import annotations

import ast
import heapq
import json
import builtins
import symtable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

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
            if isinstance(child, ast.NamedExpr):
                real.add(child.target.id)
            visit(child, in_comp or isinstance(child, _COMPREHENSIONS))

    visit(tree, False)
    return real, comp - real


def strip_magics(source: str) -> str:
    """Blank out IPython-only lines (%magic, !shell, name?) so they parse as Python.

    The kernel still runs the original source; this is only for analysis.
    """
    out = []
    for line in source.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("%", "!")) or (stripped.endswith("?") and " " not in stripped):
            out.append("")
        else:
            out.append(line)
    return "\n".join(out)


@lru_cache(maxsize=1)
def calls_table() -> dict:
    """calls.json: what calls do to their arguments (mutating methods here, the rest for concurrency.py)."""
    with Path(__file__).with_name("calls.json").open("rb") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _mutators() -> frozenset[str]:
    return frozenset(calls_table()["mutating_methods"])


def analyze(source: str) -> tuple[set[str], set[str]]:
    """Return (defs, refs) for one cell, private names left out. Raises SyntaxError."""
    defs, refs = names(source)
    public = lambda found: {n for n in found if not n.startswith("_")}
    defs = public(defs)
    return defs, public(refs) - defs


def names(source: str) -> tuple[set[str], set[str]]:
    """(defs, refs) for one cell, private names included: they still share the kernel's namespace."""
    source = strip_magics(source)
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

    return defs, refs - defs - _BUILTINS


def imports(source: str) -> dict[str, str]:
    """Names bound only by imports at a cell's top level, mapped to what they import.

    `import a.b as c` -> {"c": "a.b"}, `from a import b` -> {"b": "a.b"}, `import a.b` -> {"a": "a"}.
    A name that is also assigned, or imported two ways, is left out.
    """
    tree = ast.parse(source)
    bound: dict[str, set[str]] = {}

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _SCOPES):
                continue
            if isinstance(child, ast.Import):
                for alias in child.names:
                    name = alias.asname or alias.name.partition(".")[0]
                    bound.setdefault(name, set()).add(alias.name if alias.asname else name)
            elif isinstance(child, ast.ImportFrom):
                module = "." * child.level + (child.module or "")
                for alias in child.names:
                    bound.setdefault(alias.asname or alias.name, set()).add(f"{module}.{alias.name}")
            visit(child)

    visit(tree)
    assigned, _ = _module_stores(tree)
    return {n: next(iter(ps)) for n, ps in bound.items() if len(ps) == 1 and n not in assigned}


def _base_name(node: ast.AST) -> str | None:
    """`a.b[0].c` -> "a"."""
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def mutations(source: str) -> set[str]:
    """Names a cell changes in place at top level: `x.append(1)`, `x[0] = 1`, `x.a = 1`, `del x[0]`.

    Code inside functions is ignored, because it only runs when called.
    """
    found: set[str] = set()

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _SCOPES):
                continue
            targets = []
            if isinstance(child, ast.Assign):
                targets = child.targets
            elif isinstance(child, (ast.AugAssign, ast.AnnAssign)):
                targets = [child.target]
            elif isinstance(child, ast.Delete):
                targets = child.targets
            for target in targets:
                if isinstance(target, (ast.Attribute, ast.Subscript)):
                    found.add(_base_name(target))
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr in _mutators()
            ):
                found.add(_base_name(child.func.value))
            visit(child)

    visit(ast.parse(source))
    return {n for n in found if n and not n.startswith("_")}


@dataclass
class Graph:
    order: list[str]
    defs: dict[str, set[str]] = field(default_factory=dict)
    refs: dict[str, set[str]] = field(default_factory=dict)
    mutates: dict[str, set[str]] = field(default_factory=dict)
    owner: dict[str, str] = field(default_factory=dict)
    parents: dict[str, set[str]] = field(default_factory=dict)
    children: dict[str, set[str]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def _reach(self, roots, edges) -> set[str]:
        seen = set(roots)
        stack = list(roots)
        while stack:
            for nxt in edges[stack.pop()]:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return seen

    def descendants(self, roots: list[str]) -> set[str]:
        return self._reach(roots, self.children)

    def ancestors(self, roots: list[str]) -> set[str]:
        return self._reach(roots, self.parents)

    def sort(self, cells: set[str]) -> list[str]:
        """Topological order of a subset, ties broken by display order.

        Cells stuck in a cycle are appended last (they carry an error anyway).
        """
        position = {c: i for i, c in enumerate(self.order)}
        indegree = {c: len(self.parents[c] & cells) for c in cells}
        result: list[str] = []
        ready = [position[c] for c in self.order if c in cells and indegree[c] == 0]
        while ready:
            cell = self.order[heapq.heappop(ready)]
            result.append(cell)
            for child in self.children[cell]:
                if child in cells:
                    indegree[child] -= 1
                    if indegree[child] == 0:
                        heapq.heappush(ready, position[child])
        placed = set(result)
        return result + [c for c in self.order if c in cells and c not in placed]

    def run_order(self, roots: list[str]) -> list[str]:
        """Roots plus everything downstream, parents before children."""
        return self.sort(self.descendants(roots))

    def expand_roots(self, roots: list[str]) -> list[str]:
        """A cell that mutates `x` must start from a fresh `x`, so its owner runs too."""
        extra = {self.owner[n] for r in roots for n in self.mutates.get(r, ()) if n in self.owner}
        return list(dict.fromkeys([*roots, *sorted(extra, key=self.order.index)]))


@dataclass(frozen=True)
class Analysis:
    """One cell's analysis, memoised by source so a rebuild after an edit re-analyses only
    the edited cell. Frozen, so `build` hands out fresh sets."""

    defs: frozenset[str] = frozenset()
    refs: frozenset[str] = frozenset()
    mutates: frozenset[str] = frozenset()
    imports: tuple[tuple[str, str], ...] = ()
    error: str = ""


@lru_cache(maxsize=4096)
def _analyzed(source: str) -> Analysis:
    try:
        defs, refs = analyze(source)
        stripped = strip_magics(source)
        return Analysis(
            frozenset(defs),
            frozenset(refs),
            frozenset(mutations(stripped) & refs),
            tuple(sorted(imports(stripped).items())),
        )
    except SyntaxError as e:
        return Analysis(error=f"SyntaxError: {e.msg} (line {e.lineno})")


def _same_import(name: str, cids: list[str], imported: dict[str, dict[str, str]]) -> bool:
    """True when every cell binds `name` through the identical import, like a repeated
    `import seaborn as sns`: a harmless repetition, not a conflict."""
    targets = {imported[cid].get(name) for cid in cids}
    return len(targets) == 1 and None not in targets


def build(cells: dict[str, str]) -> Graph:
    """Build the graph for cells given as {cell_id: source} in display order."""
    g = Graph(order=list(cells))
    imported: dict[str, dict[str, str]] = {}
    for cid, source in cells.items():
        a = _analyzed(source)
        g.defs[cid], g.refs[cid], g.mutates[cid] = set(a.defs), set(a.refs), set(a.mutates)
        imported[cid] = dict(a.imports)
        if a.error:
            g.errors[cid] = a.error

    owners: dict[str, list[str]] = {}
    for cid in g.order:
        for name in g.defs[cid]:
            owners.setdefault(name, []).append(cid)
    for name, cids in owners.items():
        g.owner[name] = cids[0]
        if len(cids) > 1 and _same_import(name, cids, imported):
            owners[name] = cids[:1]
        elif len(cids) > 1:
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

    for cid in g.order:
        for name in g.mutates[cid]:
            for reader in g.order[g.order.index(cid) + 1 :]:
                if (
                    name in g.refs[reader]
                    and reader != g.owner.get(name)
                    and reader not in g.children[cid]
                    and cid not in g.descendants([reader])
                ):
                    g.parents[reader].add(cid)
                    g.children[cid].add(reader)

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
