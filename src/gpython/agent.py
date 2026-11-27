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
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static

from . import acp, agents

FLUSH_SECONDS = 0.08
DIFF_LINES = 12
TOOL_TEXT_LINES = 6
ASK_FOR_MODEL = "?"
MIN_WIDTH = 30
NOTEBOOK_ROOM = 20
TITLE_CHARS = 300
FOLDED_KINDS = {"tools": "tool", "thoughts": "thought"}
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
AGENT_KEYS = "enter send · /model · ctrl+c stop · esc notebook"
PERMISSION_KEYS = "press the number · esc refuse"


class Entry:
    """One block of the transcript and the widget that shows it."""

    def __init__(self, kind: str, text: str = "") -> None:
        self.kind = kind
        self.text = text
        self.data: dict = {}
        self.widget = Static(classes=f"agent-entry agent-{kind}")
        self.dirty = True
        self.shown = True
        self.turn = 0

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


class OneLine:
    """A text drawn on one line and cut with … where the space ends."""

    def __init__(self, text: Text) -> None:
        self.text = text

    @property
    def plain(self) -> str:
        return self.text.plain

    def __rich_console__(self, console, options):
        line = self.text.copy()
        line.truncate(options.max_width, overflow="ellipsis")
        yield line


def tool_picture(call: dict) -> Group | OneLine:
    """A tool call: its state and its title on one line, cut where the panel ends (a shell
    command can be a page long), then the diff of an edit or the reason it failed."""
    status = call.get("status") or "pending"
    failed = status == "failed"
    line = OneLine(Text.assemble((f"{STATUS_MARKS.get(status, '○')} ", STATUS_STYLES.get(status, "dim")), (short_title(call, "tool"), "red" if failed else "")))
    items = [item for item in call.get("content") or [] if isinstance(item, dict)]
    details = [detail for detail in (tool_detail(item, failed) for item in items) if detail is not None]
    return Group(line, *details) if details else line


def short_title(call: dict, fallback: str) -> str:
    """A tool call's title with its line breaks and runs of spaces as one space each, and no longer than a few lines of the panel."""
    return " ".join(str(call.get("title") or call.get("name") or fallback).split())[:TITLE_CHARS]


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


def permission_picture(data: dict) -> Group | OneLine:
    call = data.get("call") or {}
    answer = data.get("answer")
    title = " ".join(str(call.get("title") or "this action").split())
    if answer is not None:
        return OneLine(Text.assemble(("● ", "dim"), (f"{answer}: ", "bold"), (short_title(call, "this action"), "dim")))
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


class ModelPicker(ModalScreen[str | None]):
    """/model: the agent's models in a list at the bottom; enter chooses one, escape keeps the current one."""

    DEFAULT_CSS = """
    ModelPicker {
        align: center bottom;
    }
    ModelPicker > Vertical {
        height: auto;
        max-height: 60%;
        border-top: solid $accent;
        background: $surface;
        padding: 0 1;
    }
    ModelPicker OptionList, ModelPicker OptionList:focus {
        height: auto;
        max-height: 100%;
        border: none;
        padding: 0;
        background: $surface;
    }
    """
    BINDINGS = [Binding("escape,q", "dismiss(None)", show=False), Binding("j", "move(1)", show=False), Binding("k", "move(-1)", show=False)]
    APP_ACTIONS_OFF = {"add_cell", "delete_cell", "run_cell", "split_cell", "interrupt", "command_palette", "window_prefix"}

    def __init__(self, models: list[acp.Model], current: str) -> None:
        super().__init__()
        self.models = models
        self.current = current

    def compose(self) -> ComposeResult:
        from textual.widgets.option_list import Option

        rows = [
            Option(Text.assemble(("● " if model.value == self.current else "  ", "green"), (model.name, "bold"), (f"  {model.description}" if model.description else "", "dim")), id=str(index))
            for index, model in enumerate(self.models)
        ]
        with Vertical():
            yield Static(Text.assemble(("Model", "bold"), ("   enter choose · esc keep", "dim")))
            yield OptionList(*rows)

    def on_mount(self) -> None:
        options = self.query_one(OptionList)
        options.highlighted = next((index for index, model in enumerate(self.models) if model.value == self.current), 0)
        options.focus()

    def action_move(self, step: int) -> None:
        options = self.query_one(OptionList)
        options.highlighted = max(0, min(len(self.models) - 1, (options.highlighted or 0) + step))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self.models[int(event.option.id)].value)


