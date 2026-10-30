"""A TextArea with vim-style modal editing and kernel-backed Tab completion.

Supported in normal mode: h j k l w b e 0 ^ $ gg G, counts, i a I A o O,
x X s S dd dw d$ dj D cc cw c$ C yy yw p P gJ u ctrl+r, ":" for ex commands.
dd deletes lines, as in vim; cells are deleted in cell mode (esc esc, then dd),
where u brings them back. Cell-level keys are posted as messages for the app:
j/k past the first/last line and J/K move to a neighbouring cell (EdgeReached),
"/" asks for a jump (SearchRequested), escape with nothing pending leaves the
editor (LeaveEditor).
"""

from __future__ import annotations

import dataclasses
import functools
import os
import re

from rich.style import Style
from textual import events
from textual._tree_sitter import TREE_SITTER, get_language
from textual.document._document_navigator import DocumentNavigator
from textual.document._syntax_aware_document import SyntaxAwareDocument
from textual.document._wrapped_document import WrappedDocument
from textual.geometry import Size
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import OptionList, TextArea
from textual.widgets.text_area import Edit, EditResult, Location, Selection, TextAreaTheme

_WORD = re.compile(r"\w+|[^\w\s]+")
_ESCAPE_KEYS = {"escape", "ctrl+left_square_bracket", "ctrl+left_square_brace"}
_IDENT = re.compile(r"[\w.]$")
_DEDENT_AFTER = ("return", "break", "continue", "pass", "raise")
AUTOCOMPLETE_DELAY = 0.12  # seconds after the last keystroke
INDENT = "    "


MONOKAI = TextAreaTheme.get_builtin_theme("monokai")
MONOKAI_NORMAL = dataclasses.replace(  # normal mode: a yellow block cursor
    MONOKAI, name="monokai-normal", cursor_style=Style(color="#272822", bgcolor="#e6db74", bold=True)
)


@functools.cache
def _highlight_query(language: str):
    """The compiled tree-sitter highlight query for a builtin language, shared by all editors.

    Textual compiles it again for every TextArea (about 15 ms for Python), which made
    opening a 60-cell notebook take seconds. Returns None when the language is unknown.
    """
    if not TREE_SITTER or (lang := get_language(language)) is None:
        return None
    from tree_sitter import Query

    return Query(lang, TextArea._get_builtin_highlight_query(language))


def _is_escape(event: events.Key) -> bool:
    """escape, or ctrl+[ (the same byte in old terminals, its own key in new ones)."""
    return bool(_ESCAPE_KEYS & {event.key, *event.aliases})


