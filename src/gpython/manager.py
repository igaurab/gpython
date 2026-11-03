"""Window manager: a floating list of every tab, window and hidden buffer (ctrl+w space, :windows).

Like tmux's choose-tree, but with a live map: the left side is a tree (tabs, the windows in
each tab, then the hidden buffers), the right side draws the selected tab's split layout
with the selected window lit, plus what that notebook is (path, kernel, unsaved changes).

The keys are printed at the bottom of the popup, so nothing needs to be remembered:
enter goes there, s / v put the selected notebook in a split beside the window you came
from (it moves there from another tab, or a hidden buffer is shown), t gives it a tab of
its own, z zooms it, x closes its window (the notebook stays a hidden buffer), d deletes
the buffer (D even when unsaved), J / K move its tab, 1-9 jump to a tab, / filters by name.
Moves and closes keep the popup open, so a layout can be arranged in one go.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.style import Style
from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.color import Color
from textual.containers import Horizontal, Vertical
from textual.geometry import Region
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from .pane import NotebookPane
from .windows import arrange, panes_of

MAP_WIDTH = 44
MAP_HEIGHT = 13

KEY_HELP = [
    [("enter", "go"), ("s / v", "split beside current"), ("t", "own tab"), ("z", "zoom"), ("x", "close window")],
    [("d / D", "delete buffer"), ("J / K", "move tab"), ("1-9", "tab"), ("/", "filter"), ("esc", "close")],
]


@dataclass
class Row:
    kind: str
    tab: int | None = None
    pane: NotebookPane | None = None


def _count(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _kernel_style(state: str) -> str:
    if state == "kernel ready":
        return "green"
    if "fail" in state or "died" in state or state == "no kernel":
        return "red"
    return "yellow"


def build_rows(workspace, query: str = "") -> list[Row]:
    """Tab rows each followed by their windows, then the hidden buffers; `query` keeps the
    notebooks whose name or path contains it (and the tabs that hold them)."""
    q = query.strip().lower()

    def keep(pane: NotebookPane) -> bool:
        return not q or q in pane.title.lower() or q in pane.name.lower()

    rows: list[Row] = []
    for i, tab in enumerate(workspace.tabs):
        windows = [p for p in panes_of(tab.root) if keep(p)]
        if q and not windows:
            continue
        rows.append(Row("tab", i))
        rows.extend(Row("window", i, p) for p in windows)
    hidden = [p for p in workspace.hidden() if keep(p)]
    if hidden:
        rows.append(Row("hidden"))
        rows.extend(Row("buffer", None, p) for p in hidden)
    return rows


def draw_map(tab, selected: NotebookPane | None, here: NotebookPane | None, accent: str, width: int = MAP_WIDTH, height: int = MAP_HEIGHT) -> Text:
    """The tab's split layout as boxes, the selected window drawn in the accent colour."""
    rects: dict[NotebookPane, Region] = {}
    arrange(tab.root, Region(0, 0, width, height), rects)
    chars = [[" "] * width for _ in range(height)]
    styles: list[list[str]] = [[""] * width for _ in range(height)]
    order = sorted(rects, key=lambda p: p is selected)
    for pane in order:
        r = rects[pane]
        if r.width < 2 or r.height < 2:
            continue
        lit = pane is selected
        style = f"bold {accent}" if lit else "dim"
        thick = "┏┓┗┛━┃" if lit else "╭╮╰╯─│"
        x0, y0, x1, y1 = r.x, r.y, r.right - 1, r.bottom - 1
        for x in range(x0, x1 + 1):
            for y in (y0, y1):
                chars[y][x], styles[y][x] = thick[4], style
        for y in range(y0, y1 + 1):
            for x in (x0, x1):
                chars[y][x], styles[y][x] = thick[5], style
        for (x, y), ch in zip(((x0, y0), (x1, y0), (x0, y1), (x1, y1)), thick[:4]):
            chars[y][x], styles[y][x] = ch, style
        inner = r.width - 2
        marks = ("⤢ " if tab.zoom is pane else "") + ("◆ " if pane is here else "")
        lines = [marks + pane.name] + (["unsaved"] if pane.dirty else [])
        top = y0 + max(1, (r.height - len(lines)) // 2)
        for k, label in enumerate(lines):
            y = top + k
            if inner < 1 or y >= y1:
                break
            label = label if len(label) <= inner else label[: max(1, inner - 1)] + "…"
            start = x0 + 1 + (inner - len(label)) // 2
            for j, ch in enumerate(label):
                chars[y][start + j] = ch
                styles[y][start + j] = (f"bold {accent}" if lit else "") if k == 0 else "yellow"
    text = Text(no_wrap=True)
    for y in range(height):
        if y:
            text.append("\n")
        run_style, run = styles[y][0], []
        for x in range(width):
            if styles[y][x] != run_style:
                text.append("".join(run), run_style)
                run_style, run = styles[y][x], []
            run.append(chars[y][x])
        text.append("".join(run), run_style)
    return text


class WindowManager(ModalScreen[None]):
    """The floating window manager; see the module docstring for what each key does."""

    DEFAULT_CSS = """
    WindowManager {
        align: center middle;
        background: transparent;
    }
    #wm {
        width: 92%;
        max-width: 120;
        height: 80%;
        max-height: 34;
        background: $surface;
        border: round $accent;
        border-title-color: $accent;
        border-title-style: bold;
        border-subtitle-color: $text-muted;
        padding: 0 1;
    }
    #wm-body {
        height: 1fr;
    }
    #wm-list {
        width: 1fr;
        height: 1fr;
    }
    #wm-side {
        width: 46;
        height: 1fr;
        padding-left: 1;
        border-left: solid $panel-lighten-2;
    }
    #wm-info {
        height: auto;
        padding-top: 1;
    }
    #wm-filter {
        height: 1;
        border: none;
        padding: 0;
        background: transparent;
        display: none;
    }
    #wm-filter.-on {
        display: block;
    }
    #wm-keys {
        height: 3;
        border-top: solid $panel-lighten-2;
    }
    """

    BINDINGS = [
        Binding("escape", "back", show=False),
        Binding("q", "dismiss(None)", show=False),
        Binding("j,down,ctrl+n", "move(1)", show=False),
        Binding("k,up,ctrl+p", "move(-1)", show=False),
        Binding("g,home", "edge(False)", show=False),
        Binding("G,end", "edge(True)", show=False),
        Binding("enter,l", "go", show=False),
        Binding("s", "split(False)", show=False),
        Binding("v", "split(True)", show=False),
        Binding("t", "own_tab", show=False),
        Binding("z", "zoom", show=False),
        Binding("x", "close_window", show=False),
        Binding("d", "delete(False)", show=False),
        Binding("D", "delete(True)", show=False),
        Binding("J", "move_tab(1)", show=False),
        Binding("K", "move_tab(-1)", show=False),
        Binding("slash", "filter", show=False),
    ] + [Binding(str(n), f"tab({n})", show=False) for n in range(1, 10)]
    APP_ACTIONS_OFF = {
        "add_cell", "interrupt", "command_palette", "delete_cell", "run_cell", "window_prefix",
        "focus_left", "focus_right", "toggle_outline", "focus_cell", "focus_edge", "move_cell",
    }

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[Row] = []
        self.cursor = 0
        self.top = 0
        self.query_text = ""
        self.here: NotebookPane | None = None

    @property
    def workspace(self):
        return self.app.workspace

    def compose(self) -> ComposeResult:
        with Vertical(id="wm"):
            yield Input(id="wm-filter", placeholder="filter by name  (enter keeps it · esc clears)")
            with Horizontal(id="wm-body"):
                yield Static(id="wm-list")
                with Vertical(id="wm-side"):
                    yield Static(id="wm-map")
                    yield Static(id="wm-info")
            yield Static(self._keys(), id="wm-keys")

    def on_mount(self) -> None:
        self.query_one("#wm-filter", Input).can_focus = False
        self.set_focus(None)
        self.here = self.app.pane
        self.query_one("#wm").border_title = " Windows "
        self.rebuild(select=self.here)

    def _accent(self) -> str:
        """The theme's accent as a Rich colour name (Textual's "ansi_green" becomes "color(2)")."""
        try:
            return Color.parse(self.app.current_theme.accent).rich_color.name
        except Exception:
            return "cyan"

    def _keys(self) -> Text:
        text = Text(no_wrap=True, overflow="ellipsis")
        for i, line in enumerate(KEY_HELP):
            if i:
                text.append("\n")
            for j, (key, what) in enumerate(line):
                if j:
                    text.append("  ·  ", "dim")
                text.append(key, "bold")
                text.append(f" {what}", "dim")
        return text

    def rebuild(self, select: NotebookPane | None = None, select_tab: int | None = None) -> None:
        """Re-read the workspace (after a move or close) and keep the cursor on `select`."""
        if self.here not in self.workspace.panes():
            self.here = self.workspace.active
        current = self.selected
        self.rows = build_rows(self.workspace, self.query_text)
        target = None
        for i, row in enumerate(self.rows):
            if select is not None and row.pane is select:
                target = i
                break
            if select is None and select_tab is not None and row.kind == "tab" and row.tab == select_tab:
                target = i
                break
        if target is None and current is not None:
            target = next((i for i, r in enumerate(self.rows) if r == current), None)
        if target is None:
            target = min(self.cursor, len(self.rows) - 1)
        self.cursor = max(0, target)
        if self.rows and self.rows[self.cursor].kind == "hidden":
            self.cursor = min(self.cursor + 1, len(self.rows) - 1)
        self.paint()

    @property
    def selected(self) -> Row | None:
        return self.rows[self.cursor] if 0 <= self.cursor < len(self.rows) else None

    def paint(self) -> None:
        tabs = self.workspace.tabs
        n_windows = len(self.workspace.panes())
        n_hidden = len(self.workspace.hidden())
        self.query_one("#wm").border_subtitle = f" {_count(len(tabs), 'tab')} · {_count(n_windows, 'window')} · {n_hidden} hidden "
        listing = self.query_one("#wm-list", Static)
        height = max(1, listing.size.height or 20)
        if self.cursor < self.top:
            self.top = self.cursor
        elif self.cursor >= self.top + height:
            self.top = self.cursor - height + 1
        self.top = max(0, min(self.top, max(0, len(self.rows) - height)))
        text = Text(no_wrap=True, overflow="ellipsis")
        for i in range(self.top, min(len(self.rows), self.top + height)):
            if i > self.top:
                text.append("\n")
            text.append_text(self._row_text(i))
        if not self.rows:
            text = Text("nothing matches the filter", "dim italic")
        listing.update(text)
        self._paint_side()

    def _row_text(self, i: int) -> Text:
        row = self.rows[i]
        accent = self._accent()
        lit = i == self.cursor
        line = Text(no_wrap=True, overflow="ellipsis")
        line.append("▌ " if lit else "  ", f"bold {accent}")
        workspace = self.workspace
        if row.kind == "tab":
            tab = workspace.tabs[row.tab]
            count = len(panes_of(tab.root))
            line.append(f"Tab {row.tab + 1}", f"bold {accent}" if row.tab == workspace.index else "bold")
            line.append(f"  {count} window{'s' if count != 1 else ''}", "dim")
            if tab.zoom is not None:
                line.append("  ⤢ zoomed", "bold")
            if row.tab == workspace.index:
                line.append("  · current tab", "dim italic")
        elif row.kind == "hidden":
            line.append("Hidden buffers", "bold dim")
            line.append("  open, but in no window", "dim italic")
        else:
            nxt = self.rows[i + 1] if i + 1 < len(self.rows) else None
            last = nxt is None or nxt.kind != row.kind or nxt.tab != row.tab
            line.append(" └─ " if last else " ├─ ", "dim")
            pane = row.pane
            line.append("● ", _kernel_style(pane.kernel_state))
            line.append(pane.name, "bold" if lit or pane is self.here else "")
            if pane.dirty:
                line.append(" •", "bold yellow")
            line.append(f"  #{workspace.number(pane)}", "dim")
            if pane is self.here:
                line.append("  ◆ you were here", f"italic {accent}")
            elif row.kind == "window" and workspace.tabs[row.tab].active is pane and len(panes_of(workspace.tabs[row.tab].root)) > 1:
                line.append("  active", "dim italic")
        if lit:
            line.stylize("reverse" if row.kind != "hidden" else "")
            line.stylize(Style(reverse=False), 0, 2)
        return line

    def _paint_side(self) -> None:
        row = self.selected
        mapping = self.query_one("#wm-map", Static)
        info = self.query_one("#wm-info", Static)
        if row is None or row.kind == "hidden":
            mapping.update("")
            info.update("")
            return
        accent = self._accent()
        if row.kind in ("tab", "window"):
            tab = self.workspace.tabs[row.tab]
            focus = row.pane if row.kind == "window" else (tab.active if tab.active in panes_of(tab.root) else None)
            mapping.update(draw_map(tab, focus, self.here, accent))
        else:
            mapping.update(Text("\n\n   hidden buffer\n\n   not shown in any window:\n   enter shows it here,\n   s / v beside the current window,\n   t in a tab of its own", "dim"))
        if row.kind == "tab":
            tab = self.workspace.tabs[row.tab]
            names = ", ".join(p.name for p in panes_of(tab.root))
            info.update(Text.assemble(("Tab ", "bold"), (f"{row.tab + 1}", "bold"), "\n", (names, "dim")))
            return
        pane = row.pane
        where = str(pane.path) if pane.path else "untitled (not saved yet)"
        info.update(
            Text.assemble(
                (pane.name, "bold"),
                ("  unsaved changes" if pane.dirty else "", "bold yellow"),
                "\n",
                (where, "dim"),
                "\n",
                ("● ", _kernel_style(pane.kernel_state)),
                (f"{pane.kernel_label} · {pane.kernel_state}", "dim"),
            )
        )

    def on_resize(self, event: events.Resize) -> None:
        self.paint()

    def action_move(self, step: int) -> None:
        if not self.rows:
            return
        i = self.cursor
        for _ in range(len(self.rows)):
            i = max(0, min(len(self.rows) - 1, i + step))
            if self.rows[i].kind != "hidden":
                break
        self.cursor = i
        self.paint()

    def action_edge(self, end: bool) -> None:
        if self.rows:
            self.cursor = len(self.rows) - 1 if end else 0
            self.paint()

    def on_click(self, event: events.Click) -> None:
        listing = self.query_one("#wm-list", Static)
        offset = event.get_content_offset(listing)
        if offset is None:
            return
        i = self.top + offset.y
        if 0 <= i < len(self.rows) and self.rows[i].kind != "hidden":
            if i == self.cursor or event.chain > 1:
                self.cursor = i
                self.run_worker(self.action_go())
            else:
                self.cursor = i
                self.paint()

    def action_back(self) -> None:
        field = self.query_one("#wm-filter", Input)
        if field.has_focus or self.query_text:
            self.query_text = ""
            field.value = ""
            field.remove_class("-on")
            field.can_focus = False
            self.set_focus(None)
            self.rebuild()
            return
        self.dismiss(None)

    def action_filter(self) -> None:
        field = self.query_one("#wm-filter", Input)
        field.add_class("-on")
        field.can_focus = True
        field.focus()

    @on(Input.Changed, "#wm-filter")
    def _filtered(self, event: Input.Changed) -> None:
        self.query_text = event.value
        first = next((r.pane for r in build_rows(self.workspace, event.value) if r.pane is not None), None)
        self.cursor = 0
        self.rebuild(select=first)

    @on(Input.Submitted, "#wm-filter")
    def _filter_done(self, event: Input.Submitted) -> None:
        event.stop()
        event.input.can_focus = False
        self.set_focus(None)

    def _finish(self, pane: NotebookPane | None = None) -> None:
        """Close the popup and put the keyboard in `pane` (default: the active window)."""
        app = self.app
        self.dismiss(None)
        target = pane if pane is not None and pane in app.workspace.panes() else app.pane
        app.call_after_refresh(target.focus_pane)

    async def action_go(self) -> None:
        row = self.selected
        if row is None:
            return
        workspace = self.workspace
        if row.kind == "tab":
            workspace.switch(row.tab)
            self._finish()
        elif row.kind == "window":
            if workspace.tab and workspace.tab.zoom not in (None, row.pane) and workspace.tab_of(row.pane) == workspace.index:
                workspace.unzoom()
            workspace.set_active(row.pane)
            self._finish(row.pane)
        elif row.kind == "buffer":
            await workspace.show(row.pane, self.here)
            self._finish(row.pane)

    def action_tab(self, n: int) -> None:
        if n <= len(self.workspace.tabs):
            self.workspace.switch(n - 1)
            self._finish()

    def _pane_row(self) -> Row | None:
        row = self.selected
        if row is None or row.pane is None:
            self.app.notify("Select a window or a buffer first (j / k)", timeout=2)
            return None
        return row

    async def action_split(self, side_by_side: bool) -> None:
        row = self._pane_row()
        if row is None:
            return
        if row.pane is self.here:
            self.app.notify("That is the window you came from: pick another notebook to put beside it", timeout=3)
            return
        if await self.workspace.place(row.pane, self.here, side_by_side):
            self._finish(row.pane)

    async def action_own_tab(self) -> None:
        row = self._pane_row()
        if row is None:
            return
        workspace = self.workspace
        if row.kind == "buffer":
            await workspace.add(row.pane, "tab")
        elif len(panes_of(workspace.tabs[row.tab].root)) < 2:
            self.app.notify(f"{row.pane.name} already has a tab of its own", timeout=2)
            return
        else:
            await workspace.to_new_tab(row.pane)
        self.rebuild(select=row.pane)

    def action_zoom(self) -> None:
        row = self.selected
        if row is None or row.kind != "window":
            self.app.notify("Zoom works on a window (a notebook inside a tab)", timeout=2)
            return
        if len(panes_of(self.workspace.tabs[row.tab].root)) < 2:
            self.app.notify(f"{row.pane.name} is alone in its tab: it already fills it", timeout=2)
            return
        self.workspace.toggle_zoom(row.pane)
        self._finish(row.pane)

    async def action_close_window(self) -> None:
        row = self.selected
        workspace = self.workspace
        if row is None or row.kind in ("hidden", "buffer"):
            self.app.notify("A hidden buffer has no window to close (d deletes it)", timeout=2)
            return
        targets = [row.pane] if row.kind == "window" else panes_of(workspace.tabs[row.tab].root)
        if len(targets) >= len(workspace.panes()):
            self.app.notify("That would close the last window (use :q to quit)", timeout=2)
            return
        for pane in targets:
            await workspace.close(pane)
        self.rebuild(select=next((p for p in workspace.panes() if p is self.here), None))

    async def action_delete(self, force: bool) -> None:
        row = self._pane_row()
        if row is None:
            return
        pane = row.pane
        if pane.dirty and not force:
            self.app.notify(f"{pane.name} has unsaved changes: D deletes it anyway", severity="warning", timeout=3)
            return
        workspace = self.workspace
        nxt = await workspace.delete(pane)
        if nxt is None:
            await self.app.new_pane("tab")
        self.rebuild()

    def action_move_tab(self, step: int) -> None:
        row = self.selected
        if row is None or row.tab is None:
            return
        to = self.workspace.move_tab(row.tab, row.tab + step)
        if row.kind == "tab":
            self.rebuild(select_tab=to)
        else:
            self.rebuild(select=row.pane)
