"""Every key gpython understands, in one table.

The help screen (help.py) and the README key table are built from KEYMAP, and a
test checks that every App binding in app.py is listed here.

Modes: "any" works everywhere (App bindings), "cell" is Jupyter-style command
mode (the cell itself is selected), "normal" is vim normal mode inside an editor,
"files" is the file explorer sidebar.
"""

from __future__ import annotations

KEYMAP: list[tuple[str, str, str]] = [
    # ── run (any mode) ──
    ("any", "shift+enter", "Run cell (markdown: render it), then go to the cell below (a new cell only on the last one)"),
    ("any", "ctrl+enter", "Run cell (markdown: render it), stay"),
    ("any", "ctrl+shift+enter", "Run cell, stay"),
    ("any", "ctrl+e", "Run cell, stay (for terminals without ctrl+enter)"),
    ("any", "alt+enter", "Run cell and insert a new cell below"),
    ("any", "f5", "Run all cells"),
    ("any", "ctrl+t", "Pick colorscheme with live preview (type to filter, enter keeps, esc restores; :theme name)"),
    ("any", "f4", "Run stale cells"),
    ("any", "f6", "Python environment: switch the kernel's venv/interpreter, new uv or python venv, install packages (also click the kernel chip; :env :venv :uv :pip)"),
    ("any", "ctrl+k", "Interrupt running code"),
    # ── edit / navigate (any mode) ──
    ("any", "ctrl+n", "New cell below"),
    ("any", "ctrl+shift+n", "New cell above"),
    ("any", "ctrl+d", "Delete cell (u in cell mode undoes)"),
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
    # ── files, windows, tabs (any mode) ──
    ("any", "ctrl+b", "File explorer: open / focus / hide (also :Ex, :e)"),
    ("any", "ctrl+w", "Window prefix (vim): then s split · v vsplit · h j k l move · w next · q close · o only · T to tab · = equal · + - < > resize"),
    ("any", "ctrl+w s / ctrl+w v", "Split this window horizontally / vertically (new untitled notebook; :sp file, :vs file open a file)"),
    ("any", "ctrl+w h / j / k / l", "Focus the window to the left / below / above / right (h from the leftmost window: the explorer)"),
    ("any", "ctrl+w w / p", "Next / previous window"),
    ("any", "ctrl+w q / o", "Close this window (the last one quits) / keep only this window"),
    ("any", "ctrl+w T", "Move this window to a new tab"),
    ("any", "ctrl+w + / - / > / <", "Resize: taller / shorter / wider / narrower (ctrl+w = equalizes)"),
    # ── vim normal mode ──
    ("normal", "escape", "Leave the editor: cell mode"),
    ("normal", "J / K", "Next / previous cell (3J jumps three)"),
    ("normal", "j / k", "Down / up a line; past the edge moves to the next cell"),
    ("normal", "gg / G / 3G", "First / last / third line of this cell"),
    ("normal", "dd", "Delete line, like vim (3dd deletes three; u undoes). To delete the cell: esc, then dd"),
    ("normal", "gJ", "Join lines"),
    ("normal", "/", "Jump: /3 = third cell, /text = next cell containing text"),
    ("normal", ":", "Ex command (:help lists them)"),
    ("normal", "gt / gT / 2gt", "Next / previous / second tab"),
    # ── cell mode ──
    ("cell", "j / k", "Select next / previous cell (counts: 3j)"),
    ("cell", "gg / G / 3G", "Select first / last / third cell"),
    ("cell", "enter", "Edit cell (vim normal mode; markdown: show the source)"),
    ("cell", "i", "Edit cell (insert mode)"),
    ("cell", "a / b", "Insert cell above / below"),
    ("cell", "dd", "Delete cell (3dd deletes three; u brings them back)"),
    ("cell", "u / z", "Undo the last cell action (delete, move, insert, markdown/code)"),
    ("cell", "yy / x", "Copy / cut cell"),
    ("cell", "p / P", "Paste cell below / above"),
    ("cell", "J / K", "Move cell down / up"),
    ("cell", "m / y", "Make cell markdown / code"),
    ("cell", "h", "Hide / show the cell's code (marimo hide_code; :hide, :show)"),
    ("cell", "o", "Collapse / expand output"),
    ("cell", "O", "Clear output"),
    ("cell", "l", "Toggle line numbers"),
    ("cell", "/", "Jump to cell by number or text"),
    ("cell", ":", "Ex command"),
    ("cell", "?", "Help"),
    ("cell", "gt / gT / 2gt", "Next / previous / second tab"),
    ("cell", "escape", "Stay in cell mode, clear pending keys"),
    # ── file explorer ──
    ("files", "j / k / gg / G", "Move through the tree"),
    ("files", "h / l", "Collapse (or go to the parent) / expand a folder; l on a file opens it"),
    ("files", "enter / o", "Open the file in the current window (folder: expand / collapse)"),
    ("files", "s / v", "Open the file in a horizontal / vertical split"),
    ("files", "t", "Open the file in a new tab"),
    ("files", "-", "Go up: the parent folder becomes the root"),
    ("files", "C", "Make the folder under the cursor the root"),
    ("files", "%", "New notebook in the folder under the cursor"),
    ("files", ".", "Show hidden files and non-notebook files"),
    ("files", "/ , n", "Search file names / repeat the search"),
    ("files", "R", "Reload the tree"),
    ("files", "q / escape", "Hide the explorer"),
]

MODE_TITLES = {
    "any": "Everywhere",
    "normal": "Vim normal mode (in the editor)",
    "cell": "Cell mode (esc esc)",
    "files": "File explorer (ctrl+b)",
}


def keys(mode: str | None = None) -> set[str]:
    return {key for m, key, _ in KEYMAP if mode is None or m == mode}