def indent_after(line: str) -> str:
    """Indentation for the line typed after `line`, following Python's rules of thumb."""
    current = line[: len(line) - len(line.lstrip())]
    code = line.split("#", 1)[0].rstrip()
    if code.endswith((":", "(", "[", "{")):
        return current + INDENT
    if code.lstrip().split(" ")[0] in _DEDENT_AFTER:
        return current[: max(0, len(current) - len(INDENT))]
    return current


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
    # Textual repaints the whole editor whenever the cursor moves (the reactive has
    # repaint=True). Here a cursor move or an edit repaints only the lines involved; see
    # _watch_selection and edit. A multi-line change or a selection still repaints all.
    selection: reactive[Selection] = reactive(Selection(), init=False, always_update=True, repaint=False)

    enabled = True  # set False for plain (insert-only) editing
    line_numbers = True  # gutter on new editors (the app toggles existing ones)
    mode = "normal"  # one mode for the whole app, like a single vim buffer
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

    class SearchRequested(Message):
        """'/' in normal mode: the app asks where to jump."""

        def __init__(self, editor: VimTextArea) -> None:
            super().__init__()
            self.editor = editor

    class LeaveEditor(Message):
        """escape in normal mode with nothing pending: the cell takes focus (cell mode)."""

        def __init__(self, editor: VimTextArea) -> None:
            super().__init__()
            self.editor = editor


    class TabSwitch(Message):
        """gt / gT: go to tab `tab` (1-based) when given, else `step` tabs forward."""

        def __init__(self, editor: VimTextArea, tab: int | None, step: int) -> None:
            super().__init__()
            self.editor = editor
            self.tab = tab
            self.step = step

    def __init__(self, text: str = "", language: str | None = "python", **kwargs) -> None:
        # The language is set after construction so the document is only built once.
        super().__init__(
            text,
            theme="monokai",
            soft_wrap=False,
            tab_behavior="indent",
            show_line_numbers=kwargs.pop("show_line_numbers", self.line_numbers),
            **kwargs,
        )
        self.pending = ""
        self.count = ""
        self._popup: CompletionPopup | None = None
        self._popup_start: Location = (0, 0)
        self._auto_timer = None
        self._theme_names = ("monokai", "monokai-normal")
        self.set_editor_theme(MONOKAI, MONOKAI_NORMAL)
        if language:
            self.language = language
        if not self.enabled:
            VimTextArea.mode = "insert"

    def on_mount(self) -> None:
        pair = getattr(self.app, "editor_theme_pair", None)
        if pair is not None:  # editors mounted later follow the app's current theme
            self.set_editor_theme(*pair)
        self._apply_mode()

    def set_editor_theme(self, insert: TextAreaTheme, normal: TextAreaTheme) -> None:
        """Use `insert` in insert mode and `normal` (e.g. with a block cursor) in normal mode."""
        self.register_theme(insert)
        self.register_theme(normal)
        self._theme_names = (insert.name, normal.name)
        self._apply_mode()

    # ── speed ────────────────────────────────────────────────────────────

    def _set_document(self, text: str, language: str | None) -> None:
        """Like TextArea._set_document, but reuses the compiled highlight query."""
        query = _highlight_query(language) if language else None
        if query is None or language in self._languages:
            super()._set_document(text, language)
            return
        document = SyntaxAwareDocument(text, get_language(language))
        self.document = document
        self.wrapped_document = WrappedDocument(document, tab_width=self.indent_width)
        self.navigator = DocumentNavigator(self.wrapped_document)
        self._highlight_query = query
        self._build_highlight_map()
        self.move_cursor((0, 0))
        self._rewrap_and_refresh_virtual_size()

    def _refresh_size(self) -> None:
        """Only ask for a screen layout when the editor's height changes.

        `virtual_size` is a layout reactive, so in Textual every keystroke that makes
        a line longer re-arranges the whole screen. The editor is `height: auto` and
        full width, so a wider line needs no layout and no repaint of the other lines
        (the edited line is repainted by `edit`).
        """
        if self.soft_wrap or not self.is_mounted:
            super()._refresh_size()
            return
        width, height = self.document.get_size(self.indent_width)
        size = Size(width + self.gutter_width + 1, height)
        old = self.virtual_size
        if size.height != old.height:
            super()._refresh_size()
        elif size != old:
            self.set_reactive(TextArea.virtual_size, size)
            self._scroll_update(size)
            self._refresh_scrollbars()

    def _refresh_rows(self, first: int, last: int) -> None:
        """Repaint document rows first..last (inclusive); rows are visual lines without soft wrap."""
        if first > last:
            first, last = last, first
        self.refresh_lines(first, last - first + 1)

    def edit(self, edit: Edit) -> EditResult:
        rows_before = self.document.line_count
        gutter_before = self.gutter_width
        old_highlights = dict(self._highlights)  # rebuilt from scratch by the edit; the old lists stay intact
        result = super().edit(edit)
        if self.soft_wrap or self.document.line_count != rows_before or self.gutter_width != gutter_before:
            self.refresh()
            return result
        # The edited rows, plus every row whose syntax highlighting changed (an unclosed
        # bracket or string recolours lines far away).
        rows = set(range(edit.top[0], max(edit.bottom[0], result.end_location[0]) + 1))
        new_highlights = self._highlights
        for row in old_highlights.keys() | new_highlights.keys():
            if (old_highlights.get(row) or None) != (new_highlights.get(row) or None):
                rows.add(row)
        if len(rows) > 24:
            self.refresh()
            return result
        for row in sorted(rows):
            self._refresh_rows(row, row)
        return result

    def _watch_selection(self, previous: Selection, selection: Selection) -> None:
        bracket_before = self._matching_bracket_location
        super()._watch_selection(previous, selection)
        if self.soft_wrap or previous.start != previous.end or selection.start != selection.end:
            self.refresh()  # a real selection (or one that just ended): repaint everything
            return
        self._refresh_rows(previous.end[0], previous.end[0])
        if selection.end[0] != previous.end[0]:
            self._refresh_rows(selection.end[0], selection.end[0])
        if bracket_before is not None and bracket_before != self._matching_bracket_location:
            self._refresh_rows(bracket_before[0], bracket_before[0])

    def _on_focus(self, event: events.Focus) -> None:
        super()._on_focus(event)
        self._apply_mode()  # the global mode may have changed while another cell had focus

    # ── mode ─────────────────────────────────────────────────────────────

    def set_mode(self, mode: str) -> None:
        if not self.enabled:
            mode = "insert"
        VimTextArea.mode = mode
        self.pending = self.count = ""
        self._apply_mode()
        self.post_message(self.ModeChanged(self, mode))

    def _apply_mode(self) -> None:
        mode = self.mode if self.enabled else "insert"
        theme = self._theme_names[0] if mode == "insert" else self._theme_names[1]
        if self.theme != theme:
            self.theme = theme
        self.cursor_blink = mode == "insert"

    # ── keys ─────────────────────────────────────────────────────────────

    async def _on_key(self, event: events.Key) -> None:
        if self._popup is not None and self._popup_key(event):
            event.stop()
            event.prevent_default()
            return
        if self.mode == "insert" or not self.enabled:
            if _is_escape(event) and self.enabled:
                event.stop()
                event.prevent_default()
                if not self.cursor_at_start_of_line:
                    self.action_cursor_left()
                self.set_mode("normal")
            elif _is_escape(event):  # no vim: escape goes straight to cell mode
                event.stop()
                event.prevent_default()
                self.post_message(self.LeaveEditor(self))
            elif event.key == "tab" and self.language == "python" and self._word_before_cursor():
                event.stop()
                event.prevent_default()
                self.run_worker(self._complete(), exclusive=True, group="complete")
            elif event.key == "enter" and self.language == "python":  # markdown: a plain newline
                event.stop()
                event.prevent_default()
                self._newline()
            elif event.key == "backspace" and self._dedent_on_backspace():
                event.stop()
                event.prevent_default()
            else:
                await super()._on_key(event)
                if event.is_printable and self.language == "python":
                    self._schedule_autocomplete()
            return
        if _is_escape(event):
            event.stop()
            event.prevent_default()
            if self.pending or self.count:
                self.pending = self.count = ""
            else:
                self.post_message(self.LeaveEditor(self))
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
        had_count = bool(self.count)
        n = int(self.count or 1)
        pending, self.pending, self.count = self.pending, "", ""

        if pending in ("d", "c", "y"):
            self._operator(pending, key, n)
            return True
        if pending == "g":
            if key == "g":
                self.move_cursor((0, 0) if n == 1 else (min(self.document.line_count - 1, n - 1), 0))
            elif key == "J":
                self._join(n)
            elif key == "t":  # gt: next tab, {n}gt: tab n
                self.post_message(self.TabSwitch(self, n if had_count else None, 1))
            elif key == "T":
                self.post_message(self.TabSwitch(self, None, -n))
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
                    indent = indent_after(line)
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
            case "J" | "K":
                self.post_message(self.EdgeReached(self, n if key == "J" else -n))
            case "/":
                self.post_message(self.SearchRequested(self))
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

    def _join(self, n: int) -> None:
        """gJ: join the next line onto this one (vim's J; J itself moves between cells)."""
        for _ in range(n):
            row, _ = self.cursor_location
            if row + 1 < self.document.line_count:
                nxt = self.document.get_line(row + 1)
                cur = self.document.get_line(row)
                sep = " " if cur and nxt.strip() else ""
                self.replace(sep + nxt.lstrip(), (row, len(cur)), (row + 1, len(nxt)))
                self.move_cursor((row, len(cur)))

    # ── python editing helpers ───────────────────────────────────────────

    def _newline(self) -> None:
        """Enter: newline plus the indentation Python would want next."""
        self._close_popup()
        row, col = self.cursor_location
        line = self.document.get_line(row)
        before, after = line[:col], line[col:]
        indent = indent_after(before)
        if self.selection.start != self.selection.end:
            self.delete(*sorted([self.selection.start, self.selection.end]), maintain_selection_offset=False)
            row, col = self.cursor_location
        # Enter between brackets: put the closing bracket on its own line.
        if before.rstrip()[-1:] in "([{" and after.lstrip()[:1] in ")]}" and after.strip():
            outer = line[: len(line) - len(line.lstrip())]
            self.replace("\n" + indent + "\n" + outer + after.lstrip(), (row, col), (row, len(line)), maintain_selection_offset=False)
            self.move_cursor((row + 1, len(indent)))
            return
        self.replace("\n" + indent + after.lstrip() if after.strip() else "\n" + indent, (row, col), (row, len(line)), maintain_selection_offset=False)
        self.move_cursor((row + 1, len(indent)))

    def _dedent_on_backspace(self) -> bool:
        """Backspace inside leading whitespace removes a whole indent level."""
        row, col = self.cursor_location
        before = self.document.get_line(row)[:col]
        if col and not before.strip() and col % len(INDENT) == 0:
            self.delete((row, col - len(INDENT)), (row, col), maintain_selection_offset=False)
            return True
        return False

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

    def _schedule_autocomplete(self) -> None:
        """Show matches shortly after typing an identifier character or '.'."""
        if self._auto_timer is not None:
            self._auto_timer.stop()
            self._auto_timer = None
        row, col = self.cursor_location
        before = self.document.get_line(row)[:col]
        if _IDENT.search(before):
            self._auto_timer = self.set_timer(
                AUTOCOMPLETE_DELAY,
                lambda: self.run_worker(self._complete(auto=True), exclusive=True, group="complete"),
            )
        else:
            self._close_popup()

    async def _complete(self, auto: bool = False) -> None:
        """Ask the kernel for completions at the cursor.

        Tab (auto=False) fills in the common prefix and pops up the rest;
        as-you-type (auto=True) only shows the popup and never edits the text.
        """
        kernel = self._kernel()
        if kernel is None or not kernel.ready.is_set():
            return
        code = self.text
        cursor = self.cursor_location
        matches, start, end = await kernel.complete(code, self._offset(cursor))
        if self.cursor_location != cursor or self.mode != "insert":
            return
        typed = code[start : self._offset(cursor)]
        start_loc = self._location(start)
        if auto:
            matches = [m for m in matches if m != typed]
            if matches:
                self._show_popup(matches, start_loc)
            else:
                self._close_popup()
            return
        if not matches:
            return
        prefix = os.path.commonprefix(matches)
        if len(matches) == 1 or len(prefix) > len(typed):
            self.replace(prefix, start_loc, self._location(end), maintain_selection_offset=False)
        if len(matches) > 1:
            self._show_popup(matches, start_loc)

    def _kernel(self):
        """The kernel of the notebook window this editor belongs to."""
        node = self.parent
        while node is not None:
            if (kernel := getattr(node, "kernel", None)) is not None and not callable(kernel):
                return kernel
            node = node.parent
        return getattr(self.app, "kernel", None)

    def _show_popup(self, matches: list[str], start: Location) -> None:
        self._close_popup()
        popup = CompletionPopup(*matches[:200])
        self._popup, self._popup_start = popup, start
        x, y = self.cursor_screen_offset
        height = min(len(matches), 10) + 2
        if y + 1 + height > self.screen.size.height:  # no room below: open upwards
            y = max(0, y - height) - 1
        popup.styles.offset = (max(0, x - (self.cursor_location[1] - start[1]) - 1), y + 1)
        self.screen.mount(popup)
        popup.highlighted = 0

    def _close_popup(self) -> None:
        if self._popup is not None:
            self._popup.remove()
            self._popup = None

    def _popup_key(self, event: events.Key) -> bool:
        popup = self._popup
        if _is_escape(event):
            self._close_popup()
            return True
        match event.key:
            case "down" | "ctrl+n":
                popup.action_cursor_down()
            case "up" | "shift+tab" | "ctrl+p":
                popup.action_cursor_up()
            case "enter" | "tab":
                index = popup.highlighted or 0
                self.replace(str(popup.get_option_at_index(index).prompt), self._popup_start, self.cursor_location, maintain_selection_offset=False)
                self._close_popup()
            case _:
                self._close_popup()
                return False
        return True

    def _on_blur(self, event: events.Blur) -> None:
        self._close_popup()
        super()._on_blur(event)
