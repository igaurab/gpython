"""The notebook as tools for a coding agent: read cells, change them, run them, look at values.

`SPECS` is what an agent sees (name, description, JSON schema), in the shape the Model Context
Protocol uses for `tools/list`. `NotebookTools` does the work inside the running app, on the
app's own event loop, through the same pane methods the keys use, so a change made by an
agent is on the undo timeline, marks cells out of date and shows on screen like a change made
by hand. The MCP server (mcpserver.py) imports only `SPECS`; nothing here imports textual.

A tool answers with MCP content blocks: text, and PNG pictures for cells that drew a plot.
A cell is named by the number in its header (c3 is 3); the numbers change when a cell is
added or removed, and every answer that changes them says so.
"""

from __future__ import annotations

import asyncio
import base64
import re
from pathlib import Path

OUTPUT_LIMIT = 6000
VALUE_LIMIT = 12000
IMAGES_PER_ANSWER = 4
RUN_TIMEOUT = 120

NOTEBOOK = {"type": "string", "description": "Path or file name of an open notebook. Leave out for the notebook in the active window."}
CELL = {"type": "integer", "description": "Cell number as in its header: 3 is c3."}
CELLS = {"type": "string", "description": 'Cell numbers: "3", "1,4-6", or "all". Leave out for all cells.'}

INSTRUCTIONS = (
    "These tools work on the notebook the user has open in gpython, a terminal notebook, and on its live "
    "Python kernel. The user sees every change at once. Read cells with cells_read before you change them. "
    "Change the open notebook only with these tools: a change written to its file on disk does not reach "
    "the open window. Cell numbers change after cell_insert and cell_delete. Variables stay in the kernel "
    "between runs, so run only the cells you need. Use python_eval to look at values without adding a cell."
)


def _tool(name: str, description: str, properties: dict, required: tuple[str, ...] = ()) -> dict:
    return {
        "name": name, "description": description,
        "inputSchema": {"type": "object", "properties": {**properties, "notebook": NOTEBOOK}, "required": list(required)},
    }


SPECS = [
    _tool("notebooks", "List the notebooks open in gpython: path, number of cells, which one is active and the cell the cursor is in.", {}),
    _tool(
        "cells_read",
        "Read cells: number, kind (code or markdown), run state, source and the output of the last run. "
        "A cell that failed shows its traceback. Plots come back as pictures.",
        {"cells": CELLS, "outputs": {"type": "boolean", "description": "Include outputs (default true)."}},
    ),
    _tool(
        "cell_edit",
        "Change the source of one cell. Give `source` for the whole new text, or `old` and `new` to replace one "
        "exact piece of text that occurs once in the cell. The cell becomes out of date until it runs again.",
        {"cell": CELL, "source": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}},
        ("cell",),
    ),
    _tool(
        "cell_insert",
        "Add a new cell after cell number `after` (0 puts it first; leave out to add it at the end). "
        "Answers with the new cell's number. Set `run` to run it at once.",
        {
            "source": {"type": "string"}, "after": {"type": "integer"},
            "kind": {"type": "string", "enum": ["code", "markdown"]}, "run": {"type": "boolean"},
        },
        ("source",),
    ),
    _tool("cell_delete", "Remove one cell. Its variables leave the kernel and the cells that used them become out of date.", {"cell": CELL}, ("cell",)),
    _tool(
        "cells_run",
        'Run cells and wait for them: "3", "1,4-6", "all", or "stale" for the cells that are out of date. '
        "Cells they depend on that are out of date run first. Answers with each cell's state and output.",
        {
            "cells": {"type": "string", "description": 'Cell numbers, "all" or "stale".'},
            "timeout": {"type": "number", "description": f"Seconds to wait (default {RUN_TIMEOUT}). The run goes on after that; read the cells later."},
        },
        ("cells",),
    ),
    _tool(
        "variables",
        "The variables the cells defined, as they are in the kernel now: name, type, shape or length, a short value and the cell that set it.",
        {"filter": {"type": "string", "description": "Only names or types that contain this text."}},
    ),
    _tool("variable_value", "The full value of one variable: text, or a table for a DataFrame or 2-D array (size limited).", {"name": {"type": "string"}}, ("name",)),
    _tool(
        "python_eval",
        "Run Python code in the notebook's kernel without making a cell, and return what it prints and its last "
        "value. Use it to inspect state while debugging. It runs in the same namespace as the cells, so an "
        "assignment here changes the notebook's variables.",
        {"code": {"type": "string"}, "timeout": {"type": "number"}},
        ("code",),
    ),
    _tool("kernel", "Interrupt the running cell, or restart the kernel (all variables are lost).", {"action": {"type": "string", "enum": ["interrupt", "restart"]}}, ("action",)),
    _tool("notebook_save", "Save the notebook to its file.", {}),
]

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


