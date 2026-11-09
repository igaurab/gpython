"""Which cells may run at the same time.

Each cell gets an Access: the names it reads, the names it writes (rebinds, in-place changes, and
effect names such as `$fs` or `$random` for state outside the namespace) and, when it cannot be
analysed safely, the reason it must run alone. Two cells conflict when Bernstein's conditions fail.
Every doubt counts as a write: a wrong "dependent" costs speed, a wrong "independent" is a race.
What calls do lives in calls.json; the design is in docs/plans/14-concurrent-cells.md.
"""

from __future__ import annotations

import ast
import builtins
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from functools import lru_cache

from .graph import calls_table, names, strip_magics

_BUILTINS = frozenset(dir(builtins))
_DEFERRED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
_WRITE_MODES = set("wax+")


@dataclass(frozen=True)
class Access:
    reads: frozenset[str]
    writes: frozenset[str]
    exclusive: str


def conflict(a: Access, b: Access) -> bool:
    """Bernstein's conditions: no write of one is read or written by the other; exclusive cells conflict with all."""
    return bool(a.exclusive or b.exclusive or a.reads & b.writes or a.writes & b.reads or a.writes & b.writes)


@dataclass(frozen=True)
class Call:
    path: tuple[str, ...]
    method: str
    args: frozenset[str]
    out: frozenset[str]
    callee_names: frozenset[str]
    mode: str | None


@dataclass
class Scope:
    mutates: set[str] = field(default_factory=set)
    aliases: list[tuple[str, str]] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    stores: set[str] = field(default_factory=set)
    loads: set[str] = field(default_factory=set)
    declared: set[str] = field(default_factory=set)
    exclusive: str = ""


@dataclass(frozen=True)
class Function:
    params: frozenset[str]
    body: Scope


@dataclass(frozen=True)
class CellFacts:
    defs: frozenset[str]
    refs: frozenset[str]
    top: Scope
    imports: tuple[tuple[str, str], ...]
    functions: tuple[tuple[str, Function], ...]
    exclusive: str


@dataclass
class Summary:
    reads: set[str] = field(default_factory=set)
    writes: set[str] = field(default_factory=set)
    mutates_params: bool = False
    exclusive: str = ""

    def key(self) -> tuple[frozenset[str], frozenset[str], bool, str]:
        return frozenset(self.reads), frozenset(self.writes), self.mutates_params, self.exclusive


@dataclass
class Context:
    modules: dict[str, str]
    functions: dict[str, Function]
    summaries: dict[str, Summary]


@dataclass
class Effects:
    mutated: set[str] = field(default_factory=set)
    reads: set[str] = field(default_factory=set)
    writes: set[str] = field(default_factory=set)
    exclusive: str = ""


def _names_in(node: ast.AST | None) -> set[str]:
    if node is None:
        return set()
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _sources(node: ast.AST | None) -> set[str]:
    """Names whose object may be (or be inside) the value of `node`: `a[1:]` and `a.view()` share
    `a`, while `len(a)`, `open(p)`, an f-string or a comparison make a fresh object."""
    if node is None:
        return set()
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Attribute, ast.Subscript, ast.Starred, ast.Await)):
        return _sources(node.value)
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id in _rules().fresh_builtins:
            return set()
        found = set().union(*(_sources(a) for a in node.args), *(_sources(k.value) for k in node.keywords))
        return found | (_sources(func.value) if isinstance(func, ast.Attribute) else set())
    if isinstance(node, ast.NamedExpr):
        return _sources(node.value) | {node.target.id}
    if isinstance(node, ast.BoolOp):
        return set().union(*(_sources(v) for v in node.values))
    if isinstance(node, ast.BinOp):
        return _sources(node.left) | _sources(node.right)
    if isinstance(node, ast.IfExp):
        return _sources(node.body) | _sources(node.orelse)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return set().union(*(_sources(e) for e in node.elts))
    if isinstance(node, ast.Dict):
        return set().union(*(_sources(e) for e in (*node.keys, *node.values)))
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
        return set().union(*(_sources(g.iter) for g in node.generators))
    return set()


