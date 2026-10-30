"""A TextArea with vim-style modal editing and kernel-backed Tab completion.

Supported in normal mode: h j k l w b e 0 ^ $ gg G, counts, i a I A o O,
x X s S dd dw d$ D cc cw c$ C yy yw p P J u ctrl+r, ":" for ex commands.
j/k past the first/last line moves to the neighbouring cell.
"""

from __future__ import annotations

import dataclasses
import os
import re

from rich.style import Style
from textual import events
from textual.message import Message
from textual.widgets import OptionList, TextArea
from textual.widgets.text_area import Location, TextAreaTheme

_WORD = re.compile(r"\w+|[^\w\s]+")


class CompletionPopup(OptionList):
    DEFAULT_CSS = """
    CompletionPopup {
        position: absolute;
        layer: overlay;
        width: auto;
        max-width: 60;
        height: auto;
        max-height: 10;
        border: round $accent;
        background: $surface;
        padding: 0;
    }
    """


class VimTextArea(TextArea):
    enabled = True  # set False for plain (insert-only) editing
    register = ""  # shared yank buffer
    register_linewise = False

    class ModeChanged(Message):
        def __init__(self, editor: VimTextArea, mode: str) -> None:
            super().__init__()
            self.editor = editor
            self.mode = mode

    class EdgeReached(Message):
        """j/k pressed on the last/first line: the app moves to another cell."""

        def __init__(self, editor: VimTextArea, step: int) -> None:
            super().__init__()
            self.editor = editor
            self.step = step

    class ExCommand(Message):
        def __init__(self, editor: VimTextArea) -> None:
            super().__init__()
            self.editor = editor

    def __init__(self, text: str = "", *, mode: str = "normal", **kwargs) -> None:
        super().__init__(
            text,
            language="python",
            theme="monokai",
            soft_wrap=False,
            tab_behavior="indent",
            show_line_numbers=True,
            **kwargs,
        )
        self.mode = mode if self.enabled else "insert"
        self.pending = ""
        self.count = ""
        self._popup: CompletionPopup | None = None
        self._popup_start: Location = (0, 0)

    def on_mount(self) -> None:
        base = TextAreaTheme.get_builtin_theme("monokai")
        normal = dataclasses.replace(
            base, name="monokai-normal", cursor_style=Style(color="#272822", bgcolor="#e6db74", bold=True)
        )
        self.register_theme(normal)
        self.set_mode(self.mode)

    # ── mode ─────────────────────────────────────────────────────────────

    def set_mode(self, mode: str) -> None:
        if not self.enabled:
            mode = "insert"
        self.mode = mode
        self.pending = self.count = ""
        self.theme = "monokai" if mode == "insert" else "monokai-normal"
        self.cursor_blink = mode == "insert"
        self.post_message(self.ModeChanged(self, mode))

    # ── keys ─────────────────────────────────────────────────────────────

    async def _on_key(self, event: events.Key) -> None:
        if self._popup is not None and self._popup_key(event):
            event.stop()
            event.prevent_default()
            return
        if self.mode == "insert":
            if event.key == "escape" and self.enabled:
                event.stop()
                event.prevent_default()
                if not self.cursor_at_start_of_line:
                    self.action_cursor_left()
                self.set_mode("normal")
            elif event.key == "tab" and self._word_before_cursor():
                event.stop()
                event.prevent_default()
                self.run_worker(self._complete(), exclusive=True, group="complete")
            else:
                await super()._on_key(event)
            return
        key = event.character if event.is_printable and event.character else event.key
        # Unhandled non-printable keys (f-keys, ctrl combos) still reach app bindings.
        if self._normal(key) or event.is_printable:
            event.stop()
            event.prevent_default()

    def _normal(self, key: str) -> bool:
        """Handle one normal-mode key. Returns False if the key means nothing here."""
        if key == "escape":
            self.pending = self.count = ""
            return True
        if key.isdigit() and (key != "0" or self.count):
            self.count += key
            return True
        n = int(self.count or 1)
        pending, self.pending, self.count = self.pending, "", ""

        if pending in ("d", "c", "y"):
            self._operator(pending, key, n)
            return True
        if pending == "g":
            if key == "g":
                self.move_cursor((0, 0))
            return True

        row, col = self.cursor_location
        line = self.document.get_line(row)
        match key:
            case "d" | "c" | "y" | "g":
                self.pending, self.count = key, str(n) if n > 1 else ""
            case "i":
                self.set_mode("insert")
            case "a":
                if col < len(line):
                    self.move_cursor((row, col + 1))
                self.set_mode("insert")
            case "I":
                self.move_cursor((row, len(line) - len(line.lstrip())))
                self.set_mode("insert")
            case "A":
                self.move_cursor((row, len(line)))
                self.set_mode("insert")
            case "o" | "O":
                indent = line[: len(line) - len(line.lstrip())]
                if key == "o":
                    self.insert("\n" + indent, (row, len(line)), maintain_selection_offset=False)
                else:
                    self.insert(indent + "\n", (row, 0), maintain_selection_offset=False)
                    self.move_cursor((row, len(indent)))
                self.set_mode("insert")
            case "x":
                if line:
                    self.delete((row, col), (row, min(col + n, len(line))), maintain_selection_offset=False)
                    self._clamp()
            case "X":
                if col:
                    self.delete((row, max(0, col - n)), (row, col), maintain_selection_offset=False)
            case "s":
                self.delete((row, col), (row, min(col + n, len(line))), maintain_selection_offset=False)
                self.set_mode("insert")
            case "S":
                self._operator("c", "c", n)
            case "D":
                self._operator("d", "$", n)
            case "C":
                self._operator("c", "$", n)
            case "p" | "P":
                self._paste(key == "p", n)
            case "J":
                for _ in range(n):
                    row, _ = self.cursor_location
                    if row + 1 < self.document.line_count:
                        nxt = self.document.get_line(row + 1)
                        cur = self.document.get_line(row)
                        sep = " " if cur and nxt.strip() else ""
                        self.replace(sep + nxt.lstrip(), (row, len(cur)), (row + 1, len(nxt)))
                        self.move_cursor((row, len(cur)))
            case "u":
                self.undo()
            case "ctrl+r":
                self.redo()
            case ":":
                self.post_message(self.ExCommand(self))
            case _:
                if key in ("j", "down") and self.cursor_at_last_line:
                    self.post_message(self.EdgeReached(self, 1))
                elif key in ("k", "up") and self.cursor_at_first_line:
                    self.post_message(self.EdgeReached(self, -1))
                elif self._move(key, n) is None:
                    return False
                else:
                    self._clamp()
        return True

    def _clamp(self) -> None:
        """In normal mode the cursor sits on a character, not after the last one."""
        row, col = self.cursor_location
        length = len(self.document.get_line(row))
        if col >= length > 0:
            self.move_cursor((row, length - 1))

    # ── motions ──────────────────────────────────────────────────────────

    def _move(self, key: str, n: int) -> bool | None:
        """Move the cursor; return True for linewise motions, None if not a motion."""
        row, col = self.cursor_location
        line = self.document.get_line(row)
        last = self.document.line_count - 1
        match key:
            case "h" | "left" | "backspace":
                self.move_cursor((row, max(0, col - n)))
            case "l" | "right" | "space":
                self.move_cursor((row, min(len(line), col + n)))
            case "j" | "down":
                self.move_cursor((min(last, row + n), col))
                return True
            case "k" | "up":
                self.move_cursor((max(0, row - n), col))
                return True
            case "0" | "home":
                self.move_cursor((row, 0))
            case "^":
                self.move_cursor((row, len(line) - len(line.lstrip())))
            case "$" | "end":
                self.move_cursor((row, len(line)))
            case "w":
                for _ in range(n):
                    self.move_cursor(self._word_start(1))
            case "b":
                for _ in range(n):
                    self.move_cursor(self._word_start(-1))
            case "e":
                for _ in range(n):
                    self.move_cursor(self._word_end())
            case "G":
                self.move_cursor((last if n == 1 else min(last, n - 1), 0))
                return True
            case _:
                return None
        return False

    def _word_start(self, direction: int) -> Location:
        """vim `w` (direction 1) / `b` (direction -1): next/previous word start; empty lines count."""
        row, col = self.cursor_location
        last = self.document.line_count - 1
        if direction > 0:
            while row <= last:
                line = self.document.get_line(row)
                starts = [m.start() for m in _WORD.finditer(line)]
                if (row, col) != self.cursor_location and not line.strip():
                    return (row, 0)
                for start in starts:
                    if (row, start) > self.cursor_location:
                        return (row, start)
                row, col = row + 1, -1
            return self.document.end
        while row >= 0:
            line = self.document.get_line(row)
            starts = [m.start() for m in _WORD.finditer(line)]
            for start in reversed(starts):
                if (row, start) < self.cursor_location:
                    return (row, start)
            if row != self.cursor_location[0] and not line.strip():
                return (row, 0)
            row -= 1
        return (0, 0)

    def _word_end(self) -> Location:
        row, col = self.cursor_location
        while row < self.document.line_count:
            line = self.document.get_line(row)
            for m in _WORD.finditer(line):
                if m.end() - 1 > col:
                    return (row, m.end() - 1)
            row, col = row + 1, -1
        return self.document.end

    # ── operators ────────────────────────────────────────────────────────

    def _operator(self, op: str, key: str, n: int) -> None:
        start = row, col = self.cursor_location
        last = self.document.line_count - 1

        if key == op:  # dd / cc / yy
            end_row = min(last, row + n - 1)
            self._yank_lines(row, end_row)
            if op == "y":
                return
            if op == "c":
                indent = re.match(r"\s*", self.document.get_line(row)).group()
                self.replace(indent, (row, 0), (end_row, len(self.document.get_line(end_row))), maintain_selection_offset=False)
                self.set_mode("insert")
                return
            if end_row < last:
                self.delete((row, 0), (end_row + 1, 0), maintain_selection_offset=False)
            elif row > 0:
                self.delete((row - 1, len(self.document.get_line(row - 1))), (end_row, len(self.document.get_line(end_row))), maintain_selection_offset=False)
                self.move_cursor((row - 1, 0))
            else:
                self.delete((0, 0), self.document.end, maintain_selection_offset=False)
            self._clamp()
            return

        linewise = self._move(key, n)
        if linewise is None:
            self.move_cursor(start)
            return
        end = self.cursor_location
        if linewise:
            self.move_cursor((min(start[0], end[0]), 0))
            self._operator(op, op, abs(end[0] - start[0]) + 1)
            return
        if op == "c" and key == "w":  # cw behaves like ce
            self.move_cursor(start)
            end = self._word_end()
            end = (end[0], end[1] + 1)
        a, b = sorted([start, end])
        VimTextArea.register, VimTextArea.register_linewise = self.get_text_range(a, b), False
        if op == "y":
            self.move_cursor(start)
            return
        self.delete(a, b, maintain_selection_offset=False)
        if op == "c":
            self.set_mode("insert")
        else:
            self._clamp()

    def _yank_lines(self, first: int, last: int) -> None:
        text = self.get_text_range((first, 0), (last, len(self.document.get_line(last))))
        VimTextArea.register, VimTextArea.register_linewise = text, True

    def _paste(self, after: bool, n: int) -> None:
        text = VimTextArea.register
        if not text:
            return
        row, col = self.cursor_location
        line = self.document.get_line(row)
        if VimTextArea.register_linewise:
            block = "\n".join([text] * n)
            if after:
                self.insert("\n" + block, (row, len(line)), maintain_selection_offset=False)
                self.move_cursor((row + 1, 0))
            else:
                self.insert(block + "\n", (row, 0), maintain_selection_offset=False)
                self.move_cursor((row, 0))
        else:
            at = (row, min(len(line), col + 1)) if after and line else (row, col)
            self.insert(text * n, at, maintain_selection_offset=False)
            self._clamp()

    # ── completion ───────────────────────────────────────────────────────

    def _word_before_cursor(self) -> bool:
        row, col = self.cursor_location
        return bool(self.document.get_line(row)[:col].strip())

    def _offset(self, location: Location) -> int:
        row, col = location
        return sum(len(self.document.get_line(r)) + 1 for r in range(row)) + col

    def _location(self, offset: int) -> Location:
        for row in range(self.document.line_count):
            length = len(self.document.get_line(row))
            if offset <= length:
                return (row, offset)
            offset -= length + 1
        return self.document.end

    async def _complete(self) -> None:
        kernel = getattr(self.app, "kernel", None)
        if kernel is None or not kernel.ready.is_set():
            return
        code = self.text
        cursor = self.cursor_location
        matches, start, end = await kernel.complete(code, self._offset(cursor))
        if self.cursor_location != cursor or not matches:
            return
        typed = code[start : self._offset(cursor)]
        prefix = os.path.commonprefix(matches)
        start_loc = self._location(start)
        if len(matches) == 1 or len(prefix) > len(typed):
            self.replace(prefix, start_loc, self._location(end), maintain_selection_offset=False)
        if len(matches) > 1:
            self._show_popup(matches, start_loc)

    def _show_popup(self, matches: list[str], start: Location) -> None:
        self._close_popup()
        popup = CompletionPopup(*matches[:200])
        self._popup, self._popup_start = popup, start
        x, y = self.cursor_screen_offset
        popup.styles.offset = (max(0, x - (self.cursor_location[1] - start[1]) - 1), y + 1)
        self.screen.mount(popup)
        popup.highlighted = 0

    def _close_popup(self) -> None:
        if self._popup is not None:
            self._popup.remove()
            self._popup = None

    def _popup_key(self, event: events.Key) -> bool:
        popup = self._popup
        match event.key:
            case "down" | "tab" | "ctrl+n":
                popup.action_cursor_down()
            case "up" | "shift+tab" | "ctrl+p":
                popup.action_cursor_up()
            case "enter":
                index = popup.highlighted or 0
                self.replace(str(popup.get_option_at_index(index).prompt), self._popup_start, self.cursor_location, maintain_selection_offset=False)
                self._close_popup()
            case "escape":
                self._close_popup()
            case _:
                self._close_popup()
                return False
        return True

    def _on_blur(self, event: events.Blur) -> None:
        self._close_popup()
        super()._on_blur(event)
