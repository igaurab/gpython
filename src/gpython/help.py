"""The f1 / ? help screen, built from keymap.KEYMAP (one source of truth)."""

from __future__ import annotations

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from .keymap import KEYMAP, MODE_TITLES


def help_renderable(commands: list[tuple[str, str]] = ()) -> Table:
    """A two-column table: keys per mode, then `:` commands (name, description)."""
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold", no_wrap=True)
    table.add_column()
    for mode, title in MODE_TITLES.items():
        table.add_row(Text(title, style="bold underline"), "")
        for m, key, desc in KEYMAP:
            if m == mode:
                table.add_row(key, desc)
        table.add_row("", "")
    if commands:
        table.add_row(Text("Ex commands", style="bold underline"), "")
        for name, desc in commands:
            table.add_row(f":{name}", desc)
    return table


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape,q,f1,question_mark", "dismiss(None)", "Close", show=False)]

    def __init__(self, commands: list[tuple[str, str]] = ()) -> None:
        super().__init__()
        self.commands = list(commands)

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="help"):
            yield Static(help_renderable(self.commands))
