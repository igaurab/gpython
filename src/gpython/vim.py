"""A TextArea with vim-style modal editing and kernel-backed Tab completion.

Supported in normal mode: h j k l w b e W B E 0 ^ $ gg G f F t T ; , % { },
counts, i a I A o O, x X s S D C Y p P >> << gJ u ctrl+r, ":" for ex commands,
and the operators d c y with any motion or text object (iw aw iW aW ip ap i" a"
i' a' i( a( ib i[ a[ i{ a{ iB i< a<). v and V start charwise and linewise visual
mode, where every motion extends the selection and y d x c s p P > < J gJ ~ u U r
o gv work as in vim. Visual mode belongs to one editor: losing focus ends it.
dd deletes lines, as in vim; cells are deleted in cell mode (esc esc, then dd),
where u brings them back. Cell-level keys are posted as messages for the app:
j/k past the first/last line and J/K move to a neighbouring cell (EdgeReached),
"/" asks for a jump or a search (SearchRequested), n N * # repeat or start a
search (SearchNext), escape with nothing pending leaves the editor (LeaveEditor).
Every match of the active search is painted with SEARCH_STYLE (VimTextArea.search).
"""

from __future__ import annotations

import dataclasses
import functools
import os
import re

from rich.style import Style
from rich.text import Text
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
_BIG_WORD = re.compile(r"\S+")
_PAIRS = {"(": "()", ")": "()", "b": "()", "[": "[]", "]": "[]", "{": "{}", "}": "{}", "B": "{}", "<": "<>", ">": "<>"}
_QUOTES = "\"'`"
_FIND_KEYS = ("f", "F", "t", "T")
_MARK_KEYS = ("`", "'")
_CHAR_KEYS = (*_FIND_KEYS, *_MARK_KEYS, "m", '"', "q", "@")
_OPERATORS = ("gu", "gU", "g~", "d", "c", "y", ">", "<", "=")
_DEDENT_KEYWORDS = ("elif ", "elif(", "else", "except", "finally", "case ")
_BLOCK_ENDERS = ("return", "break", "continue", "pass", "raise")
_INCLUSIVE = ("e", "E", "ge", "gE", "%")
_NUMBER = re.compile(r"-?\d+")
_VISUAL_NAMES = {"v": "visual", "V": "v-line"}
_ESCAPE_KEYS = {"escape", "ctrl+left_square_bracket", "ctrl+left_square_brace"}
_IDENT = re.compile(r"[\w.]$")
_DEDENT_AFTER = ("return", "break", "continue", "pass", "raise")
AUTOCOMPLETE_DELAY = 0.12
INDENT = "    "


MONOKAI = TextAreaTheme.get_builtin_theme("monokai")
MONOKAI_NORMAL = dataclasses.replace(
    MONOKAI, name="monokai-normal", cursor_style=Style(color="#272822", bgcolor="#e6db74", bold=True)
)
SEARCH_STYLE = Style(color="#000000", bgcolor="#ffd75f", bold=True)
_REVERSE = Style(reverse=True)


def search_regex(pattern: str) -> re.Pattern:
    """Plain-text search with vim's smartcase: any capital letter makes it case-sensitive."""
    flags = 0 if any(c.isupper() for c in pattern) else re.IGNORECASE
    return re.compile(re.escape(pattern), flags)


def word_at(line: str, col: int) -> re.Match | None:
    """The word under column `col`, or the first one after it on the line (vim's * and #)."""
    for match in _WORD.finditer(line):
        if match.end() > col:
            return match
    return None


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


def _bracket_depth_change(code: str) -> int:
    """How many brackets `code` leaves open (negative: closes), ignoring strings and comments."""
    depth = 0
    quote = ""
    i = 0
    while i < len(code):
        char = code[i]
        if quote:
            if char == "\\":
                i += 1
            elif code.startswith(quote, i):
                i += len(quote) - 1
                quote = ""
        elif char == "#":
            break
        elif char in "\"'":
            quote = code[i : i + 3] if code.startswith(char * 3, i) else char
            i += len(quote) - 1
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        i += 1
    return depth


def _ends_block_header(code: str) -> bool:
    before_comment = code.split("#", 1)[0].rstrip()
    return before_comment.endswith(":") and _bracket_depth_change(before_comment) <= 0


def reindent(lines: list[str]) -> list[str]:
    """= on Python: every line gets a 4-space level. Nesting follows the original shape (deeper
    is one level in, shallower pops back to the level that had that indentation); a line ending
    with ':' opens a level, else/elif/except/finally sit one level out from the body before them,
    return/break/continue/pass/raise close a level for the next line, and the lines inside open
    brackets hang one level under the line that opened them."""
    out: list[str] = []
    stack = [0]
    level = prev_level = 0
    depth = 0
    opener_level = 0
    opens_level = closes_level = False
    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            out.append("")
            continue
        if depth > 0:
            level = opener_level if stripped[0] in ")]}" else opener_level + 1
            out.append(INDENT * level + stripped)
            depth = max(0, depth + _bracket_depth_change(stripped))
            if depth == 0:
                level = opener_level
                opens_level, closes_level = _ends_block_header(stripped), False
                prev_level = level
            continue
        original = len(raw.expandtabs(4)) - len(raw.expandtabs(4).lstrip())
        if original > stack[-1]:
            stack.append(original)
        else:
            while len(stack) > 1 and stack[-1] > original:
                stack.pop()
        level = len(stack) - 1
        if opens_level and level <= prev_level:
            level = prev_level + 1
            del stack[level:]
            stack.append(original)
        elif stripped.startswith(_DEDENT_KEYWORDS) and level >= prev_level and level > 0 and not opens_level:
            level -= 1
            del stack[level + 1 :]
        elif closes_level and level == prev_level and level > 0:
            level -= 1
            del stack[level + 1 :]
        out.append(INDENT * level + stripped)
        prev_level = level
        depth = max(0, _bracket_depth_change(stripped))
        if depth > 0:
            opener_level = level
            opens_level = closes_level = False
        else:
            opens_level = _ends_block_header(stripped)
            closes_level = stripped.split(" ", 1)[0].rstrip("(;") in _BLOCK_ENDERS
    return out


def indent_after(line: str) -> str:
    """Indentation for the line typed after `line`, following Python's rules of thumb."""
    current = line[: len(line) - len(line.lstrip())]
    code = line.split("#", 1)[0].rstrip()
    if code.endswith((":", "(", "[", "{")):
        return current + INDENT
    if code.lstrip().split(" ")[0] in _DEDENT_AFTER:
        return current[: max(0, len(current) - len(INDENT))]
    return current


