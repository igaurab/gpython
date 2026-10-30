"""A vim-style file explorer (think netrw / NERDTree) in a sidebar.

Keys: j/k move, h/l collapse/expand (l on a file opens it), enter/o/t open the file
in its own tab (a blank untitled window is reused; an open file's tab is focused),
e opens it in the current window, s/v open in a horizontal/vertical split,
- goes up one directory, C makes the directory under the cursor the root,
R reloads, . shows hidden files, / searches names, % creates a new notebook,
q / escape / ctrl+b close the explorer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from rich.style import Style
from rich.text import Text
from textual import events
from textual.message import Message
from textual.widgets import DirectoryTree
from textual.widgets.tree import TreeNode

from .vim import _is_escape

NOTEBOOK_SUFFIXES = {".py", ".ipynb"}
HIDDEN_DIRS = {"__pycache__", "node_modules", ".git", ".venv", "venv", ".mypy_cache", ".pytest_cache", ".ruff_cache"}


class Explorer(DirectoryTree):
    class Open(Message):
        """Open `path` in the current window ("here"), a "split", a "vsplit" or a "tab"."""

        def __init__(self, path: Path, how: str) -> None:
            super().__init__()
            self.path = path
            self.how = how

    class Closed(Message):
        """q / escape: the app hides the explorer and gives focus back to the notebook."""

    class SearchRequested(Message):
        """'/': the app asks for a name to look for."""

    class NewFile(Message):
        """'%': the app asks for a file name and opens it as a new notebook."""

        def __init__(self, directory: Path) -> None:
            super().__init__()
            self.directory = directory

    # Plain text markers instead of emoji (which render as wide glyphs or boxes in many terminals).
    ICON_NODE = "▸ "
    ICON_NODE_EXPANDED = "▾ "
    ICON_FILE = "  "
    show_hidden = False

    def __init__(self, path: Path, **kwargs) -> None:
        super().__init__(path, id="explorer", **kwargs)
        self.guide_depth = 2
        self.show_root = False  # the root path is shown in the header line above the tree
        self._pending = ""
        self._last_search = ""

    def render_label(self, node: TreeNode, base_style: Style, style: Style) -> Text:
        """`▸ folder` / `  file.py`: no emoji, no extension highlight, folders bold, dotfiles dim."""
        label = node._label.copy()
        label.stylize(style)
        if node._allow_expand:
            prefix = (self.ICON_NODE_EXPANDED if node.is_expanded else self.ICON_NODE, base_style)
            label.stylize_before("bold")
        else:
            prefix = (self.ICON_FILE, base_style)
        if label.plain.startswith("."):
            label.stylize_before("dim")
        return Text.assemble(prefix, label)

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        for path in sorted(paths, key=lambda p: (not p.is_dir(), p.name.lower())):
            name = path.name
            if not self.show_hidden:
                if name.startswith(".") or name in HIDDEN_DIRS or name.endswith(".egg-info"):
                    continue
                if path.is_file() and path.suffix not in NOTEBOOK_SUFFIXES:
                    continue
            yield path

    # ── helpers ──────────────────────────────────────────────────────────

    def _node_path(self, node: TreeNode | None) -> Path | None:
        return None if node is None or node.data is None else node.data.path

    def _open(self, how: str) -> None:
        node = self.cursor_node
        path = self._node_path(node)
        if path is None:
            return
        if path.is_dir():
            if how in ("here", "tab"):
                self._toggle_node(node)
            return
        self.post_message(self.Open(path, how))

    def _collapse_or_parent(self) -> None:
        node = self.cursor_node
        if node is None:
            return
        if node.allow_expand and node.is_expanded and node is not self.root:
            node.collapse()
        elif node.parent is not None and (node.parent is not self.root or self.show_root):
            self.move_cursor(node.parent)
        elif node is self.root or node.parent is self.root:
            self.go_up()

    def _expand_or_open(self) -> None:
        node = self.cursor_node
        path = self._node_path(node)
        if node is None or path is None:
            return
        if path.is_dir():
            if not node.is_expanded:
                node.expand()
        else:
            self.post_message(self.Open(path, "tab"))

    def go_up(self) -> None:
        """Make the parent directory the root (netrw's '-')."""
        parent = Path(self.path).resolve().parent
        if parent != Path(self.path).resolve():
            self.path = parent

    def set_root_to_cursor(self) -> None:
        path = self._node_path(self.cursor_node)
        if path is not None and path.is_dir():
            self.path = path

    def toggle_hidden(self) -> None:
        self.show_hidden = not self.show_hidden
        self.reload()

    def search(self, text: str, start_after_cursor: bool = True) -> bool:
        """Move the cursor to the next visible entry whose name contains `text`."""
        if not text:
            return False
        self._last_search = text
        needle = text.lower()
        lines = self._tree_lines
        if not lines:
            return False
        start = (self.cursor_line + 1) if start_after_cursor else 0
        for i in range(len(lines)):
            line_no = (start + i) % len(lines)
            node = lines[line_no].path[-1]
            path = self._node_path(node)
            if path is not None and needle in path.name.lower():
                self.move_cursor_to_line(line_no)
                return True
        self.notify(f"Not found: {text}", severity="warning")
        return False

    def cursor_directory(self) -> Path:
        """The directory a new file would go into: the one under the cursor, or its parent."""
        path = self._node_path(self.cursor_node)
        if path is None:
            return Path(self.path)
        return path if path.is_dir() else path.parent

    # ── keys ─────────────────────────────────────────────────────────────

    def on_key(self, event: events.Key) -> None:
        key = event.character if event.is_printable and event.character else event.key
        if _is_escape(event):
            key = "escape"
        pending, self._pending = self._pending, ""
        if pending == "g":
            if key == "g":
                self.move_cursor_to_line(0)
            handled = True
        else:
            handled = self._command(key)
        if handled:
            event.stop()
            event.prevent_default()

    def _command(self, key: str) -> bool:
        match key:
            case "j":
                self.action_cursor_down()
            case "k":
                self.action_cursor_up()
            case "g":
                self._pending = "g"
            case "G":
                self.move_cursor_to_line(self.last_line)
            case "h" | "left":
                self._collapse_or_parent()
            case "l" | "right":
                self._expand_or_open()
            case "enter" | "o" | "t":
                self._open("tab")
            case "e":
                self._open("here")
            case "s" | "i":  # i: NERDTree's horizontal split
                self._open("split")
            case "v":
                self._open("vsplit")
            case "-" | "u":
                self.go_up()
            case "C":
                self.set_root_to_cursor()
            case "R":
                self.reload()
            case "." | "I":
                self.toggle_hidden()
            case "/":
                self.post_message(self.SearchRequested())
            case "n":
                self.search(self._last_search)
            case "%":
                self.post_message(self.NewFile(self.cursor_directory()))
            case "q" | "escape" | "ctrl+b":
                self.post_message(self.Closed())
            case "?":
                self.app.action_help()
            case _:
                return False
        return True

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        event.stop()
        self.post_message(self.Open(event.path, "tab"))