class AgentPanel(Vertical, acp.Listener):
    """The right column of the app: the transcript above, the input line below."""

    DEFAULT_CSS = """
    AgentPanel {
        width: 64;
        max-width: 85%;
        height: 1fr;
        border-left: vkey $panel;
        padding: 0 0 0 1;
    }
    AgentPanel:focus-within {
        border-left: vkey $accent;
    }
    AgentPanel .agent-head {
        height: 1;
    }
    AgentPanel .agent-title {
        height: 1;
        width: 1fr;
        text-style: bold;
    }
    AgentPanel .agent-model {
        height: 1;
        width: auto;
        max-width: 60%;
        margin: 0 2 0 1;
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
        self._model_wanted = ""
        self._edge_held = False
        self._width_set = 0
        self._turn_number = 0
        self.step_modes: dict[str, str] = {}
        self._flush_timer = None
        self.title = Static(classes="agent-title")
        self.model_label = Static(classes="agent-model")
        self._model_shown = ""
        self.log_box = VerticalScroll(classes="agent-log")
        self.input = AgentInput(self)
        self.keys = Static(AGENT_KEYS, classes="agent-keys")

    def compose(self) -> ComposeResult:
        with Horizontal(classes="agent-head"):
            yield self.title
            yield self.model_label
        yield self.log_box
        yield self.input
        yield self.keys

    def on_mount(self) -> None:
        from . import config

        self.log_box.can_focus = False
        self._draw_title()
        width = config.load().get("agent.width")
        if isinstance(width, int) and not isinstance(width, bool):
            self.set_width(width)

    def set_width(self, columns: int) -> int:
        """Make the panel `columns` wide, within what leaves room for the notebook and for a line of text."""
        most = max(MIN_WIDTH, self.app.size.width - NOTEBOOK_ROOM)
        columns = max(MIN_WIDTH, min(most, columns))
        self.styles.width = columns
        return columns

    def on_mouse_down(self, event) -> None:
        """Take hold of the panel's left edge; moving the mouse then makes the panel wider or narrower."""
        if event.button == 1 and event.screen_x == self.region.x:
            self._edge_held = True
            self.capture_mouse()
            event.stop()

    def on_mouse_move(self, event) -> None:
        if self._edge_held:
            self._width_set = self.set_width(self.region.right - event.screen_x)
            event.stop()

    def on_mouse_up(self, event) -> None:
        if self._edge_held:
            self._edge_held = False
            self.release_mouse()
            event.stop()
            if self._width_set:
                from . import config

                config.save({"agent.width": self._width_set})

    def _draw_title(self) -> None:
        """The agent's name and state on the left, the model in use on the right."""
        name = self.agent.title if self.agent is not None else "Agent"
        notes = {"off": "not started", "starting": "starting…", "ready": "", "working": "working…", "stopped": "stopped"}
        note = notes.get(self.state, "")
        self.title.update(Text.assemble((name, "bold"), (f"  {note}" if note else "", "dim")), layout=False)
        model = self.model_name
        if model != self._model_shown:
            self._model_shown = model
            self.model_label.update(Text(model, style="dim"))

    @property
    def model_name(self) -> str:
        """The name of the model the agent uses now; empty when the agent does not say."""
        model = self.session.current_model() if self.session is not None and self.session.session_id else None
        return model.name if model is not None else ""

    def choose_model(self, wanted: str = "") -> None:
        """/model, :agent model: with a name, use that model; with none, show the list to choose from."""
        session = self.session
        models = session.models() if session is not None and self.state in ("ready", "working") else []
        if self.state == "starting":
            self._model_wanted = wanted or ASK_FOR_MODEL
            return
        if not models:
            self.note("This agent gives no list of models. Choose the model in the agent's own program")
            return
        if not wanted:
            current = session.current_model()
            self.app.push_screen(ModelPicker(models, current.value if current is not None else ""), self._model_chosen)
            return
        match = model_named(models, wanted)
        if match is None:
            self.note(f"No model named {wanted}. Models: {', '.join(m.name for m in models)}", error=True)
        else:
            self._model_chosen(match.value)

    def _model_chosen(self, value: str | None) -> None:
        if value is not None and self.session is not None:
            self.run_worker(self._set_model(self.session, value), group="agent-model", exit_on_error=False)

    async def _set_model(self, session: acp.AgentSession, value: str) -> None:
        try:
            await session.set_model(value)
        except acp.AgentError as error:
            self.note(f"The agent did not change the model: {error}", error=True)
            return
        self._draw_title()
        self.note(f"Model: {self.model_name or value}")

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
        entry.turn = self._turn_number
        if self.step_modes.get(kind) == "hide":
            entry.shown = False
            return entry
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
        if not entry.shown:
            return
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

    def fold_turn(self) -> None:
        """The turn is over: its tool calls and thoughts leave the conversation when their mode
        is fold (`[agent] tools`, `thoughts`), and one line says how many calls there were."""
        folded = [e for e in self.entries if self._folds(e)]
        if not folded:
            return
        at = self.entries.index(folded[0])
        self.entries = [e for e in self.entries if not self._folds(e)]
        for entry in folded:
            entry.shown = False
            entry.widget.remove()
        summary = calls_summary([e.data for e in folded if e.kind == "tool"])
        if summary:
            self._insert_note(at, summary)

    def _folds(self, entry: Entry) -> bool:
        return entry.turn == self._turn_number and self.step_modes.get(entry.kind) == "fold"

    def _insert_note(self, at: int, text: str) -> None:
        """A note at position `at` of the conversation, not at its end."""
        note = Entry("note", text)
        note.turn = self._turn_number
        note.dirty = False
        note.widget.update(note.renderable())
        after = self.entries[at].widget if at < len(self.entries) else None
        self.entries.insert(at, note)
        self.log_box.mount(note.widget, before=after)

    def read_step_modes(self, settings: dict) -> None:
        """Take `[agent] tools` and `thoughts` from the config; they apply from the next step on."""
        self.step_modes = {kind: agents.step_mode(settings, key) for key, kind in FOLDED_KINDS.items()}

    async def start(self, name: str | None = None) -> None:
        """Start the agent `name` (the default one when None) in a new session; a running one is closed first."""
        from . import config

        settings = config.load()
        self.read_step_modes(settings)
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
        if self._model_wanted:
            wanted, self._model_wanted = self._model_wanted, ""
            self.choose_model("" if wanted == ASK_FOR_MODEL else wanted)
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
        word, _, rest = text.partition(" ")
        if word in ("/model", "/models"):
            self.input.value = ""
            self.choose_model(rest.strip())
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
        self._turn_number += 1
        self._set_state("working")
        self.run_worker(self._turn(self.session, text), group="agent-turn", exit_on_error=False)

    async def _turn(self, session: acp.AgentSession, text: str) -> None:
        blocks = [acp.text_block(text), acp.text_block(self.context_note())]
        said_before = self._said
        try:
            reason = await session.prompt(blocks)
        except acp.AgentError as error:
            if self.session is session:
                self.fold_turn()
                self.note(str(error), error=True)
                self._set_state("ready" if session.process.running else "stopped")
            return
        if self.session is not session:
            return
        self.flush()
        self.streaming = None
        self.fold_turn()
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
        if update.get("sessionUpdate") == "config_option_update":
            self._draw_title()
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


