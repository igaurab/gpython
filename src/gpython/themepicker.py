"""Theme picker with live preview.

Textual's own theme palette only applies a theme once it is *selected*. This picker sets
`app.theme` as soon as a row is highlighted, so the whole notebook restyles while you
browse. `enter` keeps the highlighted theme; `escape` restores the one you started with.
"""

from __future__ import annotations

from rich.table import Table
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option

from .themes import PALETTES, TERMINAL

OTHER_ID = "__other__"


def _row(name: str, dark: bool) -> Table:
    grid = Table.grid(expand=True)
    grid.add_column()
    grid.add_column(justify="right")
    kind = "your terminal's colours" if name == TERMINAL else "dark" if dark else "light"
    grid.add_row(name, Text(kind, style="dim"))
    return grid


class ThemePicker(ModalScreen[str | None]):
    """Bottom panel: a filter Input over an OptionList of themes. Highlight = preview."""

    BINDINGS = [
        Binding("escape", "cancel", show=False),
        Binding("up", "move(-1)", show=False, priority=True),
        Binding("down", "move(1)", show=False, priority=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._original = ""

    def ordered_names(self) -> list[str]:
        """"terminal" (the terminal's own colours), our palettes (dark sorted, then light
        sorted), then the other app themes."""
        themes = self.app.available_themes
        ours = [n for n in PALETTES if n in themes]
        dark = sorted(n for n in ours if themes[n].dark)
        light = sorted(n for n in ours if not themes[n].dark)
        other = sorted(n for n in themes if n not in PALETTES and n != TERMINAL)
        return [TERMINAL] + dark + light + [OTHER_ID] + other

    def compose(self) -> ComposeResult:
        with Vertical(id="theme-picker"):
            yield Input(placeholder="Filter themes…  (enter keep · esc cancel)")
            yield OptionList()

    def on_mount(self) -> None:
        self._original = self.app.theme
        self.app._previewing_theme = True
        self._fill("")
        options = self.query_one(OptionList)
        index = self._index_of(self._original)
        if index is not None:
            options.highlighted = index
            options.scroll_to_highlight()
        self.query_one(Input).focus()

    def _fill(self, query: str) -> None:
        q = query.strip().lower()
        themes = self.app.available_themes
        rows: list[Option] = []
        for name in self.ordered_names():
            if name == OTHER_ID:
                if not q:
                    rows.append(Option(Text("── other ──", style="dim"), id=OTHER_ID, disabled=True))
            elif not q or q in name.lower():
                rows.append(Option(_row(name, themes[name].dark), id=name))
        self.query_one(OptionList).set_options(rows)

    def _index_of(self, name: str) -> int | None:
        options = self.query_one(OptionList)
        for i in range(options.option_count):
            if options.get_option_at_index(i).id == name:
                return i
        return None

    def _preview(self, name: str | None) -> None:
        if name and name != OTHER_ID and name in self.app.available_themes and self.app.theme != name:
            self.app.theme = name

    @on(OptionList.OptionHighlighted)
    def _highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self._preview(event.option.id)

    @on(Input.Changed)
    def _filter(self, event: Input.Changed) -> None:
        self._fill(event.value)
        options = self.query_one(OptionList)
        first = next(
            (i for i in range(options.option_count) if not options.get_option_at_index(i).disabled), None
        )
        options.highlighted = first
        if first is not None:
            self._preview(options.get_option_at_index(first).id)

    def action_move(self, delta: int) -> None:
        options = self.query_one(OptionList)
        options.action_cursor_down() if delta > 0 else options.action_cursor_up()

    def _current(self) -> str | None:
        options = self.query_one(OptionList)
        if options.highlighted is None:
            return None
        name = options.get_option_at_index(options.highlighted).id
        return name if name != OTHER_ID else None

    @on(Input.Submitted)
    def _submit(self, event: Input.Submitted) -> None:
        event.stop()
        self.confirm(self._current())

    @on(OptionList.OptionSelected)
    def _selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.confirm(event.option.id)

    def confirm(self, name: str | None) -> None:
        if not name or name == OTHER_ID:
            self.action_cancel()
            return
        self._preview(name)
        self.app._previewing_theme = False
        self.dismiss(name)

    def action_cancel(self) -> None:
        if self._original and self.app.theme != self._original:
            self.app.theme = self._original
        self.app._previewing_theme = False
        self.dismiss(None)
