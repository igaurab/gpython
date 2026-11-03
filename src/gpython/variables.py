"""The variables panel (ctrl+v, :vars) and the debugger that shows in it.

Variables mode lists what the cells defined, with type, shape or length, a short value and the
cell that bound it; enter opens the full value, g jumps to the cell, d deletes the name from the
kernel, / filters, a hides modules, functions and classes (they show by default).

When a cell pauses in breakpoint() (or `r d`, `:debug`), the same panel turns into the debugger:
the loop stack, pointer strips over sequences, grids and dict changes on top (debugview), the
locals with their history in the table, watches and the stack under it. Keys are listed in
DEBUG_KEYS and in the help screen. The module is imported on the first ctrl+v or the first
stop, so a notebook that never opens the panel pays nothing.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input, Label, Static, TextArea, Tree

from . import debugview

VARS_KEYS = "enter value · g go to cell · d delete · / filter · a all · q close"
DEBUG_KEYS = (
    "n next · s step · r return · c continue · q quit · ] next iteration · } outer · ) loop end · "
    "b / B stop if (B records) · G iteration · h/l back/forward · T timeline · u/d stack · w watch · e pdb · enter value"
)
POSTMORTEM_KEYS = "h/l back/forward · T timeline · u/d stack · w watch · e pdb · enter value · q done"


class SideColumn(Vertical):
    """The right column of a notebook window: the outline above, the variables panel below."""

    DEFAULT_CSS = """
    SideColumn {
        width: 42;
        max-width: 50%;
        height: 1fr;
    }
    SideColumn.-wide {
        width: 82;
        max-width: 60%;
    }
    """

    def __init__(self) -> None:
        super().__init__(id="side")

    def sync(self) -> None:
        """Shown while any panel in it is; wide while the debugger is."""
        shown = [child for child in self.children if child.display]
        self.display = bool(shown)
        wide = any(isinstance(child, VariablesPanel) and child.mode == "debug" for child in shown)
        if wide != self.has_class("-wide"):
            self.set_class(wide, "-wide")


class VariablesPanel(Vertical):
    """A title line, a picture (debug mode), a table of names and a footer."""

    DEFAULT_CSS = """
    VariablesPanel {
        height: 1fr;
        border-left: vkey $panel;
        padding: 0 0 0 1;
    }
    VariablesPanel:focus-within {
        border-left: vkey $accent;
    }
    VariablesPanel .vars-title {
        height: auto;
        text-style: bold;
    }
    VariablesPanel .vars-picture {
        height: auto;
        max-height: 60%;
        display: none;
        scrollbar-size-vertical: 1;
    }
    VariablesPanel .vars-picture Static {
        height: auto;
    }
    VariablesPanel DataTable {
        height: 1fr;
        scrollbar-size-vertical: 1;
    }
    VariablesPanel .vars-footer {
        height: auto;
        max-height: 40%;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("j,down", "move(1)", show=False),
        Binding("k,up", "move(-1)", show=False),
        Binding("escape", "back", show=False),
    ] + [
        Binding(key, f"press({key!r})", show=False)
        for key in (
            "n", "s", "r", "c", "q", "u", "d", "w", "g", "G", "a", "b", "B", "e", "h", "l", "T", "slash",
            "right_square_bracket", "right_curly_bracket", "right_parenthesis",
        )
    ]

    def __init__(self, pane) -> None:
        super().__init__(id="variables")
        self.pane = pane
        self.mode = "vars"
        self.show_all = True
        self.filter = ""
        self.rows: list[tuple] = []
        self.session: debugview.DebugSession | None = None
        self.title = Static(classes="vars-title")
        self.picture_box = VerticalScroll(classes="vars-picture")
        self.picture = Static()
        self.table = DataTable(cursor_type="row", zebra_stripes=False, show_cursor=True)
        self.footer = Static(classes="vars-footer")
        self._columns = ""
        self._names: list[str] = []

    def compose(self) -> ComposeResult:
        yield self.title
        with self.picture_box:
            yield self.picture
        yield self.table
        yield self.footer

    def on_mount(self) -> None:
        self.call_after_refresh(self._first_draw)

    def _first_draw(self) -> None:
        """The rows known when the panel was built (`is_mounted` is still False in on_mount)."""
        if self.mode == "vars":
            self.show_vars(self.rows)

    @property
    def width(self) -> int:
        return max(30, (self.content_size.width or 80) - 2)

    def _set_columns(self, kind: str, names: tuple[str, ...]) -> None:
        if self._columns != kind:
            self._columns = kind
            self.table.clear(columns=True)
            for name in names:
                self.table.add_column(name, key=name)

    def _fill(self, rows: list[tuple[str, list]]) -> None:
        """Replace the table's rows, keeping the cursor on the same name."""
        table = self.table
        keep = self._names[table.cursor_row] if 0 <= table.cursor_row < len(self._names) else None
        table.clear()
        self._names = [name for name, _ in rows]
        for name, cells in rows:
            table.add_row(*cells, key=name)
        if keep in self._names:
            table.move_cursor(row=self._names.index(keep), animate=False)

    def current_name(self) -> str | None:
        row = self.table.cursor_row
        return self._names[row] if 0 <= row < len(self._names) else None

    def _visible(self, name: str, kind: str, category: str) -> bool:
        if not self.show_all and category in ("module", "function", "class"):
            return False
        needle = self.filter.lower()
        return not needle or needle in name.lower() or needle in kind.lower()

    def show_vars(self, rows: list[tuple]) -> None:
        """Variables mode: rows are (name, type, value, shape, category, cell label)."""
        self.rows = rows
        if self.mode != "vars" or not self.is_mounted:
            return
        self._set_columns("vars", ("name", "type", "shape", "value", "cell"))
        shown = [row for row in rows if self._visible(row[0], row[1], row[4])]
        self._fill([(row[0], [Text(row[0], style="bold"), Text(row[1], style="dim"), row[3], row[2], Text(row[5], style="dim")]) for row in shown])
        hidden = len(rows) - len(shown)
        title = Text("Variables", style="bold")
        if self.filter:
            title.append(f"  /{self.filter}", style="cyan")
        if hidden:
            title.append(f"  {hidden} hidden (a)", style="dim")
        if not rows:
            title.append("  run a cell to fill it", style="dim")
        self.title.update(title)
        self.footer.update(Text(VARS_KEYS, style="dim"))

    def set_mode(self, mode: str) -> None:
        if mode == self.mode:
            return
        self.mode = mode
        self.picture_box.display = mode == "debug"
        if mode == "vars":
            self.session = None
            self.show_vars(self.rows)
        if isinstance(self.parent, SideColumn):
            self.parent.sync()
        if mode == "debug":
            self.call_after_refresh(self._redraw)

    def _redraw(self) -> None:
        """Draw again at the column's new width once the wide layout has happened."""
        if self.mode == "debug" and self.session is not None:
            self.show_debug(self.session)

    def show_debug(self, session: debugview.DebugSession) -> None:
        """Debug mode: draw the record on screen (the stop, or where h / l travelled to)."""
        self.session = session
        self.set_mode("debug")
        payload, record = session.payload, session.record
        width = self.width
        self.title.update(self._debug_title(session))
        lines = debugview.render_record(record, session.before, width)
        self.picture.update(Text("\n").join(lines) if lines else Text("no loops or sequences here", style="dim"))
        self._set_columns("debug", ("name", "value", "history"))
        before = dict((session.before or {}).get("vars", []))
        rows = []
        if session.live:
            for name, kind, shown, info, category in payload.get("locals", []):
                if not self._visible(name, kind, category):
                    continue
                changed = name in before and before[name] != dict(record.get("vars", [])).get(name, shown)
                history = session.history_of(name)
                trail = " → ".join(history[-5:]) if len(history) > 1 else ""
                rows.append((name, [Text(name, style=debugview.CHANGED if changed else "bold"), shown, Text(trail, style="dim")]))
        else:
            for name, shown in record.get("vars", []):
                if self.filter and self.filter.lower() not in name.lower():
                    continue
                changed = name in before and before[name] != shown
                rows.append((name, [Text(name, style=debugview.CHANGED if changed else "bold"), shown, ""]))
        self._fill(rows)
        self.footer.update(self._debug_footer(session))

    def _debug_title(self, session: debugview.DebugSession) -> Text:
        payload, record = session.payload, session.record
        place = self.pane.place_of(record)
        if payload.get("postmortem"):
            title = Text.assemble((" POST-MORTEM ", "bold black on red"), " ", (payload.get("exception", "") + "  ", "red"))
        else:
            title = Text.assemble((" DEBUG ", "bold black on red"), " ")
            if payload.get("event") == "exception":
                title.append(payload.get("exception", "") + "  ", style="red")
            elif payload.get("event") == "return":
                title.append("returning  ", style="dim")
        title.append(place, style="bold")
        if not session.live:
            title.append(f"   ⏪ {session.position():,} of {session.total:,}", style="bold yellow")
        if session.running:
            title.append("   running…", style="bold yellow")
        return title

    def _debug_footer(self, session: debugview.DebugSession) -> Text:
        payload = session.payload
        parts: list[Text] = []
        for expr, value in payload.get("watches", []):
            parts.append(Text.assemble(("watch ", "dim"), (expr, "bold"), " = ", value))
        stack = payload.get("stack", [])
        if len(stack) > 1:
            current = payload.get("frame", len(stack) - 1)
            for index, frame in enumerate(stack):
                marker = "▸ " if index == current else "  "
                parts.append(Text(marker + self.pane.place_of(frame), style="bold" if index == current else "dim"))
        reply = payload.get("reply") or {}
        if reply.get("kind") == "error":
            parts.append(Text(reply.get("text", ""), style="bold red"))
        if payload.get("output"):
            parts.append(Text(payload["output"].strip()[-600:]))
        history = session.history
        if history:
            parts.append(Text(debugview.history_line(history), style="cyan"))
            warning = debugview.history_warning(history)
            if warning:
                parts.append(Text(warning, style="bold yellow"))
        parts.append(Text(POSTMORTEM_KEYS if payload.get("postmortem") else DEBUG_KEYS, style="dim"))
        return Text("\n").join(parts)

    def action_move(self, step: int) -> None:
        if step > 0:
            self.table.action_cursor_down()
        else:
            self.table.action_cursor_up()

    def action_back(self) -> None:
        self.pane.focus_pane()

    def action_press(self, key: str) -> None:
        handler = (self._debug_key if self.mode == "debug" else self._vars_key)
        handler(key)

    def _vars_key(self, key: str) -> None:
        pane, name = self.pane, self.current_name()
        if key == "q":
            pane.hide_variables()
        elif key == "g" and name:
            pane.jump_to_definition(name)
        elif key == "d" and name:
            pane.run_worker(pane.delete_variable(name), group="vars")
        elif key == "a":
            self.show_all = not self.show_all
            self.show_vars(self.rows)
        elif key == "slash":
            self._ask("filter by name or type: ", self.filter, self._set_filter)

    def _set_filter(self, text: str | None) -> None:
        self.filter = (text or "").strip()
        if self.mode == "vars":
            self.show_vars(self.rows)
        elif self.session is not None:
            self.show_debug(self.session)

    def _ask(self, label: str, value: str, then) -> None:
        from .app import Prompt

        self.app.push_screen(Prompt(label, value), then)

    def _debug_key(self, key: str) -> None:
        pane, session = self.pane, self.session
        if session is None:
            return
        postmortem = session.payload.get("postmortem")
        commands = {"n": "n", "s": "s", "r": "r", "c": "c", "right_square_bracket": "_gp_next 0", "right_curly_bracket": "_gp_next 1", "right_parenthesis": "_gp_end"}
        if key in commands:
            if postmortem:
                self.app.notify("The cell already failed: this is a post-mortem, nothing runs from here", severity="warning")
                return
            pane.debug_command(commands[key])
        elif key == "q":
            pane.debug_command("q")
        elif key in ("u", "d"):
            frames = len(session.payload.get("stack", []))
            current = session.payload.get("frame", frames - 1)
            target = current - 1 if key == "u" else current + 1
            if 0 <= target < frames:
                pane.debug_command(f"_gp_frame {target}", keep_view=True)
        elif key == "h" or key == "l":
            pane.debug_travel(-1 if key == "h" else 1)
        elif key == "T":
            pane.debug_timeline()
        elif key == "w":
            self._ask("watch: ", "", lambda text: text and pane.debug_command(f"_gp_watch {text.strip()}", keep_view=True))
        elif key in ("b", "B"):
            command = "_gp_until" if key == "b" else "_gp_until_rec"
            label = "stop if: " if key == "b" else "stop if (recording every line on the way): "
            if not postmortem:
                self._ask(label, "", lambda text: text and pane.debug_command(f"{command} {text.strip()}"))
        elif key == "G":
            if not postmortem:
                self._ask("go to iteration (N, or N of loop L counted from the outermost, as N L): ", "", self._goto)
        elif key == "e":
            self._ask("pdb> ", "", lambda text: text and pane.debug_command(text.strip(), keep_view=True))
        elif key == "g":
            pane.jump_to_record(session.record)
        elif key == "a":
            self.show_all = not self.show_all
            self.show_debug(session)
        elif key == "slash":
            self._ask("filter by name or type: ", self.filter, self._set_filter)

    def _goto(self, text: str | None) -> None:
        if not text or self.session is None:
            return
        parts = text.split()
        loops = self.session.record.get("loops", [])
        try:
            target = int(parts[0])
            level = int(parts[1]) if len(parts) > 1 else len(loops)
        except ValueError:
            self.app.notify("go to iteration: a number, optionally followed by the loop level", severity="warning")
            return
        depth = max(0, len(loops) - max(1, level))
        self.pane.debug_command(f"_gp_goto {depth} {target}")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        name = self.current_name()
        if name is None:
            return
        if self.mode == "debug" and self.session is not None:
            if self.session.live:
                self.pane.debug_command(f"_gp_full {name}", keep_view=True)
            else:
                shown = dict(self.session.record.get("vars", [])).get(name, "")
                self.app.push_screen(ValueScreen(name, {"kind": "text", "text": shown + "\n\n(as recorded; l goes forward to the stop)"}))
        else:
            self.pane.run_worker(self.pane.show_value(name), group="vars")