def model_named(models: list[acp.Model], wanted: str) -> acp.Model | None:
    """The model whose id or name is `wanted`, else the first one that has `wanted` in either; case does not count."""
    low = wanted.lower()
    exact = [m for m in models if low in (m.value.lower(), m.name.lower())]
    partial = [m for m in models if low in m.value.lower() or low in m.name.lower()]
    return next(iter(exact + partial), None)


def calls_summary(calls: list[dict]) -> str:
    """The line that stands for a turn's folded tool calls; empty when there were none."""
    if not calls:
        return ""
    failed = sum(1 for call in calls if call.get("status") == "failed")
    return f"{len(calls)} tool call{'s' if len(calls) != 1 else ''}" + (f", {failed} failed" if failed else "")


def start_failure(agent: agents.Agent, error: Exception, stderr_tail: str) -> str:
    """Why the agent did not start, and what the user can do about it."""
    if isinstance(error, acp.AgentError) and error.code == acp.AUTH_REQUIRED:
        return f"{agent.title} is not logged in. Run `{agents.NEEDS.get(agent.name, agent.command[0])}` in a terminal, log in, then :agent {agent.name} again"
    last = stderr_tail.strip().splitlines()[-1:] or [""]
    detail = f"{error}" if str(error) else type(error).__name__
    return f"{agent.title} did not start ({' '.join(agent.command)}): {detail} {last[0] if last[0] not in detail else ''}".strip()