class ToolError(Exception):
    """The request cannot be done as asked; the message goes to the agent."""


def plain(text: str) -> str:
    return _ANSI.sub("", text)


def clipped(text: str, limit: int = OUTPUT_LIMIT) -> str:
    """`text`, with its middle left out when it is longer than `limit`: errors are at the end, headers at the start."""
    if len(text) <= limit:
        return text
    head, tail = limit // 3, limit - limit // 3
    return f"{text[:head]}\n… {len(text) - limit} characters left out …\n{text[-tail:]}"


def parse_cells(text, count: int) -> list[int]:
    """Zero-based positions for "3", "1,4-6" or "all" in a notebook of `count` cells."""
    if text is None or str(text).strip().lower() in ("", "all", "%"):
        return list(range(count))
    found: list[int] = []
    for part in str(text).replace("c", "").replace(" ", "").split(","):
        first, dash, last = part.partition("-")
        if not first.isdigit() or (dash and not last.isdigit()):
            raise ToolError(f'Cannot read "{text}" as cell numbers. Examples: "3", "1,4-6", "all"')
        low, high = int(first), int(last) if dash else int(first)
        if not 1 <= low <= high <= count:
            raise ToolError(f"No cell c{high if high > count or low <= high else low}: the notebook has cells c1 to c{count}")
        found.extend(n - 1 for n in range(low, high + 1) if n - 1 not in found)
    return found


def text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def image_block(png: bytes) -> dict:
    return {"type": "image", "data": base64.b64encode(png).decode(), "mimeType": "image/png"}