class ValueScreen(ModalScreen):
    """The full value of a name: scrollable text, or a table for a DataFrame or 2-D data."""

    DEFAULT_CSS = """
    ValueScreen {
        align: center middle;
    }
    ValueScreen > Vertical {
        width: 90%;
        height: 85%;
        border: round $accent;
        background: $surface;
    }
    ValueScreen .value-title {
        height: 1;
        text-style: bold;
    }
    ValueScreen TextArea, ValueScreen DataTable {
        height: 1fr;
    }
    """

    BINDINGS = [Binding("escape,q", "dismiss", show=False)]

    def __init__(self, name: str, value: dict) -> None:
        super().__init__()
        self.value_name, self.value = name, value

    def compose(self) -> ComposeResult:
        value = self.value
        with Vertical():
            if value.get("kind") == "table":
                rows, cols = (value.get("total") or [len(value["rows"]), len(value["columns"])])[:2]
                shown = len(value["rows"])
                note = f"  {rows}×{cols}" + (f", first {shown} rows" if shown < rows else "")
                yield Label(Text.assemble((self.value_name, "bold"), (note + "  ·  q closes", "dim")), classes="value-title")
                table = DataTable(cursor_type="cell")
                table.add_column("", key="_index")
                for column in value["columns"]:
                    table.add_column(column)
                for index, row in zip(value["index"], value["rows"]):
                    table.add_row(Text(index, style="dim"), *row)
                yield table
            else:
                yield Label(Text.assemble((self.value_name, "bold"), ("  ·  q closes", "dim")), classes="value-title")
                yield TextArea(value.get("text", ""), read_only=True, soft_wrap=False, show_line_numbers=False)

    def on_mount(self) -> None:
        self.query_one("TextArea, DataTable").focus()


