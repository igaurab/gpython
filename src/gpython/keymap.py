"""Every key gpython understands, in one table.

The help screen (help.py) and the README key table are built from KEYMAP, and a
test checks that every App binding in app.py is listed here.

Modes: "any" works everywhere (App bindings), "cell" is Jupyter-style command
mode (the cell itself is selected), "normal" is vim normal mode inside an editor.
"""

from __future__ import annotations

KEYMAP: list[tuple[str, str, str]] = [
    # ── run (any mode) ──
    ("any", "shift+enter", "Run cell, then go to the cell below (a new cell only on the last one)"),
    ("any", "ctrl+enter", "Run cell, stay"),
    ("any", "ctrl+shift+enter", "Run cell, stay"),
    ("any", "ctrl+e", "Run cell, stay (for terminals without ctrl+enter)"),
    ("any", "alt+enter", "Run cell and insert a new cell below"),
    ("any", "f5", "Run all cells"),
    ("any", "ctrl+t", "Pick colorscheme with live preview (type to filter, enter keeps, esc restores; :theme name)"),
    ("any", "f4", "Run stale cells"),
    ("any", "ctrl+k", "Interrupt running code"),
    # ── edit / navigate (any mode) ──
    ("any", "ctrl+n", "New cell below"),
    ("any", "ctrl+shift+n", "New cell above"),
    ("any", "ctrl+d", "Delete cell (z in cell mode undoes)"),
    ("any", "ctrl+s", "Save"),
    ("any", "alt+up", "Previous cell"),
    ("any", "ctrl+up", "Previous cell"),
    ("any", "alt+down", "Next cell"),
    ("any", "ctrl+down", "Next cell"),
    ("any", "ctrl+home", "First cell"),
    ("any", "ctrl+end", "Last cell"),
    ("any", "alt+shift+up", "Move cell up"),
    ("any", "alt+shift+down", "Move cell down"),
    ("any", "f1", "Help (this screen)"),
    ("any", "f2", "Toggle variables panel"),
    ("any", "f3", "Toggle lazy mode"),
    ("any", "ctrl+p", "Command palette"),
    ("any", "ctrl+q", "Quit (asks if unsaved)"),
    # ── vim normal mode ──
    ("normal", "escape", "Leave the editor: cell mode"),
    ("normal", "J / K", "Next / previous cell (3J jumps three)"),
    ("normal", "j / k", "Down / up a line; past the edge moves to the next cell"),
    ("normal", "gg / G / 3G", "First / last / third line of this cell"),
    ("normal", "dd", "Delete this cell (3dd deletes three; 0D clears a line)"),
    ("normal", "gJ", "Join lines"),
    ("normal", "/", "Jump: /3 = third cell, /text = next cell containing text"),
    ("normal", ":", "Ex command (:help lists them)"),
    # ── cell mode ──
    ("cell", "j / k", "Select next / previous cell (counts: 3j)"),
    ("cell", "gg / G / 3G", "Select first / last / third cell"),
    ("cell", "enter", "Edit cell (vim normal mode)"),
    ("cell", "i", "Edit cell (insert mode)"),
    ("cell", "a / b", "Insert cell above / below"),
    ("cell", "dd", "Delete cell"),
    ("cell", "z", "Undo cell deletion"),
    ("cell", "yy / x", "Copy / cut cell"),
    ("cell", "p / P", "Paste cell below / above"),
    ("cell", "J / K", "Move cell down / up"),
    ("cell", "m / y", "Make cell markdown / code"),
    ("cell", "o", "Collapse / expand output"),
    ("cell", "O", "Clear output"),
    ("cell", "l", "Toggle line numbers"),
    ("cell", "/", "Jump to cell by number or text"),
    ("cell", ":", "Ex command"),
    ("cell", "?", "Help"),
    ("cell", "escape", "Stay in cell mode, clear pending keys"),
]

MODE_TITLES = {"any": "Everywhere", "normal": "Vim normal mode (in the editor)", "cell": "Cell mode (esc esc)"}


def keys(mode: str | None = None) -> set[str]:
    return {key for m, key, _ in KEYMAP if mode is None or m == mode}