@dataclass(frozen=True)
class _Rules:
    fresh_builtins: frozenset[str]
    readonly_builtins: frozenset[str]
    exclusive_builtins: frozenset[str]
    readonly_methods: frozenset[str]
    receiver_only_methods: frozenset[str]
    write_methods: dict[str, str]
    read_methods: dict[str, str]


@lru_cache(maxsize=1)
def _rules() -> _Rules:
    table = calls_table()
    effects = table["effects"]
    return _Rules(
        frozenset(table["fresh_builtins"]), frozenset(table["readonly_builtins"]),
        frozenset(table["exclusive_builtins"]), frozenset(table["readonly_methods"]),
        frozenset(table["receiver_only_methods"]), effects["write_methods"], effects["read_methods"],
    )


@lru_cache(maxsize=4096)
def _module_call(dotted: str) -> tuple[bool, frozenset[str], frozenset[str]]:
    """(only reads its arguments, effect names written, effect names read) of `numpy.random.seed` and the like."""
    table = calls_table()
    effects = table["effects"]
    readonly = any(fnmatchcase(dotted, p) for p in table["readonly_functions"])
    writes = frozenset(name for pattern, name in effects["write"].items() if fnmatchcase(dotted, pattern))
    reads = frozenset(name for pattern, name in effects["read"].items() if fnmatchcase(dotted, pattern))
    return readonly, writes, reads