class SearchLine(Text):
    """An editor line whose search-match spans stay above the styles TextArea adds
    afterwards (syntax colours, the cursor line) but below the cursor. A cursor on a
    match is drawn reversed, since most normal-mode cursors are yellow too.

    The match spans are `_spans[first_match : first_match + matches]`; a later span is
    slotted in before them, the cursor goes on top.
    """

    __slots__ = ("first_match", "matches", "cursor_style")

    def stylize(self, style, start: int = 0, end: int | None = None) -> None:
        spans = self._spans
        count = len(spans)
        first, matches = self.first_match, self.matches
        if style is self.cursor_style:
            if any(span.start <= start < span.end for span in spans[first : first + matches]):
                style = style + _REVERSE
            super().stylize(style, start, end)
            return
        super().stylize(style, start, end)
        if matches and len(spans) > count:
            spans.insert(first, spans.pop())
            self.first_match = first + 1


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
    selection: reactive[Selection] = reactive(Selection(), init=False, always_update=True, repaint=False)

    enabled = True
    line_numbers = True
    mode = "normal"
    register = ""
    registers: dict[str, tuple[str, bool]] = {}
    macros: dict[str, list[tuple[str, str | None]]] = {}
    last_macro = ""
    register_linewise = False
    visual = ""
    search: re.Pattern | None = None

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

    class SearchNext(Message):
        """n / N (and * / # with the word under the cursor as `pattern`): search `step` matches on."""

        def __init__(self, editor: VimTextArea, step: int, pattern: str | None = None) -> None:
            super().__init__()
            self.editor = editor
            self.step = step
            self.pattern = pattern

    class LeaveEditor(Message):
        """escape in normal mode with nothing pending: the cell takes focus (cell mode)."""

        def __init__(self, editor: VimTextArea) -> None:
            super().__init__()
            self.editor = editor


    class FindFiles(Message):
        """space f f (leader key, like telescope's): the app opens the file finder."""

        def __init__(self, editor: VimTextArea) -> None:
            super().__init__()
            self.editor = editor

    class RunByNumber(Message):
        """space r: the app waits for a cell number and runs that cell."""

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
        super().__init__(
            text,
            theme="monokai",
            soft_wrap=False,
            tab_behavior="indent",
            highlight_cursor_line=False,
            show_line_numbers=kwargs.pop("show_line_numbers", self.line_numbers),
            **kwargs,
        )
        self.pending = ""
        self.count = ""
        self._anchor: Location = (0, 0)
        self._last_visual: tuple[str, Location, Location] | None = None
        self._last_find: tuple[str, str] | None = None
        self._op_count = 1
        self._marks: dict[str, Location] = {}
        self._use_register = ""
        self._keys: list[tuple[str, str | None]] = []
        self._in_command = False
        self._cmd_start = 0
        self._cmd_count: int | None = None
        self._edited = False
        self._no_dot = False
        self._last_change: tuple[int | None, list[tuple[str, str | None]]] | None = None
        self._replaying = False
        self._insert_repeat = 1
        self._macro_name: str | None = None
        self._one_shot = False
        self._replaced: list[str | None] = []
        self._popup: CompletionPopup | None = None
        self._popup_start: Location = (0, 0)
        self._auto_timer = None
        self._mounted_screen = None
        self._theme_names = ("monokai", "monokai-normal")
        self.set_editor_theme(MONOKAI, MONOKAI_NORMAL)
        if language:
            self.language = language
        if not self.enabled:
            VimTextArea.mode = "insert"

    def on_mount(self) -> None:
        pair = getattr(self.app, "editor_theme_pair", None)
        if pair is not None:
            self.set_editor_theme(*pair)
        self._apply_mode()
        self._mounted_screen = self.screen

    def on_unmount(self) -> None:
        """Textual's TextArea watches the app's theme and subscribes to the screen's
        text-selection signal when mounted and never lets go: both keep a strong reference,
        so every editor of a deleted cell stayed alive with its document, parser and caches.
        """
        watchers = getattr(self.app, "__watchers", None)
        if watchers and "theme" in watchers:
            watchers["theme"] = [pair for pair in watchers["theme"] if pair[0] is not self]
        screen, self._mounted_screen = self._mounted_screen, None
        if screen is not None:
            screen.text_selection_started_signal.unsubscribe(self)

    def set_editor_theme(self, insert: TextAreaTheme, normal: TextAreaTheme) -> None:
        """Use `insert` in insert mode and `normal` (e.g. with a block cursor) in normal mode."""
        self.register_theme(insert)
        self.register_theme(normal)
        self._theme_names = (insert.name, normal.name)
        self._apply_mode()


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

    def get_line(self, line_index: int):
        """The line's text: search matches painted in SEARCH_STYLE; in linewise visual mode the selected lines are highlighted end to end."""
        line = self._search_line(line_index) if self.search is not None else super().get_line(line_index)
        if self.visual == "V" and self._theme is not None and self._theme.selection_style is not None:
            first, last = sorted((self._anchor[0], self.cursor_location[0]))
            if first <= line_index <= last:
                line.stylize(self._theme.selection_style)
        return line

    def _search_line(self, line_index: int) -> SearchLine:
        text = self.document.get_line(line_index)
        line = SearchLine(text, end="", no_wrap=True)
        line.first_match = line.matches = 0
        line.cursor_style = self._theme.cursor_style if self._theme else None
        for match in self.search.finditer(text):
            line.stylize(SEARCH_STYLE, match.start(), match.end())
        line.matches = len(line.spans)
        return line

    def repaint_search(self) -> None:
        """Forget the cached lines so the search highlight is painted afresh."""
        self._line_cache.clear()
        self.refresh()

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
        self._edited = True
        rows_before = self.document.line_count
        gutter_before = self.gutter_width
        old_highlights = dict(self._highlights)
        result = super().edit(edit)
        if self.soft_wrap or self.document.line_count != rows_before or self.gutter_width != gutter_before:
            self.refresh()
            return result
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
        if self.soft_wrap:
            self.refresh()
            return
        if previous.start != previous.end or selection.start != selection.end or self.visual:
            rows = (previous.start[0], previous.end[0], selection.start[0], selection.end[0])
            self._refresh_rows(min(rows), max(rows))
            for bracket in (bracket_before, self._matching_bracket_location):
                if bracket is not None:
                    self._refresh_rows(bracket[0], bracket[0])
            return
        self._refresh_rows(previous.end[0], previous.end[0])
        if selection.end[0] != previous.end[0]:
            self._refresh_rows(selection.end[0], selection.end[0])
        if bracket_before is not None and bracket_before != self._matching_bracket_location:
            self._refresh_rows(bracket_before[0], bracket_before[0])

    def focus(self, scroll_visible: bool = True):
        """Focusing an editor puts the notebook in edit mode; see `Cell.focus`."""
        self.app.cell_mode = False
        return super().focus(scroll_visible)

    def _on_focus(self, event: events.Focus) -> None:
        super()._on_focus(event)
        self._apply_mode()

    @classmethod
    def _get_component_classes(cls) -> frozenset[str]:
        """TextArea declares eight component classes (text-area--cursor, --gutter, ...) and Textual
        runs a full stylesheet match for each of them on every editor at mount and on every theme
        change. They are only fallbacks for theme fields left unset, and every gpython theme sets
        them all, so the editor has none; `get_component_rich_style` below covers the lookups
        TextArea still makes."""
        return frozenset()

    def get_component_rich_style(self, *names: str, partial: bool = False, default: Style | None = None) -> Style:
        return super().get_component_rich_style(*names, partial=partial, default=Style() if default is None else default)

    def watch_has_focus(self, has_focus: bool) -> None:
        """Textual re-applies the stylesheet to a widget and its component classes whenever its
        focus changes, for `:focus` rules. No rule depends on an editor's focus (a cell's TextArea
        has no border either way), and that re-application cost about 2 ms per cell move."""


    @property
    def visual_mode(self) -> str:
        """"visual" or "v-line" while a visual selection is active, else ""."""
        return _VISUAL_NAMES.get(self.visual, "")

    def set_mode(self, mode: str) -> None:
        if self.visual:
            self._exit_visual(post=False)
        if not self.enabled:
            mode = "insert"
        VimTextArea.mode = mode
        self.pending = self.count = ""
        self._apply_mode()
        self.post_message(self.ModeChanged(self, mode))

    def _watch_has_focus(self, focus: bool) -> None:
        """Only the focused editor paints a cursor and a cursor line. Textual's line cache
        does not key on focus, so the cursor line is forgotten when focus moves."""
        super()._watch_has_focus(focus)
        self.set_reactive(TextArea.highlight_cursor_line, focus and not self.visual)
        self.forget_cursor_line()

    def forget_cursor_line(self) -> None:
        _, cursor_y = self._cursor_offset
        cache = self._line_cache
        for key in [key for key in cache.keys() if key[2] == cursor_y]:
            cache.discard(key)

    def _apply_mode(self) -> None:
        mode = self.mode if self.enabled else "insert"
        theme = self._theme_names[0] if mode in ("insert", "replace") else self._theme_names[1]
        if self.theme != theme:
            self.theme = theme
        self.cursor_blink = mode in ("insert", "replace")

    def _record(self, key: str, char: str | None) -> None:
        """Every key goes to the macro being recorded and to the keys of the current command (for `.`)."""
        if self._macro_name is not None:
            VimTextArea.macros[self._macro_name].append((key, char))
        if not self._replaying:
            self._keys.append((key, char))

    def _feed(self, key: str, char: str | None) -> None:
        """Handle one key as if typed: replays macros and the last change."""
        if not self.enabled:
            return
        if key in _ESCAPE_KEYS:
            key, char = "escape", None
        if self.mode == "insert":
            self._insert_key(key, char)
        elif self.mode == "replace":
            self._replace_key(key, char)
        else:
            name = char if char and len(char) == 1 and char.isprintable() else key
            if self.visual:
                self._visual(name)
            else:
                self._normal(name)

    def _replay(self, keys: list[tuple[str, str | None]], times: int = 1) -> None:
        was_replaying, self._replaying = self._replaying, True
        try:
            for _ in range(times):
                for key, char in keys:
                    self._feed(key, char)
        finally:
            self._replaying = was_replaying

    def _insert_key(self, key: str, char: str | None) -> None:
        """The insert-mode keys a replay needs: text, enter, backspace, tab and escape."""
        if key == "escape":
            self._end_insert()
        elif key == "enter":
            if self.language == "python":
                self._newline()
            else:
                self.insert("\n")
        elif key == "backspace":
            if not self._dedent_on_backspace():
                self.action_delete_left()
        elif key == "tab":
            self.insert(INDENT)
        elif char and len(char) == 1 and char.isprintable():
            self.insert(char)

    def _end_insert(self) -> None:
        """Escape from insert or replace mode: back to normal mode, repeating a counted insert (3ix)."""
        if self.mode == "insert" and not self.cursor_at_start_of_line:
            self.action_cursor_left()
        elif self.mode == "replace":
            self._replaced = []
            if not self.cursor_at_start_of_line:
                self.action_cursor_left()
        self.set_mode("normal")
        repeat, self._insert_repeat = self._insert_repeat, 1
        self._finish_command()
        if repeat > 1 and self._last_change is not None:
            self._replay(self._last_change[1], repeat - 1)

    def _begin_command(self, had_count: bool, n: int) -> None:
        if self._replaying:
            return
        self._in_command = True
        self._cmd_start = len(self._keys) - 1
        self._cmd_count = n if had_count else None
        self._edited = False
        self._no_dot = False

    def _finish_command(self) -> None:
        """A normal-mode command is complete: keep its keys for `.` when it changed the text."""
        if self._replaying or not self._in_command:
            return
        self._in_command = False
        if self._edited and not self._no_dot:
            self._last_change = (self._cmd_count, self._keys[self._cmd_start :])
        self._keys.clear()
        if self._one_shot and self.mode == "normal":
            self._one_shot = False
            self.set_mode("insert")

    def _repeat_change(self, had_count: bool, n: int) -> None:
        """`.`: replay the last change; a count replaces the one it was made with."""
        if self._last_change is None:
            return
        count, keys = self._last_change
        count = n if had_count else count
        prefix = [(digit, digit) for digit in str(count)] if count and count > 1 else []
        self._replay(prefix + keys)
        self._last_change = (count, keys)

    def _replace_key(self, key: str, char: str | None) -> None:
        """R: overtype; backspace restores what was typed over; escape leaves."""
        if key == "escape":
            self._end_insert()
            return
        row, col = self.cursor_location
        if key == "backspace":
            if not self._replaced or col == 0:
                return
            original = self._replaced.pop()
            if original is None:
                self.delete((row, col - 1), (row, col), maintain_selection_offset=False)
            else:
                self.replace(original, (row, col - 1), (row, col), maintain_selection_offset=False)
            self.move_cursor((row, col - 1))
        elif key == "enter":
            self.insert("\n")
            self._replaced = []
        elif char and len(char) == 1 and char.isprintable():
            line = self.document.get_line(row)
            if col < len(line):
                self._replaced.append(line[col])
                self.replace(char, (row, col), (row, col + 1), maintain_selection_offset=False)
            else:
                self._replaced.append(None)
                self.insert(char, (row, col), maintain_selection_offset=False)
            self.move_cursor((row, col + 1))


    async def _on_key(self, event: events.Key) -> None:
        if self._popup is not None and self._popup_key(event):
            event.stop()
            event.prevent_default()
            return
        if self.enabled:
            self._record(event.key, event.character)
        if self.mode == "replace" and self.enabled:
            event.stop()
            event.prevent_default()
            self._replace_key("escape" if _is_escape(event) else event.key, event.character)
            return
        if self.mode == "insert" or not self.enabled:
            if _is_escape(event) and self.enabled:
                event.stop()
                event.prevent_default()
                self._end_insert()
            elif event.key == "ctrl+o" and self.enabled:
                event.stop()
                event.prevent_default()
                self._one_shot = True
                self.set_mode("normal")
            elif _is_escape(event):
                event.stop()
                event.prevent_default()
                self.post_message(self.LeaveEditor(self))
            elif event.key == "tab" and self.language == "python" and self._word_before_cursor():
                event.stop()
                event.prevent_default()
                self.run_worker(self._complete(), exclusive=True, group="complete")
            elif event.key == "enter" and self.language == "python":
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
            if self.visual:
                self._exit_visual()
            elif self.pending or self.count:
                self.pending = self.count = ""
            else:
                self.post_message(self.LeaveEditor(self))
            return
        key = event.character if event.is_printable and event.character else event.key
        if (self._visual(key) if self.visual else self._normal(key)) or event.is_printable:
            event.stop()
            event.prevent_default()

    def _normal(self, key: str) -> bool:
        """Handle one normal-mode key; commands are tracked for `.`. Returns False if the key means nothing here."""
        counting = key.isdigit() and (key != "0" or self.count) and not self._wants_char()
        if not self.pending and not self._in_command and not counting and key != "escape":
            self._begin_command(bool(self.count), int(self.count or 1))
        handled = self._normal_key(key)
        if self._in_command and not self.pending and self.mode == "normal" and not self.visual:
            self._finish_command()
        return handled

    def _split_operator(self, pending: str) -> tuple[str, str] | None:
        for op in _OPERATORS:
            if pending.startswith(op):
                return op, pending[len(op) :]
        return None

    def _normal_key(self, key: str) -> bool:
        if key == "escape":
            self.pending = self.count = ""
            self._op_count = 1
            return True
        if key.isdigit() and (key != "0" or self.count) and not self._wants_char():
            self.count += key
            return True
        had_count = bool(self.count)
        n = int(self.count or 1)
        pending, self.pending, self.count = self.pending, "", ""

        if (split := self._split_operator(pending)) is not None:
            op, suffix = split
            n *= self._op_count
            if suffix == "":
                if key in ("i", "a", "g", *_FIND_KEYS, *_MARK_KEYS):
                    self.pending, self._op_count = pending + key, n
                elif key == op[-1] or key == op:
                    row = self.cursor_location[0]
                    self._apply_operator(op, (row, 0), (min(self.document.line_count - 1, row + n - 1), 0), True)
                else:
                    self._op_count = 1
                    self._operator(op, key, n)
                return True
            self._op_count = 1
            if suffix in "ia":
                self._object_operator(op, suffix + key, n)
            elif suffix == "g":
                if key == "g":
                    self._operator(op, "gg", n)
                elif key in ("e", "E"):
                    self._operator(op, "g" + key, n)
                elif len(op) == 2 and key == op[1]:
                    row = self.cursor_location[0]
                    self._apply_operator(op, (row, 0), (min(self.document.line_count - 1, row + n - 1), 0), True)
            elif len(key) == 1:
                self._operator(op, (suffix, key), n)
            return True
        if pending in _FIND_KEYS or pending in _MARK_KEYS:
            if len(key) == 1 and self._move((pending, key), n) is not None:
                self._clamp()
            return True
        if pending == "m":
            if len(key) == 1:
                self._marks[key] = self.cursor_location
            return True
        if pending == '"':
            if len(key) == 1:
                self._use_register = key
            return True
        if pending == "q":
            if len(key) == 1 and key.isalnum():
                self._macro_name = key
                VimTextArea.macros[key] = []
            return True
        if pending == "@":
            name = VimTextArea.last_macro if key == "@" else key
            if name in VimTextArea.macros:
                VimTextArea.last_macro = name
                self._replay(VimTextArea.macros[name], n)
            return True
        if pending == "g":
            if key == "g":
                self._move("gg", n)
            elif key in ("e", "E"):
                self._move("g" + key, n)
                self._clamp()
            elif key in ("u", "U", "~"):
                self.pending, self._op_count = "g" + key, n
            elif key == "v":
                self._reselect()
            elif key == "J":
                self._join(n)
            elif key == "t":
                self.post_message(self.TabSwitch(self, n if had_count else None, 1))
            elif key == "T":
                self.post_message(self.TabSwitch(self, None, -n))
            return True
        if pending in (" ", " f"):
            if pending == " " and key == "f":
                self.pending = " f"
            elif pending == " " and key == "r":
                self.post_message(self.RunByNumber(self))
            elif pending == " f" and key == "f":
                self.post_message(self.FindFiles(self))
            return True

        row, col = self.cursor_location
        line = self.document.get_line(row)
        match key:
            case "d" | "c" | "y" | ">" | "<" | "=":
                self.pending, self._op_count = key, n
            case "g" | " " | "f" | "F" | "t" | "T":
                self.pending, self.count = key, str(n) if n > 1 else ""
            case "m" | "`" | "'" | '"' | "@":
                self.pending, self.count = key, str(n) if n > 1 else ""
            case "q":
                if self._macro_name is not None:
                    VimTextArea.macros[self._macro_name].pop()
                    VimTextArea.last_macro = self._macro_name
                    self._macro_name = None
                else:
                    self.pending = key
            case ".":
                self._no_dot = True
                self._repeat_change(had_count, n)
            case "v" | "V":
                self._enter_visual(key, self.cursor_location)
            case "i":
                self._insert_repeat = n
                self.set_mode("insert")
            case "a":
                if col < len(line):
                    self.move_cursor((row, col + 1))
                self._insert_repeat = n
                self.set_mode("insert")
            case "I":
                self.move_cursor((row, len(line) - len(line.lstrip())))
                self._insert_repeat = n
                self.set_mode("insert")
            case "A":
                self.move_cursor((row, len(line)))
                self._insert_repeat = n
                self.set_mode("insert")
            case "o" | "O":
                indent = line[: len(line) - len(line.lstrip())]
                if key == "o":
                    indent = indent_after(line)
                    self.insert("\n" + indent, (row, len(line)), maintain_selection_offset=False)
                else:
                    self.insert(indent + "\n", (row, 0), maintain_selection_offset=False)
                    self.move_cursor((row, len(indent)))
                self._insert_repeat = n
                self.set_mode("insert")
            case "R":
                self._replaced = []
                self.set_mode("replace")
            case "~":
                if line:
                    end = min(len(line), col + n)
                    self._case_edit((row, col), (row, end), str.swapcase)
                    self.move_cursor((row, min(end, len(line) - 1)))
            case "ctrl+a" | "ctrl+x":
                self._add_to_number(n if key == "ctrl+a" else -n)
            case "x":
                if line:
                    self._chars_operator("d", (row, col), (row, min(col + n, len(line))))
            case "X":
                if col:
                    self._chars_operator("d", (row, max(0, col - n)), (row, col))
            case "s":
                self._chars_operator("c", (row, col), (row, min(col + n, len(line))))
            case "Y":
                self._yank_lines(row, min(self.document.line_count - 1, row + n - 1))
                self._copy_register()
            case "S":
                self._apply_operator("c", (row, 0), (min(self.document.line_count - 1, row + n - 1), 0), True)
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
            case "n" | "N":
                self.post_message(self.SearchNext(self, n if key == "n" else -n))
            case "*" | "#":
                word = word_at(line, col)
                if word is None:
                    self.notify("No string under cursor", severity="warning")
                else:
                    self.move_cursor((row, word.start()))
                    self.post_message(self.SearchNext(self, n if key == "*" else -n, word.group()))
            case "u":
                self._no_dot = True
                self.undo()
            case "ctrl+r":
                self._no_dot = True
                self.redo()
            case "r":
                self.post_message(self.RunByNumber(self))
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

    def _wants_char(self) -> bool:
        """The pending key takes any character next (f F t T r ` ' m \" q @), so a digit is not a count."""
        return self.pending[-1:] in (*_CHAR_KEYS, "r") and self.pending != " f"

    def _case_edit(self, start: Location, end: Location, change) -> None:
        """~ g~ gu gU: replace the text between start and end with change(text); cursor at the start."""
        text = self.get_text_range(start, end)
        new = change(text)
        if new != text:
            self.history.checkpoint()
            self.replace(new, start, end, maintain_selection_offset=False)
            self.history.checkpoint()
        self.move_cursor(start)

    def _add_to_number(self, delta: int) -> None:
        """ctrl+a / ctrl+x: add `delta` to the number under or after the cursor."""
        row, col = self.cursor_location
        line = self.document.get_line(row)
        match = next((m for m in _NUMBER.finditer(line) if m.end() > col), None)
        if match is None:
            return
        new = str(int(match.group()) + delta)
        self.replace(new, (row, match.start()), (row, match.end()), maintain_selection_offset=False)
        self.move_cursor((row, match.start() + len(new) - 1))

    def _visible_rows(self) -> tuple[int, int]:
        """The first and last document rows on screen (H and L)."""
        top = self.scroll_offset.y
        height = max(1, self.size.height)
        return top, min(self.document.line_count - 1, top + height - 1)

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
        """In normal mode the cursor sits on a character, not after the last one (not during ctrl+o)."""
        if self._one_shot:
            return
        row, col = self.cursor_location
        length = len(self.document.get_line(row))
        if col >= length > 0:
            self.move_cursor((row, length - 1))


    def _move(self, key: str | tuple[str, str], n: int) -> bool | None:
        """Move the cursor; return True for linewise motions, None if not a motion.

        A find (f F t T) is the tuple (kind, char); ; and , repeat the last one.
        """
        row, col = self.cursor_location
        line = self.document.get_line(row)
        last = self.document.line_count - 1
        match key:
            case (str() as kind, str() as char) if kind in _FIND_KEYS:
                target = self._find(kind, char, n)
                if target is None:
                    return None
                self._last_find = (kind, char)
                self.move_cursor(target)
            case ";" | ",":
                found = self._resolve(key)
                if found is None:
                    return None
                remembered = self._last_find
                result = self._move(found, n)
                self._last_find = remembered
                return result
            case "W" | "B":
                for _ in range(n):
                    self.move_cursor(self._word_start(1 if key == "W" else -1, _BIG_WORD))
            case "E":
                for _ in range(n):
                    self.move_cursor(self._word_end(_BIG_WORD))
            case "%":
                target = self._match_pair()
                if target is None:
                    return None
                self.move_cursor(target)
            case "{" | "}":
                self.move_cursor(self._paragraph_edge(1 if key == "}" else -1, n))
            case "gg":
                self.move_cursor((0, 0) if n == 1 else (min(last, n - 1), 0))
                return True
            case "ge" | "gE":
                for _ in range(n):
                    self.move_cursor(self._previous_word_end(_WORD if key == "ge" else _BIG_WORD))
            case "|":
                self.move_cursor((row, min(max(0, len(line) - 1), n - 1)))
            case "_" | "+" | "-" | "enter":
                target = row + (n - 1 if key == "_" else n if key in ("+", "enter") else -n)
                target = max(0, min(last, target))
                text = self.document.get_line(target)
                self.move_cursor((target, len(text) - len(text.lstrip())))
                return True
            case "H" | "L" | "M":
                top, bottom = self._visible_rows()
                target = min(bottom, top + n - 1) if key == "H" else max(top, bottom - n + 1) if key == "L" else (top + bottom) // 2
                text = self.document.get_line(target)
                self.move_cursor((target, len(text) - len(text.lstrip())))
                return True
            case ("`" | "'", str() as name):
                mark = self._marks.get(name)
                if mark is None:
                    return None
                mark = self.clamp_visitable(mark)
                if key[0] == "'":
                    text = self.document.get_line(mark[0])
                    self.move_cursor((mark[0], len(text) - len(text.lstrip())))
                    return True
                self.move_cursor(mark)
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

    def _word_start(self, direction: int, pattern: re.Pattern = _WORD) -> Location:
        """vim `w` (direction 1) / `b` (direction -1): next/previous word start; empty lines count.

        With `pattern` _BIG_WORD it is W / B: words are runs of non-blanks.
        """
        row, col = self.cursor_location
        last = self.document.line_count - 1
        if direction > 0:
            while row <= last:
                line = self.document.get_line(row)
                starts = [m.start() for m in pattern.finditer(line)]
                if (row, col) != self.cursor_location and not line.strip():
                    return (row, 0)
                for start in starts:
                    if (row, start) > self.cursor_location:
                        return (row, start)
                row, col = row + 1, -1
            return self.document.end
        while row >= 0:
            line = self.document.get_line(row)
            starts = [m.start() for m in pattern.finditer(line)]
            for start in reversed(starts):
                if (row, start) < self.cursor_location:
                    return (row, start)
            if row != self.cursor_location[0] and not line.strip():
                return (row, 0)
            row -= 1
        return (0, 0)

    def _word_end(self, pattern: re.Pattern = _WORD) -> Location:
        row, col = self.cursor_location
        while row < self.document.line_count:
            line = self.document.get_line(row)
            for m in pattern.finditer(line):
                if m.end() - 1 > col:
                    return (row, m.end() - 1)
            row, col = row + 1, -1
        return self.document.end

    def _previous_word_end(self, pattern: re.Pattern = _WORD) -> Location:
        """ge / gE: the end of the previous word, possibly on an earlier line."""
        row, col = self.cursor_location
        while row >= 0:
            line = self.document.get_line(row)
            ends = [m.end() - 1 for m in pattern.finditer(line)]
            for end in reversed(ends):
                if (row, end) < self.cursor_location:
                    return (row, end)
            row, col = row - 1, 10**9
            if row >= 0 and not self.document.get_line(row).strip():
                return (row, 0)
        return (0, 0)


    def _resolve(self, key):
        """; and , as the find tuple they repeat (None without an earlier find); other keys unchanged."""
        if key not in (";", ","):
            return key
        if self._last_find is None:
            return None
        kind, char = self._last_find
        return (kind.swapcase(), char) if key == "," else (kind, char)

    def _find(self, kind: str, char: str, n: int) -> Location | None:
        """f F t T: the n-th `char` after/before the cursor on this line (t and T stop next to it)."""
        row, col = self.cursor_location
        line = self.document.get_line(row)
        index = col
        for _ in range(n):
            index = line.find(char, index + 1) if kind in "ft" else line.rfind(char, 0, max(0, index))
            if index < 0:
                return None
        if kind == "t":
            index -= 1
        elif kind == "T":
            index += 1
        return (row, index)

    def _match_pair(self) -> Location | None:
        """%: the bracket matching the first bracket at or after the cursor on this line."""
        row, col = self.cursor_location
        line = self.document.get_line(row)
        for index in range(col, len(line)):
            char = line[index]
            if char in "([{":
                return self._scan_close(_PAIRS[char], (row, index + 1))
            if char in ")]}":
                return self._scan_open(_PAIRS[char], (row, index - 1))
        return None

    def _scan_close(self, pair: str, start: Location) -> Location | None:
        """The `pair[1]` that closes a bracket open just before `start`, nesting respected."""
        depth = 0
        row, col = start
        while row < self.document.line_count:
            line = self.document.get_line(row)
            for index in range(col, len(line)):
                if line[index] == pair[0]:
                    depth += 1
                elif line[index] == pair[1]:
                    if depth == 0:
                        return (row, index)
                    depth -= 1
            row, col = row + 1, 0
        return None

    def _scan_open(self, pair: str, start: Location) -> Location | None:
        """The unclosed `pair[0]` at or before `start`, nesting respected."""
        depth = 0
        row, col = start
        while row >= 0:
            line = self.document.get_line(row)
            for index in range(min(col, len(line) - 1), -1, -1):
                if line[index] == pair[1]:
                    depth += 1
                elif line[index] == pair[0]:
                    if depth == 0:
                        return (row, index)
                    depth -= 1
            row -= 1
            col = len(self.document.get_line(row)) - 1 if row >= 0 else 0
        return None

    def _blank(self, row: int) -> bool:
        return not self.document.get_line(row).strip()

    def _paragraph_edge(self, direction: int, n: int) -> Location:
        """{ / }: the blank line before/after the paragraph, else the start/end of the text."""
        last = self.document.line_count - 1
        row = self.cursor_location[0]
        for _ in range(n):
            if 0 <= row <= last and self._blank(row):
                while 0 <= row <= last and self._blank(row):
                    row += direction
            while 0 <= row <= last and not self._blank(row):
                row += direction
            if row > last:
                return self.document.end
            if row < 0:
                return (0, 0)
        return (row, 0)


    def _text_object(self, spec: str, n: int = 1) -> tuple[Location, Location, bool] | None:
        """iw aw iW aW ip ap i" a" i' a' i( a( ib i[ a[ i{ a{ iB i< a<: (start, end, linewise).

        `end` is exclusive; linewise objects span whole lines. None when there is no such object here.
        A count on a bracket object takes the n-th enclosing pair (2i( is the pair around the pair).
        """
        if len(spec) != 2:
            return None
        around, kind = spec[0] == "a", spec[1]
        row, col = self.cursor_location
        line = self.document.get_line(row)
        if kind in "wW":
            return self._word_object(row, col, line, around, kind == "W")
        if kind == "p":
            return self._paragraph_object(row, around)
        if kind in _QUOTES:
            return self._quote_object(row, col, line, kind, around)
        if kind in _PAIRS:
            return self._bracket_object(_PAIRS[kind], around, n)
        return None

    def _word_object(self, row: int, col: int, line: str, around: bool, big: bool):
        if not line:
            return None
        col = min(col, len(line) - 1)

        def kind(char: str) -> int:
            if char.isspace():
                return 0
            return 1 if big or char.isalnum() or char == "_" else 2

        here = kind(line[col])
        start, end = col, col + 1
        while start > 0 and kind(line[start - 1]) == here:
            start -= 1
        while end < len(line) and kind(line[end]) == here:
            end += 1
        if around and here:
            trailing = end
            while trailing < len(line) and line[trailing].isspace():
                trailing += 1
            if trailing > end:
                end = trailing
            else:
                while start > 0 and line[start - 1].isspace():
                    start -= 1
        elif around and end < len(line):
            following = kind(line[end])
            while end < len(line) and kind(line[end]) == following:
                end += 1
        return (row, start), (row, end), False

    def _paragraph_object(self, row: int, around: bool):
        last = self.document.line_count - 1
        blank = self._blank(row)
        top = bottom = row
        while top > 0 and self._blank(top - 1) == blank:
            top -= 1
        while bottom < last and self._blank(bottom + 1) == blank:
            bottom += 1
        if around:
            if bottom < last:
                bottom += 1
                while bottom < last and self._blank(bottom + 1) != blank:
                    bottom += 1
            elif not blank:
                while top > 0 and self._blank(top - 1):
                    top -= 1
        return (top, 0), (bottom, len(self.document.get_line(bottom))), True

    def _quote_object(self, row: int, col: int, line: str, quote: str, around: bool):
        marks = [i for i, char in enumerate(line) if char == quote and (i == 0 or line[i - 1] != "\\")]
        pairs = list(zip(marks[0::2], marks[1::2]))
        chosen = next((p for p in pairs if p[0] <= col <= p[1]), None) or next((p for p in pairs if p[0] > col), None)
        if chosen is None:
            return None
        first, second = chosen
        if not around:
            return (row, first + 1), (row, second), False
        end = second + 1
        while end < len(line) and line[end] in " \t":
            end += 1
        return (row, first), (row, end), False

    def _bracket_object(self, pair: str, around: bool, n: int = 1):
        row, col = self.cursor_location
        line = self.document.get_line(row)
        here = line[col] if col < len(line) else ""
        opener = (row, col) if here == pair[0] else self._scan_open(pair, (row, col - 1))
        for _ in range(n - 1):
            if opener is not None:
                opener = self._scan_open(pair, (opener[0], opener[1] - 1)) if opener[1] else self._scan_open(pair, (opener[0] - 1, 10**9)) if opener[0] else None
        if opener is None:
            return None
        closer = self._scan_close(pair, (opener[0], opener[1] + 1))
        if closer is None:
            return None
        if around:
            return opener, (closer[0], closer[1] + 1), False
        start, end = (opener[0], opener[1] + 1), closer
        if start[1] == len(self.document.get_line(start[0])) and end[0] > start[0] and not self.document.get_line(end[0])[: end[1]].strip():
            if end[0] - 1 < start[0] + 1:
                return start, start, False
            return (start[0] + 1, 0), (end[0] - 1, len(self.document.get_line(end[0] - 1))), True
        return start, end, False


    def _operator(self, op: str, key, n: int) -> None:
        """An operator (d c y > < g~ gu gU) applied over a motion, with vim's rules for the end:
        e E % f t and ge include the character under the cursor; an exclusive motion that ends at
        the start of a line stops at the end of the line before, or becomes linewise when it
        also started at or before the first non-blank; w stops at the end of the last word."""
        start = self.cursor_location
        key = self._resolve(key)
        linewise = None if key is None else self._move(key, n)
        if linewise is None:
            self.move_cursor(start)
            return
        end = self.cursor_location
        if linewise:
            self._apply_operator(op, start, end, True)
            return
        if op == "c" and key in ("w", "W"):
            self.move_cursor(start)
            end = self._word_end(_WORD if key == "w" else _BIG_WORD)
            end = (end[0], end[1] + 1)
        elif key in ("w", "W") and end[0] > start[0]:
            end = max(start, (end[0] - 1, len(self.document.get_line(end[0] - 1))))
        elif end >= start and (key in _INCLUSIVE or (isinstance(key, tuple) and key[0] in "ft`")):
            end = (end[0], end[1] + 1)
        elif end[1] == 0 and end[0] > start[0] and not (isinstance(key, tuple) and key[0] in "ft`"):
            first = self.document.get_line(start[0])
            if start[1] <= len(first) - len(first.lstrip()):
                self._apply_operator(op, start, (end[0] - 1, 0), True)
                return
            end = (end[0] - 1, len(self.document.get_line(end[0] - 1)))
        self._apply_operator(op, start, end, False)

    def _object_operator(self, op: str, spec: str, n: int = 1) -> None:
        """An operator on a text object such as iw, a(, ip."""
        found = self._text_object(spec, n)
        if found is None:
            return
        start, end, linewise = found
        self._apply_operator(op, start, end, linewise)

    def _apply_operator(self, op: str, start: Location, end: Location, linewise: bool) -> None:
        """Run `op` over start..end (exclusive end; linewise: whole rows)."""
        a, b = sorted([start, end])
        top, bottom = a[0], b[0]
        if op in ("d", "c", "y"):
            if linewise:
                self.move_cursor(a)
                self._lines_operator(op, top, bottom)
            else:
                self._chars_operator(op, a, b)
        elif op in (">", "<"):
            self._shift(top, bottom, 1, op == ">")
        elif op == "=":
            self._reindent_rows(top, bottom)
        else:
            change = {"g~": str.swapcase, "gu": str.lower, "gU": str.upper}[op]
            if linewise:
                a, b = (top, 0), (bottom, len(self.document.get_line(bottom)))
            self._case_edit(a, b, change)
            self._clamp()

    def _lines_operator(self, op: str, row: int, end_row: int) -> None:
        """d/c/y on whole lines row..end_row (dd, cc, yy and the linewise motions)."""
        last = self.document.line_count - 1
        self._yank_lines(row, end_row)
        if op == "y":
            self._copy_register()
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

    def _store(self, text: str, linewise: bool) -> None:
        """A yank or delete fills the unnamed register and the named one chosen with \"x (\"X appends, \"_ discards)."""
        name, self._use_register = self._use_register, ""
        if name == "_":
            return
        VimTextArea.register, VimTextArea.register_linewise = text, linewise
        if name.isupper():
            old, _ = VimTextArea.registers.get(name.lower(), ("", linewise))
            VimTextArea.registers[name.lower()] = ((old + "\n" + text) if old and linewise else old + text, linewise)
        elif name:
            VimTextArea.registers[name] = (text, linewise)

    def _fetch(self) -> tuple[str, bool]:
        """The register a put uses: the one chosen with \"x, else the unnamed one."""
        name, self._use_register = self._use_register, ""
        if name:
            return VimTextArea.registers.get(name.lower(), ("", False))
        return VimTextArea.register, VimTextArea.register_linewise

    def _chars_operator(self, op: str, start: Location, end: Location) -> None:
        """d/c/y on the characters from `start` up to, not including, `end`."""
        a, b = sorted([start, end])
        text = self.get_text_range(a, b)
        if text:
            self._store(text, False)
        else:
            self._use_register = ""
        if op == "y":
            if text:
                self._copy_register()
            self.move_cursor(a)
            self._clamp()
            return
        if text:
            self.delete(a, b, maintain_selection_offset=False)
        self.move_cursor(a)
        if op == "c":
            self.set_mode("insert")
        else:
            self._clamp()

    def _copy_register(self) -> None:
        """A yank also goes to the system clipboard (OSC 52), so it pastes in other programs."""
        text = VimTextArea.register + ("\n" if VimTextArea.register_linewise else "")
        try:
            self.app.copy_to_clipboard(text)
        except Exception:
            pass

    def _yank_lines(self, first: int, last: int) -> None:
        text = self.get_text_range((first, 0), (last, len(self.document.get_line(last))))
        self._store(text, True)

    def _paste(self, after: bool, n: int) -> None:
        text, linewise = self._fetch()
        if not text:
            return
        row, col = self.cursor_location
        line = self.document.get_line(row)
        if linewise:
            block = "\n".join([text] * n)
            if after:
                self.insert("\n" + block, (row, len(line)), maintain_selection_offset=False)
                self.move_cursor((row + 1, 0))
            else:
                self.insert(block + "\n", (row, 0), maintain_selection_offset=False)
                self.move_cursor((row, 0))
        else:
            at = (row, min(len(line), col + 1)) if after and line else (row, col)
            result = self.insert(text * n, at, maintain_selection_offset=False)
            self.move_cursor(self._before(result.end_location))
            self._clamp()

    def _reindent_rows(self, top: int, bottom: int) -> None:
        """= on lines top..bottom: re-indent them so they fit the structure of the whole cell."""
        lines = [self.document.get_line(row) for row in range(self.document.line_count)]
        fixed = reindent(lines)
        if fixed[top : bottom + 1] != lines[top : bottom + 1]:
            self.history.checkpoint()
            self.replace("\n".join(fixed[top : bottom + 1]), (top, 0), (bottom, len(lines[bottom])), maintain_selection_offset=False)
            self.history.checkpoint()
        first = self.document.get_line(top)
        self.move_cursor((top, len(first) - len(first.lstrip())))

    def _shift(self, top: int, bottom: int, n: int, right: bool) -> None:
        """> and <: indent or dedent lines top..bottom by n levels as one edit; blank lines stay empty."""
        lines = [self.document.get_line(row) for row in range(top, bottom + 1)]
        width = n * len(INDENT)
        if right:
            shifted = [INDENT * n + line if line.strip() else line for line in lines]
        else:
            shifted = [line[min(width, len(line) - len(line.lstrip(" "))) :] for line in lines]
        if shifted != lines:
            self.history.checkpoint()
            self.replace("\n".join(shifted), (top, 0), (bottom, len(lines[-1])), maintain_selection_offset=False)
        first = self.document.get_line(top)
        self.move_cursor((top, len(first) - len(first.lstrip())))

    def _join_rows(self, top: int, bottom: int, spaces: bool) -> None:
        """J / gJ on lines top..bottom (at least two) as one edit; J strips indents and adds a space."""
        bottom = min(max(bottom, top + 1), self.document.line_count - 1)
        if bottom <= top:
            return
        joined = self.document.get_line(top)
        col = 0
        for row in range(top + 1, bottom + 1):
            line = self.document.get_line(row)
            sep = ""
            if spaces:
                line = line.lstrip()
                if joined and line and not joined.endswith((" ", "\t")) and not line.startswith(")"):
                    sep = " "
            col = len(joined)
            joined += sep + line
        self.history.checkpoint()
        self.replace(joined, (top, 0), (bottom, len(self.document.get_line(bottom))), maintain_selection_offset=False)
        self.move_cursor((top, col))
        self._clamp()

    def _before(self, location: Location) -> Location:
        """The position one character before `location` (a line end counts as a character)."""
        row, col = location
        if col:
            return (row, col - 1)
        return (row - 1, len(self.document.get_line(row - 1))) if row else (0, 0)

    def _after(self, location: Location) -> Location:
        """The position one character after `location` (a line end counts as a character)."""
        row, col = location
        if col < len(self.document.get_line(row)):
            return (row, col + 1)
        return (row + 1, 0) if row < self.document.line_count - 1 else (row, col)


    def _enter_visual(self, kind: str, anchor: Location, head: Location | None = None) -> None:
        """Start (or switch to) charwise "v" or linewise "V" visual mode, anchored at `anchor`."""
        self.pending = self.count = ""
        if not self.visual:
            self.highlight_cursor_line = False
        self.visual = kind
        self._anchor = anchor
        self._line_cache.clear()
        if head is not None:
            self.move_cursor(head)
        self._show_visual()
        self.post_message(self.ModeChanged(self, self.mode))

    def _exit_visual(self, cursor: Location | None = None, post: bool = True) -> None:
        """Leave visual mode, remember the selection for gv, and put the cursor at `cursor` (default: where it is)."""
        head = self.cursor_location
        self._last_visual = (self.visual, self._anchor, head)
        self.visual = self.pending = self.count = ""
        self.highlight_cursor_line = True
        self._line_cache.clear()
        self.move_cursor(cursor or head)
        self._clamp()
        self.refresh()
        if post:
            self.post_message(self.ModeChanged(self, self.mode))

    def _show_visual(self) -> None:
        """Turn the anchor and the cursor into the TextArea selection that draws like vim's.

        The selection ends at the cursor, which is drawn as a block. Charwise, the anchor's
        own character is included; linewise, get_line paints whole lines.
        """
        anchor, head = self._anchor, self.cursor_location
        if self.visual == "v":
            start = anchor if head >= anchor else (anchor[0], anchor[1] + 1)
        elif head[0] > anchor[0] or (head[0] == anchor[0] and head[1] > 0):
            start = (anchor[0], 0)
        else:
            start = (anchor[0], len(self.document.get_line(anchor[0])))
        self.selection = Selection(start, head)

    def _reselect(self) -> None:
        """gv: select the last visual area again."""
        if self._last_visual is None:
            return
        kind, anchor, head = self._last_visual
        clamp = self.clamp_visitable
        self._enter_visual(kind, clamp(anchor), clamp(head))

    def _visual_range(self) -> tuple[Location, Location, int, int]:
        """(start, exclusive end, top row, bottom row) of the selection; linewise spans whole lines."""
        first, last = sorted([self._anchor, self.cursor_location])
        if self.visual == "V":
            return (first[0], 0), (last[0], len(self.document.get_line(last[0]))), first[0], last[0]
        return first, self._after(last), first[0], last[0]

    def _visual(self, key: str) -> bool:
        """Handle one visual-mode key. Returns False if the key means nothing here."""
        if key.isdigit() and (key != "0" or self.count) and not self._wants_char():
            self.count += key
            return True
        n = int(self.count or 1)
        pending, self.pending, self.count = self.pending, "", ""
        if pending == "g":
            if key == "g":
                self._visual_motion("gg", n)
            elif key == "J":
                self._visual_join(spaces=False)
            elif key == "v":
                self._reselect()
            return True
        if pending in ("i", "a"):
            self._visual_object(pending + key)
            return True
        if pending in _FIND_KEYS:
            if len(key) == 1:
                self._visual_motion((pending, key), n)
            return True
        if pending == "r":
            if len(key) == 1:
                self._visual_edit(lambda text: "".join(char if char == "\n" else key for char in text))
            return True
        if pending == '"':
            if len(key) == 1:
                self._use_register = key
            return True
        match key:
            case "v" | "V":
                if key == self.visual:
                    self._exit_visual()
                else:
                    self._enter_visual(key, self._anchor)
            case "o" | "O":
                self._anchor, head = self.cursor_location, self._anchor
                self.move_cursor(head)
                self._show_visual()
            case "y" | "Y":
                self._visual_operator("y", key == "Y")
            case "d" | "x" | "X" | "D":
                self._visual_operator("d", key in ("X", "D"))
            case "c" | "s":
                self._visual_operator("c", False)
            case "C" | "S" | "R":
                self._visual_operator("c", True)
            case "p" | "P":
                self._visual_put(key == "P", n)
            case ">" | "<":
                _, _, top, bottom = self._visual_range()
                self._exit_visual()
                self._shift(top, bottom, n, key == ">")
            case "=":
                _, _, top, bottom = self._visual_range()
                self._exit_visual()
                self._reindent_rows(top, bottom)
            case "J":
                self._visual_join(spaces=True)
            case "~":
                self._visual_edit(str.swapcase)
            case "u":
                self._visual_edit(str.lower)
            case "U":
                self._visual_edit(str.upper)
            case "g" | "i" | "a" | "r" | "f" | "F" | "t" | "T" | '"':
                self.pending, self.count = key, str(n) if n > 1 else ""
            case ":" | "/" | "K":
                pass
            case _:
                return self._visual_motion(key, n)
        return True

    def _visual_motion(self, key, n: int) -> bool:
        """Move the free end of the selection; False if `key` is not a motion."""
        if self._move(key, n) is None:
            self._show_visual()
            return False
        if key not in ("$", "end"):
            self._clamp()
        self._show_visual()
        return True

    def _visual_object(self, spec: str) -> None:
        """iw, a(, ip, ... in visual mode: select the object (ip and ap switch to linewise)."""
        found = self._text_object(spec)
        if found is None:
            return
        start, end, linewise = found
        if end <= start:
            return
        kind = "V" if linewise else self.visual
        self._enter_visual(kind, start, self._before(end) if not linewise else end)

    def _visual_operator(self, op: str, lines: bool) -> None:
        """y, d or c on the selection; `lines` makes it linewise like V (Y, D, X, C, S, R)."""
        start, end, top, bottom = self._visual_range()
        first = min(self._anchor, self.cursor_location)
        linewise = lines or self.visual == "V"
        self._exit_visual((top, 0) if linewise and op != "y" else first, post=op != "c")
        if linewise:
            self._lines_operator(op, top, bottom)
        else:
            self._chars_operator(op, start, end)
        if op == "y":
            self.move_cursor(first)
            self._clamp()

    def _visual_put(self, keep_register: bool, n: int) -> None:
        """p / P: replace the selection with the register (p puts the replaced text in the register)."""
        text, text_lines = self._fetch()
        start, end, top, bottom = self._visual_range()
        selection_lines = self.visual == "V"
        replaced = self.get_text_range(start, end)
        self._exit_visual(start)
        if not text:
            return
        block = ("\n" if text_lines else "").join([text] * n)
        self.history.checkpoint()
        if selection_lines:
            self.replace(block, start, end, maintain_selection_offset=False)
            cursor = (top, 0)
        elif text_lines:
            self.replace("\n" + block + "\n", start, end, maintain_selection_offset=False)
            cursor = (top + 1, 0)
        else:
            result = self.replace(block, start, end, maintain_selection_offset=False)
            cursor = self._before(result.end_location)
        self.history.checkpoint()
        self.move_cursor(cursor)
        self._clamp()
        if not keep_register:
            self._store(replaced, selection_lines)

    def _visual_join(self, spaces: bool) -> None:
        _, _, top, bottom = self._visual_range()
        self._exit_visual((top, 0))
        self._join_rows(top, bottom, spaces)

    def _visual_edit(self, change) -> None:
        """~ u U r<char>: replace the selected text with `change(text)` as one edit; cursor at the start."""
        start, end, _, _ = self._visual_range()
        text = self.get_text_range(start, end)
        self._exit_visual(start)
        new = change(text)
        if new != text:
            self.history.checkpoint()
            self.replace(new, start, end, maintain_selection_offset=False)
            self.history.checkpoint()
        self.move_cursor(start)
        self._clamp()


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
        if y + 1 + height > self.screen.size.height:
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
        if self.visual:
            self._exit_visual()
        super()._on_blur(event)
