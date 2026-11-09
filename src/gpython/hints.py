"""Plain-language hints under a traceback for errors that have one well-known fix."""

from __future__ import annotations

import ast

NESTED_LOOP = "asyncio.run() cannot be called from a running event loop"


def event_loop(error: dict | None, source: str) -> str | None:
    """`asyncio.run()` inside the kernel, which already runs an event loop: say to await instead.

    When the cell itself calls `asyncio.run(x)`, the hint
    names `await x`; when a library calls it, it says to await that library's coroutine."""
    if not error or error.get("ename") != "RuntimeError" or NESTED_LOOP not in error.get("evalue", ""):
        return None
    lead = "the kernel already runs an event loop, so asyncio.run() cannot start another"
    if (awaited := _awaitable(source)) is not None:
        return f"{lead} · write await {awaited} instead"
    return f"{lead} · await the coroutine at the top level of the cell instead (the async version of what this cell calls)"


def _awaitable(source: str) -> str | None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_asyncio_run(node.func) and node.args:
            return ast.get_source_segment(source, node.args[0]) or ast.unparse(node.args[0])
    return None


def _is_asyncio_run(func: ast.expr) -> bool:
    if isinstance(func, ast.Attribute):
        return func.attr == "run" and isinstance(func.value, ast.Name) and func.value.id == "asyncio"
    return False