def _base(node: ast.AST) -> str | None:
    while isinstance(node, (ast.Attribute, ast.Subscript, ast.Starred)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _path(node: ast.AST) -> tuple[str, ...]:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return ()
    return (node.id, *reversed(parts))


def _target_names(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        return set().union(*(_target_names(t) for t in target.elts))
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    return set()


def _open_mode(call: ast.Call) -> str | None:
    mode = call.args[1] if len(call.args) > 1 else next((k.value for k in call.keywords if k.arg == "mode"), None)
    if mode is None:
        return "r"
    return mode.value if isinstance(mode, ast.Constant) and isinstance(mode.value, str) else "?"


class _Collector(ast.NodeVisitor):
    """Gathers one block's facts; function bodies are left for later, class bodies run now."""

    def __init__(self, top: bool) -> None:
        self.scope = Scope()
        self.top = top
        self.functions: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
        self.imports: list[tuple[str, str]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.functions.append((node.name, node))
        self.scope.stores.add(node.name)
        for part in (*node.decorator_list, *node.args.defaults, *node.args.kw_defaults):
            if part is not None:
                self.visit(part)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.scope.loads |= _names_in(node.body) - {a.arg for a in node.args.args}

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.stores.add(node.name)
        for part in (*node.decorator_list, *node.bases, *node.keywords):
            self.visit(part)
        for statement in node.body:
            if not isinstance(statement, _DEFERRED):
                self.visit(statement)

    def visit_Name(self, node: ast.Name) -> None:
        (self.scope.loads if isinstance(node.ctx, ast.Load) else self.scope.stores).add(node.id)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.asname:
                self.imports.append((alias.asname, alias.name))
            else:
                root = alias.name.split(".")[0]
                self.imports.append((root, root))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == "*":
                self.scope.exclusive = self.scope.exclusive or f"from {node.module} import *"
            else:
                self.imports.append((alias.asname or alias.name, f"{node.module}.{alias.name}"))

    def visit_Global(self, node: ast.Global) -> None:
        self.scope.declared.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.scope.declared.update(node.names)

    def _changed(self, target: ast.AST) -> None:
        if isinstance(target, (ast.Attribute, ast.Subscript)):
            base = _base(target)
            if base:
                self.scope.mutates.add(base)

    def _alias(self, targets: set[str], value: ast.AST | None) -> None:
        for name in targets:
            for source in _sources(value):
                self.scope.aliases.append((name, source))

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._changed(target)
            self._alias(_target_names(target), node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._changed(node.target)
        self._alias(_target_names(node.target), node.value)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._changed(node.target)
        if isinstance(node.target, ast.Name):
            self.scope.mutates.add(node.target.id)
        self.generic_visit(node)

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            self._changed(target)
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self._alias({node.target.id}, node.value)
        self.generic_visit(node)

    def visit_For(self, node: ast.For | ast.AsyncFor) -> None:
        if isinstance(node, ast.AsyncFor):
            self._top_await()
        self._alias(_target_names(node.target), node.iter)
        self.generic_visit(node)

    visit_AsyncFor = visit_For

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self._alias(_target_names(node.target), node.iter)
        self.generic_visit(node)

    def visit_With(self, node: ast.With | ast.AsyncWith) -> None:
        if isinstance(node, ast.AsyncWith):
            self._top_await()
        for item in node.items:
            if item.optional_vars is not None:
                self._alias(_target_names(item.optional_vars), item.context_expr)
        self.generic_visit(node)

    visit_AsyncWith = visit_With

    def visit_Await(self, node: ast.Await) -> None:
        self._top_await()
        self.generic_visit(node)

    def _top_await(self) -> None:
        if self.top:
            self.scope.exclusive = self.scope.exclusive or "top-level await"

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        args = set().union(*(_sources(a) for a in node.args), *(_sources(k.value) for k in node.keywords))
        out = set().union(*(_sources(k.value) for k in node.keywords if k.arg == "out"))
        path = _path(func)
        self.scope.calls.append(Call(
            path=path,
            method=func.attr if isinstance(func, ast.Attribute) else "",
            args=frozenset(args),
            out=frozenset(out),
            callee_names=frozenset() if path else frozenset(_sources(func.value if isinstance(func, ast.Attribute) else func)),
            mode=_open_mode(node) if path == ("open",) else None,
        ))
        self.generic_visit(node)


def _collect(nodes: list[ast.stmt], top: bool) -> _Collector:
    collector = _Collector(top)
    for node in nodes:
        collector.visit(node)
    return collector


def _function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> Function:
    args = node.args
    params = {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
    params |= {a.arg for a in (args.vararg, args.kwarg) if a is not None}
    body = _collect(node.body, top=False)
    for _, inner in body.functions:
        body.scope.loads |= _names_in(inner)
    return Function(frozenset(params), body.scope)


@lru_cache(maxsize=4096)
def facts(source: str) -> CellFacts:
    """What one cell does, before the rest of the notebook is known; memoised by source."""
    stripped = strip_magics(source)
    try:
        defs, refs = names(source)
        tree = ast.parse(stripped)
    except SyntaxError as e:
        return CellFacts(frozenset(), frozenset(), Scope(), (), (), f"syntax error: {e.msg}")
    collector = _collect(tree.body, top=True)
    magic = stripped.splitlines() != source.splitlines()
    exclusive = "IPython magic or shell line" if magic else collector.scope.exclusive
    functions = tuple((name, _function(node)) for name, node in collector.functions)
    return CellFacts(frozenset(defs), frozenset(refs), collector.scope, tuple(collector.imports), functions, exclusive)


def _call(call: Call, ctx: Context) -> Effects:
    """What one call does to names and outside state."""
    rules = _rules()
    args = call.args - ctx.modules.keys()
    found = Effects(mutated=set(call.out - ctx.modules.keys()))
    if call.method:
        if call.method in rules.write_methods:
            found.writes.add(rules.write_methods[call.method])
        if call.method in rules.read_methods:
            found.reads.add(rules.read_methods[call.method])
    readonly_method = call.method in rules.readonly_methods
    keeps_args = call.method not in rules.receiver_only_methods
    if not call.path:
        (found.reads if readonly_method or not keeps_args else found.mutated).update(args)
        for name in call.callee_names - ctx.modules.keys():
            if readonly_method:
                found.reads.add(name)
                continue
            if name in ctx.summaries:
                found.mutated |= ctx.summaries[name].reads
            elif name not in _BUILTINS:
                found.mutated.add(name)
        return found
    root = call.path[0]
    if root in ctx.modules and root not in ctx.functions:
        readonly, writes, reads = _module_call(".".join((ctx.modules[root], *call.path[1:])))
        found.writes |= writes
        found.reads |= reads
        (found.reads if readonly else found.mutated).update(args)
        return found
    if len(call.path) == 1:
        if root in ctx.summaries:
            summary = ctx.summaries[root]
            found.reads |= summary.reads | {root}
            found.writes |= summary.writes
            found.exclusive = summary.exclusive
            (found.mutated if summary.mutates_params else found.reads).update(args)
        elif root in _BUILTINS:
            if root in rules.exclusive_builtins:
                found.exclusive = f"calls {root}()"
            elif root == "open":
                found.reads |= args
                writes = call.mode is None or call.mode == "?" or bool(_WRITE_MODES & set(call.mode))
                (found.writes if writes else found.reads).add("$fs")
            elif root in rules.readonly_builtins:
                found.reads |= args
            else:
                found.mutated |= args
        else:
            found.mutated |= args | {root}
        return found
    found.reads.add(root)
    if readonly_method:
        found.reads |= args
    else:
        found.mutated.add(root)
        (found.mutated if keeps_args else found.reads).update(args)
    return found


class _Aliases:
    """Union-find over names that may share an object: changing one in place changes them all."""

    def __init__(self) -> None:
        self.parent: dict[str, str] = {}
        self.members: dict[str, set[str]] | None = None

    def find(self, name: str) -> str:
        while self.parent.get(name, name) != name:
            self.parent[name] = self.parent.get(self.parent[name], self.parent[name])
            name = self.parent[name]
        return name

    def join(self, a: str, b: str) -> None:
        self.parent[self.find(a)] = self.find(b)
        self.members = None

    def closure(self, found: set[str]) -> set[str]:
        if self.members is None:
            self.members = {}
            for name in self.parent:
                self.members.setdefault(self.find(name), {self.find(name)}).add(name)
        result = set(found)
        for name in found:
            result |= self.members.get(self.find(name), set())
        return result


def _scope_effects(scope: Scope, ctx: Context) -> Effects:
    total = Effects(mutated=set(scope.mutates), exclusive=scope.exclusive)
    for call in scope.calls:
        one = _call(call, ctx)
        total.mutated |= one.mutated
        total.reads |= one.reads
        total.writes |= one.writes
        total.exclusive = total.exclusive or one.exclusive
    return total


def _summary(function: Function, ctx: Context) -> Summary:
    body = function.body
    local = (body.stores | function.params) - body.declared
    found = _scope_effects(body, ctx)
    aliases = _Aliases()
    for a, b in body.aliases:
        if b not in ctx.modules and b not in _BUILTINS:
            aliases.join(a, b)
    changed = aliases.closure(found.mutated)
    summary = Summary(exclusive=found.exclusive)
    summary.mutates_params = bool(changed & function.params)
    summary.writes = (changed - local) | found.writes | (body.declared & body.stores)
    summary.reads = ((body.loads | found.reads) - local) - _BUILTINS
    return summary


def _summaries(ctx: Context) -> dict[str, Summary]:
    ctx.summaries = {name: Summary() for name in ctx.functions}
    for _ in range(len(ctx.functions) + 1):
        fresh = {name: _summary(f, ctx) for name, f in ctx.functions.items()}
        if all(fresh[n].key() == ctx.summaries[n].key() for n in fresh):
            break
        ctx.summaries = fresh
    return ctx.summaries


def accesses(cells: dict[str, str]) -> dict[str, Access]:
    """Access of every cell of a notebook given as {cell id: source} in display order."""
    every = {cid: facts(source) for cid, source in cells.items()}
    ctx = Context(modules={}, functions={}, summaries={})
    for found in every.values():
        ctx.modules.update(found.imports)
        ctx.functions.update(found.functions)
    _summaries(ctx)
    aliases = _Aliases()
    for found in every.values():
        for a, b in found.top.aliases:
            if b not in ctx.modules and b not in ctx.functions and b not in _BUILTINS:
                aliases.join(a, b)
    result = {}
    for cid, found in every.items():
        effects = _scope_effects(found.top, ctx)
        writes = set(found.defs) | aliases.closure(effects.mutated) | effects.writes
        reads = set(found.refs) | effects.reads | (found.top.loads - _BUILTINS)
        exclusive = found.exclusive or effects.exclusive
        result[cid] = Access(frozenset(reads), frozenset(writes), exclusive)
    return result