class NotebookTools:
    def __init__(self, app) -> None:
        self.app = app

    async def call(self, name: str, arguments: dict) -> dict:
        """Run one tool. Always answers in MCP's shape; a refusal is an answer with isError."""
        handler = getattr(self, f"tool_{name}", None) if name in {spec["name"] for spec in SPECS} else None
        if handler is None:
            return {"content": [text_block(f"Unknown tool: {name}")], "isError": True}
        try:
            content = await handler(dict(arguments or {}))
        except ToolError as error:
            return {"content": [text_block(str(error))], "isError": True}
        except Exception as error:
            return {"content": [text_block(f"gpython could not do this: {type(error).__name__}: {error}")], "isError": True}
        return {"content": content if isinstance(content, list) else [text_block(content)], "isError": False}

    def _panes(self) -> list:
        return self.app.workspace.notebooks()

    def _pane(self, arguments: dict):
        wanted = arguments.get("notebook")
        if not wanted:
            return self.app.pane
        target = Path(str(wanted)).expanduser()
        for pane in self._panes():
            if pane.path is not None and (pane.path.resolve() == target.resolve() or pane.path.name == target.name):
                return pane
        names = ", ".join(pane.name for pane in self._panes())
        raise ToolError(f"No open notebook named {wanted}. Open notebooks: {names}")

    def _state(self, pane, arguments: dict, key: str = "cell"):
        number = arguments.get(key)
        if isinstance(number, str) and number.lstrip("c").isdigit():
            number = int(number.lstrip("c"))
        if not isinstance(number, int) or isinstance(number, bool):
            raise ToolError(f"`{key}` must be a cell number, for example 3 for c3")
        if not 1 <= number <= len(pane.states):
            raise ToolError(f"No cell c{number}: the notebook has cells c1 to c{len(pane.states)}")
        return pane.states[number - 1]

    def _report(self, state, outputs: bool = True) -> str:
        from .pane import needs_run

        status = "out of date" if state.kind == "code" and state.status in ("idle", "ok", "stale") and needs_run(state) and state.exec_count else state.status
        if state.kind == "code" and not state.exec_count and status == "idle":
            status = "not run"
        took = f" {state.duration:.2f}s" if state.kind == "code" and state.duration is not None and status in ("ok", "error") else ""
        head = f"== {state.label} [{state.kind}] {status}{took}" if state.kind == "code" else f"== {state.label} [markdown]"
        parts = [head, state.source]
        output = plain(state.output_text).rstrip() if outputs and state.kind == "code" else ""
        if output:
            parts += ["-- output", clipped(output)]
        return "\n".join(parts)

    def _answer(self, states: list, outputs: bool = True, lead: str = "") -> list[dict]:
        text = "\n\n".join(self._report(state, outputs) for state in states)
        blocks = [text_block(f"{lead}\n\n{text}".strip() if lead else text)]
        if outputs:
            pictures = [(state, png) for state in states if state.kind == "code" for png in state.images]
            for state, png in pictures[:IMAGES_PER_ANSWER]:
                blocks += [text_block(f"picture from {state.label}:"), image_block(png)]
            if len(pictures) > IMAGES_PER_ANSWER:
                blocks.append(text_block(f"{len(pictures) - IMAGES_PER_ANSWER} more pictures not sent; read fewer cells to get them"))
        return blocks

    async def tool_notebooks(self, arguments: dict) -> str:
        active = self.app.pane
        lines = []
        for pane in self._panes():
            here = pane.buffer is active.buffer
            current = pane.current_cell() if here else None
            cursor = f", cursor in {current.state.label}" if current is not None else ""
            unsaved = ", not saved" if pane.dirty else ""
            lines.append(f"{'* ' if here else '  '}{pane.path or '(untitled)'}: {len(pane.states)} cells{cursor}{unsaved}")
        return "\n".join(lines) + "\n(* is the active notebook)"

    async def tool_cells_read(self, arguments: dict) -> list[dict]:
        pane = self._pane(arguments)
        states = [pane.states[i] for i in parse_cells(arguments.get("cells"), len(pane.states))]
        lead = f"{pane.path or '(untitled notebook)'}: {len(pane.states)} cells"
        return self._answer(states, arguments.get("outputs", True) is not False, lead)

    async def tool_cell_edit(self, arguments: dict) -> str:
        pane = self._pane(arguments)
        state = self._state(pane, arguments)
        source = arguments.get("source")
        if source is None:
            old, new = arguments.get("old"), arguments.get("new")
            if not isinstance(old, str) or not isinstance(new, str) or not old:
                raise ToolError("Give `source` (the whole new text), or `old` and `new`")
            found = state.source.count(old)
            if found != 1:
                raise ToolError(f"`old` must occur one time in {state.label}; it occurs {found} times. Read the cell again or give `source`")
            source = state.source.replace(old, new)
        if source == state.source:
            return f"{state.label} already has this text"
        await self._set_source(pane, state, str(source))
        return f"{state.label} changed ({len(str(source).splitlines())} lines). It has not run yet"

    async def _set_source(self, pane, state, text: str) -> None:
        """New text for a cell as one undo step when its editor is on screen; a cell off screen
        takes the text as an ex command would (exlines.py)."""
        widget = pane.widget_of(state)
        editor = widget._editor if widget is not None else None
        if editor is None:
            state.replace_source(text)
            pane.source_changed()
            return
        editor.history.checkpoint()
        editor.replace(text, (0, 0), editor.document.end, maintain_selection_offset=False)
        editor.history.checkpoint()
        if state.rendered:
            await widget.show_rendered()

    async def tool_cell_insert(self, arguments: dict) -> list[dict] | str:
        from .cell import CellSnapshot

        pane = self._pane(arguments)
        states = pane.states
        after = arguments.get("after")
        if after is None:
            after = len(states)
        if not isinstance(after, int) or not 0 <= after <= len(states):
            raise ToolError(f"`after` must be from 0 to {len(states)}")
        kind = arguments.get("kind") or "code"
        if kind not in ("code", "markdown"):
            raise ToolError("`kind` must be code or markdown")
        snapshot = CellSnapshot(after, str(arguments.get("source") or ""), kind, False, [])
        anchor = states[after - 1] if after else (states[0] if states else None)
        with self._focus_kept():
            widget = await pane._insert(anchor, above=after == 0 and bool(states), snapshot=snapshot)
        state = widget.state
        if arguments.get("run") and kind == "code":
            return await self._run(pane, [state], RUN_TIMEOUT, f"Added {state.label}.")
        return f"Added {state.label}. The notebook has {len(pane.states)} cells now"

    async def tool_cell_delete(self, arguments: dict) -> str:
        pane = self._pane(arguments)
        state = self._state(pane, arguments)
        label = state.label
        with self._focus_kept():
            await pane._remove_cells([state])
        return f"Removed {label}. The notebook has {len(pane.states)} cells now; the cells after it moved up one number"

    def _focus_kept(self):
        return _FocusKept(self.app)

    async def tool_cells_run(self, arguments: dict) -> list[dict]:
        from .pane import needs_run

        pane = self._pane(arguments)
        wanted = str(arguments.get("cells") or "").strip().lower()
        if wanted == "stale":
            states = [s for s in pane.states if s.kind == "code" and needs_run(s)]
            if not states:
                return [text_block("No cell is out of date")]
        else:
            states = [pane.states[i] for i in parse_cells(wanted, len(pane.states))]
        states = [s for s in states if s.kind == "code" and s.source.strip()]
        if not states:
            raise ToolError("These cells have no code to run")
        return await self._run(pane, states, _seconds(arguments.get("timeout"), RUN_TIMEOUT))

    async def _run(self, pane, states: list, timeout: float, lead: str = "") -> list[dict]:
        before = {s.cid: s.exec_count for s in pane.states}
        run = asyncio.ensure_future(pane.run_cells([s.cid for s in states]))
        done, _ = await asyncio.wait({run}, timeout=timeout)
        if done and run.exception() is not None:
            raise run.exception()
        ran = [s for s in pane.states if s in states or s.exec_count != before.get(s.cid)]
        if not done:
            lead = f"{lead} Still running after {timeout:g} s; the run goes on. Read the cells later, or use the kernel tool to interrupt.".strip()
        failed = [s.label for s in ran if s.status == "error"]
        if failed:
            lead = f"{lead} Failed: {', '.join(failed)}.".strip()
        return self._answer(ran, True, lead)

    async def tool_variables(self, arguments: dict) -> str:
        pane = self._pane(arguments)
        wanted = str(arguments.get("filter") or "").lower()
        rows = [row for row in pane._variable_rows() if not wanted or wanted in row[0].lower() or wanted in row[1].lower()]
        if not rows:
            return "No variables yet: run a cell first" if not wanted else f"No variable matches {wanted}"
        lines = [f"{name}: {kind}{f' [{info}]' if info else ''} = {clipped(str(shown), 200)}{f'  (from {cell})' if cell else ''}" for name, kind, shown, info, _, cell in rows]
        return "\n".join(lines)

    async def tool_variable_value(self, arguments: dict) -> str:
        pane = self._pane(arguments)
        name = str(arguments.get("name") or "")
        if not name.isidentifier():
            raise ToolError("`name` must be the name of one variable; use python_eval for an expression")
        value = await pane.kernel.full_value(name)
        if value.get("kind") == "table":
            import json

            return clipped(json.dumps({key: value[key] for key in value if key != "kind"}, default=str), VALUE_LIMIT)
        return clipped(str(value.get("text", "")), VALUE_LIMIT) or f"{name} has no value in the kernel"

    async def tool_python_eval(self, arguments: dict) -> str:
        pane = self._pane(arguments)
        code = str(arguments.get("code") or "")
        if not code.strip():
            raise ToolError("`code` is empty")
        parts: list[str] = []

        def collect(kind: str, content: dict) -> None:
            if kind == "stream":
                parts.append(content.get("text", ""))
            elif kind == "error":
                parts.append("\n".join(content.get("traceback", [])))
            elif kind in ("execute_result", "display_data"):
                text = content.get("data", {}).get("text/plain", "")
                parts.append((text if isinstance(text, str) else "".join(text)) + "\n")

        timeout = _seconds(arguments.get("timeout"), RUN_TIMEOUT)
        async with pane._run_lock:
            run = asyncio.ensure_future(pane.kernel.execute(code, collect, store_history=False))
            done, _ = await asyncio.wait({run}, timeout=timeout)
            if not done:
                await pane.kernel.interrupt()
                await asyncio.wait({run}, timeout=5)
                return clipped(plain("".join(parts))) + f"\nStopped: no answer after {timeout:g} s"
        return clipped(plain("".join(parts)).rstrip()) or "(no output)"

    async def tool_kernel(self, arguments: dict) -> str:
        pane = self._pane(arguments)
        action = arguments.get("action")
        if action == "interrupt":
            await pane.interrupt()
            return "Sent an interrupt to the kernel"
        if action == "restart":
            await pane.restart_kernel()
            return "The kernel restarted. All variables are gone; run the cells again"
        raise ToolError("`action` must be interrupt or restart")

    async def tool_notebook_save(self, arguments: dict) -> str:
        pane = self._pane(arguments)
        if pane.path is None:
            raise ToolError("This notebook has no file name yet. Ask the user to save it with :w NAME")
        pane.write_file()
        return f"Saved {pane.path}"


def _seconds(value, default: float) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else default


class _FocusKept:
    """Adding or removing a cell selects a cell; the focus goes back to where the user had it."""

    def __init__(self, app) -> None:
        self.app = app

    def __enter__(self) -> None:
        self.focused = self.app.focused

    def __exit__(self, *exc) -> None:
        focused = self.focused
        if focused is None:
            return

        def restore() -> None:
            if focused.is_attached and self.app.focused is not focused:
                focused.focus(scroll_visible=False)

        restore()
        self.app.call_after_refresh(restore)
