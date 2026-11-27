"""The agent panel (:agent): a conversation with a coding agent beside the notebook.

The panel knows nothing about any one agent. It starts the command the registry names
(agents.py), speaks the Agent Client Protocol with it (acp.py) and gives it the notebook
tools through the agent link (agentlink.py), so Claude Code, Codex, OpenCode, Gemini and pi
all read, change and run the cells the same way.

The transcript is a column of entries, one Static each: what the user wrote, what the agent
answers (Markdown), its thoughts, its tool calls with their state, its plan and its
questions about permission. Text arrives in many small pieces; the entries that changed are
painted together a few times a second (`FLUSH_SECONDS`), never once per piece. The module is
imported on the first :agent, so a notebook that never opens the panel pays nothing.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from rich.console import Group
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Input, Static

from . import acp, agents

FLUSH_SECONDS = 0.08
DIFF_LINES = 12
TOOL_TEXT_LINES = 6
THOUGHT_LINES = 8
STATUS_MARKS = {"pending": "○", "in_progress": "◐", "completed": "●", "failed": "✗"}
STATUS_STYLES = {"pending": "dim", "in_progress": "bold", "completed": "green", "failed": "red"}
PLAN_MARKS = {"pending": "☐", "in_progress": "◐", "completed": "☑"}
STOP_NOTES = {
    "cancelled": "Stopped.",
    "max_tokens": "The agent stopped: it reached its limit of text for one turn.",
    "max_turn_requests": "The agent stopped: it reached its limit of steps for one turn.",
    "refusal": "The agent refused this request.",
}
AGENT_KEYS = "enter send · ctrl+c stop · esc notebook · pgup/pgdn scroll"
PERMISSION_KEYS = "press the number · esc refuse"


class Entry:
    """One block of the transcript and the widget that shows it."""

    def __init__(self, kind: str, text: str = "") -> None:
        self.kind = kind
        self.text = text
        self.data: dict = {}
        self.widget = Static(classes=f"agent-entry agent-{kind}")
        self.dirty = True

    def renderable(self):
        if self.kind == "agent":
            from rich.markdown import Markdown

            return Markdown(self.text)
        if self.kind == "user":
            return Text.assemble(("› ", "bold"), (self.text, "bold"))
        if self.kind == "thought":
            lines = self.text.strip().splitlines()
            shown = lines[-THOUGHT_LINES:]
            more = [f"… {len(lines) - len(shown)} lines before"] if len(lines) > len(shown) else []
            return Text("\n".join(more + shown), style="dim italic")
        if self.kind == "tool":
            return tool_picture(self.data)
        if self.kind == "plan":
            return plan_picture(self.data.get("entries") or [])
        if self.kind == "permission":
            return permission_picture(self.data)
        return Text(self.text, style="red" if self.kind == "error" else "dim")


def tool_picture(call: dict) -> Text:
    status = call.get("status") or "pending"
    title = " ".join(str(call.get("title") or call.get("name") or "tool").split())
    picture = Text.assemble((f"{STATUS_MARKS.get(status, '○')} ", STATUS_STYLES.get(status, "dim")), (title, "" if status != "failed" else "red"))
    for item in call.get("content") or []:
        detail = tool_detail(item, status == "failed") if isinstance(item, dict) else None
        if detail is not None:
            picture.append("\n")
            picture.append_text(detail)
    return picture


def tool_detail(item: dict, failed: bool) -> Text | None:
    """What a tool call shows under its title: the diff of an edit, and the first lines of the text of a call that failed."""
    if item.get("type") == "diff":
        return diff_picture(item)
    inner = item.get("content") or {}
    if failed and item.get("type") == "content" and inner.get("type") == "text" and inner.get("text"):
        lines = str(inner["text"]).strip().splitlines()[:TOOL_TEXT_LINES]
        return Text("\n".join(f"  {line}" for line in lines), style="dim")
    return None


def diff_picture(diff: dict) -> Text:
    """The changed lines of a file edit, a few of them, as - and + lines."""
    import difflib

    old = (diff.get("oldText") or "").splitlines()
    new = (diff.get("newText") or "").splitlines()
    picture = Text(f"  {Path(str(diff.get('path') or '')).name}", style="dim")
    changed = [line for line in difflib.unified_diff(old, new, lineterm="", n=0) if line[:1] in "+-" and line[:3] not in ("+++", "---")]
    for line in changed[:DIFF_LINES]:
        picture.append(f"\n  {line}", style="green" if line.startswith("+") else "red")
    if len(changed) > DIFF_LINES:
        picture.append(f"\n  … {len(changed) - DIFF_LINES} more lines", style="dim")
    return picture


def plan_picture(entries: list[dict]) -> Text:
    picture = Text("Plan", style="bold")
    for entry in entries:
        status = entry.get("status") or "pending"
        picture.append(f"\n{PLAN_MARKS.get(status, '☐')} {entry.get('content', '')}", style="dim" if status == "completed" else "")
    return picture


def permission_picture(data: dict) -> Group | Text:
    call = data.get("call") or {}
    answer = data.get("answer")
    title = " ".join(str(call.get("title") or "this action").split())
    if answer is not None:
        return Text.assemble(("● ", "dim"), (f"{answer}: ", "bold"), (title, "dim"))
    head = Text.assemble(("The agent asks: ", "bold yellow"), title)
    for item in call.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "diff":
            head.append("\n")
            head.append_text(diff_picture(item))
    choices = Text()
    for number, option in enumerate(data.get("options") or [], 1):
        choices.append(f" {number} ", style="bold reverse")
        choices.append(f" {option.get('name', '')}   ")
    return Group(head, choices)


class AgentInput(Input):
    """The line the user types to the agent. While the agent waits for a permission, a number
    answers it; escape goes back to the notebook (or refuses the permission)."""

    BINDINGS = [
        Binding("ctrl+c", "stop_agent", show=False, priority=True),
        Binding("pageup", "scroll_log(-1)", show=False),
        Binding("pagedown", "scroll_log(1)", show=False),
    ]

    def __init__(self, panel: AgentPanel) -> None:
        super().__init__(placeholder="Ask the agent about this notebook", classes="agent-input")
        self.panel = panel

    async def _on_key(self, event) -> None:
        panel = self.panel
        if panel.waiting_permission is not None and not self.value:
            if event.key == "escape" or event.character in ("n", "N"):
                event.stop()
                event.prevent_default()
                panel.answer_permission(None)
                return
            if event.character is not None and event.character.isdigit():
                event.stop()
                event.prevent_default()
                panel.answer_permission(int(event.character))
                return
        if event.key == "escape":
            event.stop()
            event.prevent_default()
            panel.leave()
            return
        await super()._on_key(event)

    def action_stop_agent(self) -> None:
        self.panel.stop_turn()

    def action_scroll_log(self, step: int) -> None:
        log = self.panel.log_box
        log.scroll_to(y=log.scroll_y + step * max(1, log.size.height - 2), animate=False)


class AgentPanel(Vertical, acp.Listener):
    """The right column of the app: the transcript above, the input line below."""

    DEFAULT_CSS = """
    AgentPanel {
        width: 64;
        max-width: 50%;
        height: 1fr;
        border-left: vkey $panel;
        padding: 0 0 0 1;
    }
    AgentPanel:focus-within {
        border-left: vkey $accent;
    }
    AgentPanel .agent-title {
        height: 1;
        text-style: bold;
    }
    AgentPanel .agent-log {
        height: 1fr;
        scrollbar-size-vertical: 1;
    }
    AgentPanel .agent-entry {
        height: auto;
        margin: 0 1 1 0;
    }
    AgentPanel .agent-tool, AgentPanel .agent-thought {
        margin: 0 1 0 0;
    }
    AgentPanel .agent-input {
        height: 3;
        border: round $panel;
    }
    AgentPanel .agent-input:focus {
        border: round $accent;
    }
    AgentPanel .agent-keys {
        height: 1;
        color: $text-muted;
    }
    """

    def __init__(self, app_link, cwd: Path) -> None:
        super().__init__(id="agent")
        self.link_of_app = app_link
        self.cwd = cwd
        self.agent: agents.Agent | None = None
        self.session: acp.AgentSession | None = None
        self.state = "off"
        self.entries: list[Entry] = []
        self.tools: dict[str, Entry] = {}
        self.plan: Entry | None = None
        self.streaming: Entry | None = None
        self.waiting_permission: Entry | None = None
        self._permission_answer: asyncio.Future | None = None
        self._queued: list[str] = []
        self._introduced = False
        self._said = 0
        self._flush_timer = None
        self.title = Static(classes="agent-title")
        self.log_box = VerticalScroll(classes="agent-log")
        self.input = AgentInput(self)
        self.keys = Static(AGENT_KEYS, classes="agent-keys")

    def compose(self) -> ComposeResult:
        yield self.title
        yield self.log_box
        yield self.input
        yield self.keys

    def on_mount(self) -> None:
        self.log_box.can_focus = False
        self._draw_title()

    def _draw_title(self) -> None:
        name = self.agent.title if self.agent is not None else "Agent"
        notes = {"off": "not started", "starting": "starting…", "ready": "", "working": "working…", "stopped": "stopped"}
        note = notes.get(self.state, "")
        self.title.update(Text.assemble((name, "bold"), (f"  {note}" if note else "", "dim")), layout=False)

    def _set_state(self, state: str) -> None:
        self.state = state
        self._draw_title()

    def leave(self) -> None:
        """escape: back to the notebook; the panel stays."""
        self.app.pane.focus_pane()

    def focus_input(self) -> None:
        self.input.focus()

    def add(self, kind: str, text: str = "", data: dict | None = None) -> Entry:
        entry = Entry(kind, text)
        entry.data = data or {}
        self.entries.append(entry)
        at_end = self._at_end()
        self.log_box.mount(entry.widget)
        entry.widget.update(entry.renderable())
        entry.dirty = False
        if at_end:
            self.log_box.scroll_end(animate=False)
        return entry

    def note(self, text: str, error: bool = False) -> None:
        self.streaming = None
        self.add("error" if error else "note", text)

    def _at_end(self) -> bool:
        log = self.log_box
        return log.scroll_y >= log.max_scroll_y - 2

    def touch(self, entry: Entry) -> None:
        """`entry` changed: it is painted with the next flush."""
        entry.dirty = True
        if self._flush_timer is None:
            self._flush_timer = self.set_timer(FLUSH_SECONDS, self.flush)

    def flush(self) -> None:
        self._flush_timer = None
        at_end = self._at_end()
        for entry in self.entries:
            if entry.dirty:
                entry.dirty = False
                entry.widget.update(entry.renderable())
        if at_end:
            self.log_box.scroll_end(animate=False)

    async def start(self, name: str | None = None) -> None:
        """Start the agent `name` (the default one when None) in a new session; a running one is closed first."""
        from . import config

        settings = config.load()
        known = agents.known(settings)
        name = name or (self.agent.name if self.agent is not None else agents.default_name(settings))
        if name not in known:
            self.note(f"No agent named {name}. Agents: {', '.join(known)}. Add one in the config: [agents] {name} = \"command\"", error=True)
            return
        await self.close_session()
        self.agent = known[name]
        self._introduced = False
        if (missing := self.agent.missing) is not None:
            self._set_state("off")
            self.note(f"{self.agent.title} needs the program `{missing}`, which is not installed. Install it, or choose another agent with :agent NAME ({', '.join(known)})", error=True)
            return
        self._set_state("starting")
        self.run_worker(self._open(self.agent), group="agent-open", exclusive=True, exit_on_error=False)

    async def _open(self, agent: agents.Agent) -> None:
        link = await self.link_of_app()
        session = self.session = acp.AgentSession(list(agent.command), self.cwd, self)
        try:
            await session.open([link.mcp_server()])
        except (acp.AgentError, OSError) as error:
            if self.session is session:
                self.session = None
                self._set_state("off")
                self.note(start_failure(agent, error, session.process.stderr_tail), error=True)
            await session.close()
            return
        if self.session is not session:
            await session.close()
            return
        self._set_state("ready")
        queued, self._queued = self._queued, []
        for text in queued:
            self._send(text)

    async def close_session(self) -> None:
        session, self.session = self.session, None
        self.answer_permission(None)
        self.streaming = None
        if session is not None:
            await session.close()
        self._set_state("off")

    def submit(self, text: str) -> None:
        """The user's line: shown at once, sent now or as soon as the agent is ready."""
        text = text.strip()
        if not text:
            return
        if self.state == "working":
            self.app.notify("The agent is working. ctrl+c stops it", severity="warning")
            return
        self.input.value = ""
        self.streaming = None
        self.add("user", text)
        self.log_box.scroll_end(animate=False)
        if self.state == "ready":
            self._send(text)
        elif self.state == "starting":
            self._queued.append(text)
        else:
            self._queued.append(text)
            self.run_worker(self.start(), group="agent-start", exit_on_error=False)

    def _send(self, text: str) -> None:
        self._set_state("working")
        self.run_worker(self._turn(self.session, text), group="agent-turn", exit_on_error=False)

    async def _turn(self, session: acp.AgentSession, text: str) -> None:
        blocks = [acp.text_block(text), acp.text_block(self.context_note())]
        said_before = self._said
        try:
            reason = await session.prompt(blocks)
        except acp.AgentError as error:
            if self.session is session:
                self.note(str(error), error=True)
                self._set_state("ready" if session.process.running else "stopped")
            return
        if self.session is not session:
            return
        self.flush()
        self.streaming = None
        if reason in STOP_NOTES:
            self.note(STOP_NOTES[reason])
        elif self._said == said_before:
            program = agents.NEEDS.get(self.agent.name, self.agent.command[0])
            self.note(f"{self.agent.title} ended its turn and said nothing. Run `{program}` in a terminal and check that it is logged in and has a model", error=True)
        self._set_state("ready")

    def context_note(self) -> str:
        """What the agent is told with each prompt: which notebook is open and where the cursor is."""
        pane = self.app.pane
        current = pane.current_cell()
        where = f", the cursor is in {current.state.label}" if current is not None else ""
        note = f"[gpython] Open notebook: {pane.path or 'untitled (not saved yet)'}, {len(pane.states)} cells{where}."
        if not self._introduced:
            self._introduced = True
            note += (
                " The user talks to you from a panel beside this notebook in gpython, a terminal notebook. "
                "Use the gpython tools (cells_read, cell_edit, cell_insert, cell_delete, cells_run, variables, "
                "variable_value, python_eval) to read, change and run its cells: the user sees each change at "
                "once and the kernel keeps its variables. Do not edit the notebook's file on disk."
            )
        return note

    def stop_turn(self) -> None:
        """ctrl+c: stop the agent's turn; a waiting permission is refused."""
        self.answer_permission(None)
        if self.session is not None and self.state == "working":
            self.session.cancel()

    def session_update(self, update: dict) -> None:
        shows = self.UPDATE_SHOWN.get(str(update.get("sessionUpdate")))
        if shows is not None:
            self._said += 1
            getattr(self, shows)(update)

    UPDATE_SHOWN = {
        "agent_message_chunk": "_message_chunk", "agent_thought_chunk": "_thought_chunk",
        "tool_call": "_tool_call", "tool_call_update": "_tool_update", "plan": "_plan",
    }

    def _message_chunk(self, update: dict) -> None:
        self._chunk("agent", update.get("content") or {})

    def _thought_chunk(self, update: dict) -> None:
        self._chunk("thought", update.get("content") or {})

    def _tool_call(self, update: dict) -> None:
        self.streaming = None
        call = {key: value for key, value in update.items() if key != "sessionUpdate"}
        self.tools[str(update.get("toolCallId"))] = self.add("tool", data=call)

    def _plan(self, update: dict) -> None:
        self.streaming = None
        if self.plan is None:
            self.plan = self.add("plan", data={"entries": update.get("entries") or []})
        else:
            self.plan.data["entries"] = update.get("entries") or []
            self.touch(self.plan)

    def _chunk(self, kind: str, content: dict) -> None:
        text = content.get("text") if content.get("type") == "text" else None
        if not text:
            return
        entry = self.streaming
        if entry is None or entry.kind != kind:
            self.streaming = self.add(kind, text)
            return
        entry.text += text
        self.touch(entry)

    def _tool_update(self, update: dict) -> None:
        entry = self.tools.get(str(update.get("toolCallId")))
        if entry is None:
            self.streaming = None
            entry = self.tools[str(update.get("toolCallId"))] = self.add("tool")
        for key, value in update.items():
            if key != "sessionUpdate" and value is not None:
                entry.data[key] = value
        self.touch(entry)

    async def permission(self, tool_call: dict, options: list[dict]) -> str | None:
        """The agent waits for a yes or no: the question goes in the transcript and the input line answers it."""
        self.answer_permission(None)
        known = self.tools.get(str(tool_call.get("toolCallId")))
        call = {**(known.data if known is not None else {}), **{k: v for k, v in tool_call.items() if v is not None}}
        self.streaming = None
        entry = self.waiting_permission = self.add("permission", data={"call": call, "options": options, "answer": None})
        self._permission_answer = asyncio.get_running_loop().create_future()
        self.keys.update(PERMISSION_KEYS, layout=False)
        self.log_box.scroll_end(animate=False)
        if not self.input.has_focus:
            self.app.notify("The agent asks for permission. Go to the agent panel (ctrl+w l) to answer")
        chosen = await self._permission_answer
        entry.data["answer"] = next((o.get("name") for o in options if o.get("optionId") == chosen), None) or "Refused"
        self.touch(entry)
        return chosen

    def answer_permission(self, number: int | None) -> bool:
        """Answer the waiting question with option `number` (1-based), or refuse it with None. False if no such option."""
        entry, answer = self.waiting_permission, self._permission_answer
        if entry is None or answer is None:
            return False
        options = entry.data.get("options") or []
        if number is not None and not 1 <= number <= len(options):
            return False
        self.waiting_permission = self._permission_answer = None
        self.keys.update(AGENT_KEYS, layout=False)
        if not answer.done():
            answer.set_result(options[number - 1].get("optionId") if number is not None else None)
        return True

    def agent_exited(self, code: int | None, stderr_tail: str) -> None:
        if self.session is None or not self.is_attached:
            return
        self.session = None
        self.answer_permission(None)
        self._set_state("stopped")
        last = stderr_tail.strip().splitlines()[-1:] or [""]
        self.note(f"The agent stopped (exit code {code}). {last[0]}  :agent starts it again".strip(), error=bool(code))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.submit(event.value)

    async def on_unmount(self) -> None:
        if self.session is not None:
            await self.session.close()


def start_failure(agent: agents.Agent, error: Exception, stderr_tail: str) -> str:
    """Why the agent did not start, and what the user can do about it."""
    if isinstance(error, acp.AgentError) and error.code == acp.AUTH_REQUIRED:
        return f"{agent.title} is not logged in. Run `{agents.NEEDS.get(agent.name, agent.command[0])}` in a terminal, log in, then :agent {agent.name} again"
    last = stderr_tail.strip().splitlines()[-1:] or [""]
    detail = f"{error}" if str(error) else type(error).__name__
    return f"{agent.title} did not start ({' '.join(agent.command)}): {detail} {last[0] if last[0] not in detail else ''}".strip()