class TimelineScreen(ModalScreen[int | None]):
    """T in the debugger: one row per iteration of the outermost loop, expanding into the inner
    loop's iterations; enter travels to the end of that iteration."""

    DEFAULT_CSS = """
    TimelineScreen {
        align: center middle;
    }
    TimelineScreen > Vertical {
        width: 90%;
        height: 85%;
        border: round $accent;
        background: $surface;
    }
    TimelineScreen .timeline-title {
        height: 1;
        text-style: bold;
    }
    TimelineScreen Tree {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("escape,q", "dismiss(None)", show=False),
        Binding("j", "cursor(1)", show=False),
        Binding("k", "cursor(-1)", show=False),
        Binding("l,right", "fold(True)", show=False),
        Binding("h,left", "fold(False)", show=False),
    ]

    def __init__(self, roots: list[debugview.Iteration], total: int) -> None:
        super().__init__()
        self.roots, self.total = roots, total

    def compose(self) -> ComposeResult:
        marked = sum(1 for node in self.roots if node.warnings)
        title = Text.assemble(("Timeline", "bold"), (f"  {self.total:,} moments recorded", "dim"))
        if marked:
            title.append(f"  ⚠ {marked} iterations with warnings", style="bold red")
        title.append("  ·  enter goes there · l/h expand/collapse · q closes", style="dim")
        tree: Tree = Tree("loops")
        tree.show_root = False
        tree.guide_depth = 2
        self._add_all(tree.root, self.roots)
        with Vertical():
            yield Label(title, classes="timeline-title")
            yield tree

    def _add(self, parent, node: debugview.Iteration) -> None:
        if node.children:
            branch = parent.add(node.label(), data=node.index, expand=bool(node.warnings))
            self._add_all(branch, node.children)
        else:
            parent.add_leaf(node.label(), data=node.index)

    def _add_all(self, parent, nodes: list[debugview.Iteration]) -> None:
        """The rows of one loop, with a note where thinning left out iterations."""
        previous = None
        for node in nodes:
            if previous is not None and previous.k is not None and node.k is not None and node.k - previous.k > 1:
                parent.add_leaf(Text(f"     … {node.k - previous.k - 1:,} iterations thinned", style="dim italic"))
            self._add(parent, node)
            previous = node

    def on_mount(self) -> None:
        tree = self.query_one(Tree)
        tree.focus()
        marked = next((node for node in tree.root.children if "⚠" in str(node.label)), None)
        if marked is None and tree.root.children:
            marked = tree.root.children[0]
        if marked is not None:
            tree.move_cursor(marked)

    def action_cursor(self, step: int) -> None:
        tree = self.query_one(Tree)
        if step > 0:
            tree.action_cursor_down()
        else:
            tree.action_cursor_up()

    def action_fold(self, expand: bool) -> None:
        """l opens the iteration under the cursor (its inner loop's iterations), h closes it or goes to its parent."""
        tree = self.query_one(Tree)
        node = tree.cursor_node
        if node is None:
            return
        if expand and node.allow_expand and not node.is_expanded:
            node.expand()
        elif not expand and node.is_expanded:
            node.collapse()
        elif not expand and node.parent is not None and node.parent is not tree.root:
            tree.move_cursor(node.parent)

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        event.stop()
        if event.node.data is not None:
            self.dismiss(event.node.data)


class InputPrompt(ModalScreen[str | None]):
    """input() in a cell: the prompt and a one-line answer at the bottom; escape interrupts the cell."""

    DEFAULT_CSS = """
    InputPrompt {
        align: left bottom;
        background: transparent;
    }
    InputPrompt > Vertical {
        height: auto;
        background: $panel;
    }
    InputPrompt Label {
        padding: 0 1;
    }
    """

    BINDINGS = [Binding("escape", "dismiss(None)", show=False)]

    def __init__(self, prompt: str, password: bool, where: str) -> None:
        super().__init__()
        self.prompt, self.password, self.where = prompt, password, where

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(Text.assemble((f"{self.where} asks for input", "bold"), ("  ·  enter sends · esc interrupts the cell", "dim")))
            yield Input(placeholder=self.prompt.strip() or "input", password=self.password)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.value)
