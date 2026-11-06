"""A TextArea with vim-style modal editing and kernel-backed Tab completion.

Normal mode follows vim: h j k l w b e W B E ge gE 0 ^ $ g_ gm | _ + - H M L gg G
f F t T ; , % ( ) { } [( [{ ]) ]} [[ ]] [] ][ [m ]m, counts, i a I A gi gI o O R
r x X s S D C Y p P gp gP J gJ ~ u U ctrl+r . & g&, the operators d c y > < = g~
gu gU g? gq gw with any motion or text object (iw aw iW aW ip ap is as i" a" i' a'
i` a` i( a( ib i[ a[ i{ a{ iB i< a<), marks, registers ("a "A "_ "0 "1-"9 "- ". "+),
macros, the jump list (ctrl+o, tab, '' ``), the change list (g; g,), scrolling
(ctrl+d ctrl+u ctrl+f ctrl+b ctrl+e ctrl+y zz zt zb z. z<enter> z-), ZZ ZQ, @:,
ga, gd, ctrl+g, ctrl+a ctrl+x. v, V and ctrl+v start charwise, linewise and
blockwise visual mode. Insert mode adds ctrl+w ctrl+u ctrl+t ctrl+d ctrl+r ctrl+a
ctrl+e ctrl+y ctrl+n ctrl+p ctrl+v ctrl+o. Visual mode belongs to one editor:
losing focus ends it. Cell-level keys are posted as messages for the app: j/k past
the first/last line move to a neighbouring cell (EdgeReached), "/" and "?" ask for a
jump or a search (SearchRequested), n N * # repeat or start a search (SearchNext),
escape with nothing pending leaves the editor (LeaveEditor), ZZ ZQ & @: run an ex
command (ExRun). Every match of the active search is painted with SEARCH_STYLE.
The cursor is a block in the colour of the mode's badge (MODE_CURSOR_COLORS).
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
_OPERATORS = ("gu", "gU", "g~", "g?", "gq", "gw", "d", "c", "y", ">", "<", "=")
_JUMP_KEYS = frozenset(("G", "gg", "%", "(", ")", "{", "}", "H", "M", "L", "[[", "]]", "[]", "][", "gd", "gD"))
_NORMAL_ALIASES = {"ctrl+n": "j", "ctrl+p": "k", "ctrl+j": "j", "ctrl+m": "enter"}
_INSERT_CTRL = frozenset(("ctrl+w", "ctrl+u", "ctrl+t", "ctrl+d", "ctrl+r", "ctrl+a", "ctrl+e", "ctrl+y", "ctrl+n", "ctrl+p", "ctrl+v", "ctrl+j", "ctrl+k"))
_BRACKET_MOTIONS = {"[(": "()", "[{": "{}", "])": "()", "]}": "{}"}
_DEFINITION = re.compile(r"\s*(async\s+def|def|class)\b")
_SENTENCE_START = re.compile(r"(?<=[.!?])[)\]\"']*\s+(?=\S)|\n[ \t]*\n\s*(?=\S)")
TEXT_WIDTH = 79
CELL_MODE_COLOR = "#3b82f6"
INSERT_COLOR = "#a3e635"
MODE_CURSOR_COLORS = {"normal": "#f5c451", "insert": INSERT_COLOR, "visual": "#c084fc", "replace": "#f87171", "cell": CELL_MODE_COLOR}
_DEDENT_KEYWORDS = ("elif ", "elif(", "else", "except", "finally", "case ")
_BLOCK_ENDERS = ("return", "break", "continue", "pass", "raise")
_INCLUSIVE = ("e", "E", "ge", "gE", "%", "g_", "])", "]}")
_NUMBER = re.compile(r"-?\d+")
_VISUAL_NAMES = {"v": "visual", "V": "v-line", "b": "v-block"}
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
DEBUG_LINE_STYLE = Style(bgcolor="#5f0000", bold=True)
BREAK_LINE_STYLE = Style(underline=True, color="#ff5f5f")
_REVERSE = Style(reverse=True)
INSERT_CURSOR_SHAPES = {"bar": "\x1b[5 q", "underline": "\x1b[3 q", "block": ""}
SHOW_CURSOR = "\x1b[?25h"
SHOW_BAR_CURSOR = INSERT_CURSOR_SHAPES["bar"] + SHOW_CURSOR
HIDE_CURSOR = "\x1b[?25l"
RESET_CURSOR_COLOR = "\x1b]112\x07"
RESET_CURSOR_SHAPE = "\x1b[0 q" + RESET_CURSOR_COLOR
_BAR_MODES = ("insert", "replace")
_MODE_THEMES: dict[tuple[str, str], dict[str, TextAreaTheme]] = {}


def mode_themes(insert: TextAreaTheme, normal: TextAreaTheme) -> dict[str, TextAreaTheme]:
    """One editor theme per vim mode, alike but for the cursor: a block in the colour of the
    mode's badge in the status bar (MODE_CURSOR_COLORS). Built once per colorscheme."""
    key = (insert.name, normal.name)
    themes = _MODE_THEMES.get(key)
    if themes is None:
        themes = _MODE_THEMES[key] = {
            mode: dataclasses.replace(
                insert if mode in _BAR_MODES else normal,
                name=insert.name if mode == "insert" else normal.name if mode == "normal" else f"{insert.name}-{mode}",
                cursor_style=Style(color="black", bgcolor=color, bold=True),
            )
            for mode, color in MODE_CURSOR_COLORS.items()
        }
    return themes


def terminal_cursor_color(mode: str) -> str:
    """OSC 12: the terminal's own cursor (bar or underline shape) takes the mode's colour too."""
    return f"\x1b]12;{MODE_CURSOR_COLORS.get(mode, 'green')}\x07"


@functools.cache
def _rot13_table() -> dict[int, int]:
    lower, upper = "abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return str.maketrans(lower + upper, lower[13:] + lower[:13] + upper[13:] + upper[:13])


def rot13(text: str) -> str:
    return text.translate(_rot13_table())


def vim_regex(pattern: str) -> str:
    """A vim (magic) pattern as a Python regex: \\( \\) \\| \\{ \\+ \\= \\? \\< \\> are special,
    bare ( ) | { } + ? are literal. A pattern starting with \\v is already Python-like."""
    if pattern.startswith("\\v"):
        return pattern[2:]
    out = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "\\" and i + 1 < len(pattern):
            nxt = pattern[i + 1]
            i += 2
            if nxt in "<>":
                out.append(r"\b")
            elif nxt in "()|{}+?":
                out.append(nxt)
            elif nxt == "=":
                out.append("?")
            else:
                out.append("\\" + nxt)
            continue
        out.append("\\" + char if char in "()|{}+?" else char)
        i += 1
    return "".join(out)


def vim_replacement(text: str):
    """A :s replacement as a function of the match: & and \\0-\\9 insert groups, \\r \\n a line
    break, \\t a tab, \\u \\l change the next character's case, \\U \\L until \\E or \\e."""
    tokens: list[tuple[str, object]] = []
    i = 0
    while i < len(text):
        char = text[i]
        if char == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            i += 2
            if nxt.isdigit():
                tokens.append(("group", int(nxt)))
            elif nxt in "rn":
                tokens.append(("text", "\n"))
            elif nxt == "t":
                tokens.append(("text", "\t"))
            elif nxt in "uUlLeE":
                tokens.append(("case", nxt))
            else:
                tokens.append(("text", nxt))
            continue
        tokens.append(("group", 0) if char == "&" else ("text", char))
        i += 1

    def build(match: re.Match) -> str:
        out = []
        one = span = ""
        for kind, value in tokens:
            if kind == "case":
                if value in "ul":
                    one = value
                else:
                    span = value if value in "UL" else ""
                continue
            piece = value if kind == "text" else (match.group(value) or "")
            if span:
                piece = piece.upper() if span == "U" else piece.lower()
            if one and piece:
                piece = (piece[0].upper() if one == "u" else piece[0].lower()) + piece[1:]
                one = ""
            out.append(piece)
        return "".join(out)

    return build


def _split_unescaped(text: str, delimiter: str) -> list[str]:
    parts, current = [], []
    i = 0
    while i < len(text):
        char = text[i]
        if char == "\\" and i + 1 < len(text):
            current.append(delimiter if text[i + 1] == delimiter else text[i : i + 2])
            i += 2
            continue
        if char == delimiter:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
        i += 1
    parts.append("".join(current))
    return parts


_ADDRESS = r"(?:[.$]|\d+)"
_RANGE = rf"%|'<,'>|{_ADDRESS}(?:,{_ADDRESS})?"
_GLOBAL = re.compile(rf"({_RANGE})?(g!|global!|g|global|v|vglobal)(?=[^\w\s])(.*)$")
_LINE_COMMAND = re.compile(rf"({_RANGE})?(d|delete|j|join|>+|<+|m|move|t|co|norm|normal)!?(?=$|[^A-Za-z_])(.*)$")


def _split_first(text: str, delimiter: str) -> tuple[str, str]:
    """`text` up to the first unescaped `delimiter` (\\delimiter stays as the delimiter), and the rest after it."""
    out = []
    i = 0
    while i < len(text):
        char = text[i]
        if char == "\\" and i + 1 < len(text):
            out.append(delimiter if text[i + 1] == delimiter else text[i : i + 2])
            i += 2
            continue
        if char == delimiter:
            return "".join(out), text[i + 1 :]
        out.append(char)
        i += 1
    return "".join(out), ""


def parse_global(command: str) -> tuple[str, bool, str, str] | None:
    """:[range]g/pattern/command, :g!/pattern/ and :v/pattern/ as (range, invert, pattern, command)."""
    match = _GLOBAL.match(command.strip())
    if match is None:
        return None
    span, name, rest = match.group(1) or "", match.group(2), match.group(3)
    pattern, command_text = _split_first(rest[1:], rest[0])
    return span, name.endswith("!") or name.startswith("v"), pattern, command_text.strip()


def parse_line_command(command: str) -> tuple[str, str, str] | None:
    """:[range]d / j / > / < / m N / t N / co N / normal keys as (range, name, argument)."""
    match = _LINE_COMMAND.match(command.strip())
    if match is None:
        return None
    return match.group(1) or "", match.group(2), match.group(3)


_SUBSTITUTE = re.compile(r"(%|'<,'>|(?:[.$]|\d+)(?:,(?:[.$]|\d+))?)?(s(?:ubstitute)?|&&?)(?=$|[^\w\s])(.*)$")


def prepare_substitute(pattern: str | None, replacement: str | None, flags: str):
    """Resolve a parsed :s against the last one (missing pattern: repeat it; empty pattern: the
    last pattern) and compile it: (regex, replacement function, flags). Case: i ignores, I
    matches, else smart case like the editor's search."""
    last = VimTextArea.last_substitute
    if pattern is None:
        if last is None:
            raise ValueError("No previous substitute")
        pattern, replacement = last[0], last[1]
        flags = (last[2] if "&" in flags else "") + flags.replace("&", "")
    elif not pattern:
        pattern = VimTextArea.last_pattern or (last[0] if last else "")
        if not pattern:
            raise ValueError("No previous pattern")
    replacement = replacement or ""
    flags = flags.replace("&", "")
    VimTextArea.last_substitute = (pattern, replacement, flags)
    VimTextArea.last_pattern = pattern
    return compile_pattern(pattern, flags), vim_replacement(replacement), flags


def compile_pattern(pattern: str, flags: str = "") -> re.Pattern:
    if "i" in flags:
        case = re.IGNORECASE
    elif "I" in flags or any(char.isupper() for char in pattern):
        case = 0
    else:
        case = re.IGNORECASE
    return re.compile(vim_regex(pattern), case)


def parse_substitute(command: str) -> tuple[str, str | None, str | None, str] | None:
    """:[range]s/pattern/replacement/[flags], :[range]& and :[range]&& as
    (range, pattern, replacement, flags); pattern None repeats the last one. None if not a :s."""
    match = _SUBSTITUTE.match(command.strip())
    if match is None:
        return None
    span, name, rest = match.group(1) or "", match.group(2), match.group(3)
    if name.startswith("&"):
        return span, None, None, ("&" if name == "&&" else "") + rest.strip()
    if not rest:
        return span, None, None, ""
    if rest[0].isalpha() or rest[0] == "&":
        return span, None, None, rest
    parts = _split_unescaped(rest[1:], rest[0])
    return span, parts[0], parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else ""


def has_real_terminal(app) -> bool:
    return not app.is_headless


def write_to_terminal(app, data: str) -> None:
    driver = getattr(app, "_driver", None)
    if driver is not None and not driver.is_headless:
        driver.write(data)


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


def sync_terminal_cursor(app) -> None:
    """Show the terminal's own cursor, shaped by `VimTextArea.insert_cursor` (a bar or underline,
    coloured like the mode with OSC 12), while the focused editor is in insert or replace mode, and
    hide it otherwise: normal mode, and the "block" shape (the default), paint a block instead.
    Writes only on change."""
    shape = INSERT_CURSOR_SHAPES.get(VimTextArea.insert_cursor, "")
    focused = app.focused if shape and has_real_terminal(app) else None
    editor = (
        focused
        if isinstance(focused, VimTextArea) and VimTextArea.mode in _BAR_MODES and not focused.read_only
        else None
    )
    color = terminal_cursor_color(VimTextArea.mode) if editor is not None else ""
    previous = VimTextArea.bar_editor
    if editor is previous and color == VimTextArea.bar_color:
        return
    old_color = VimTextArea.bar_color
    VimTextArea.bar_editor, VimTextArea.bar_color = editor, color
    if editor is not None and (previous is None or color != old_color):
        write_to_terminal(app, shape + SHOW_CURSOR + color)
    elif editor is None and previous is not None:
        write_to_terminal(app, HIDE_CURSOR + RESET_CURSOR_COLOR)
    for changed in (previous, editor):
        if changed is not None and changed.is_attached:
            changed.forget_cursor_line()
            changed.refresh()
    if editor is not None:
        editor.cursor_blink = False


class VimTextArea(TextArea):
    selection: reactive[Selection] = reactive(Selection(), init=False, always_update=True, repaint=False)

    enabled = True
    line_numbers = True
    mode = "normal"
    bar_editor: VimTextArea | None = None
    bar_color = ""
    insert_cursor = "block"
    register = ""
    registers: dict[str, tuple[str, bool | None]] = {}
    last_substitute: tuple[str, str, str] | None = None
    last_pattern = ""
    macros: dict[str, list[tuple[str, str | None]]] = {}
    last_macro = ""
    register_linewise = False
    visual = ""
    search: re.Pattern | None = None
    debug_line: int | None = None
    debug_breaks: frozenset = frozenset()

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
        """':' opens the prompt; from visual mode it starts with the range '<,'>."""

        def __init__(self, editor: VimTextArea, initial: str = "") -> None:
            super().__init__()
            self.editor = editor
            self.initial = initial

    class SearchRequested(Message):
        """'/' (or '?', `backward`) in normal mode: the app asks where to jump."""

        def __init__(self, editor: VimTextArea, backward: bool = False) -> None:
            super().__init__()
            self.editor = editor
            self.backward = backward

    class HelpRequested(Message):
        """K: the app shows the kernel's help (docstring, signature) for the name at `offset` of `code`."""

        def __init__(self, editor: VimTextArea, code: str, offset: int, name: str) -> None:
            super().__init__()
            self.editor = editor
            self.code = code
            self.offset = offset
            self.name = name

    class FoldRequested(Message):
        """z c / z a / z M / z R ...: folds are cells here, so the app hides or shows cells' code.
        `action` is "close" (this cell), "toggle", "close_all", "open_all"."""

        def __init__(self, editor: VimTextArea, action: str) -> None:
            super().__init__()
            self.editor = editor
            self.action = action

    class ExRun(Message):
        """ZZ, ZQ, &, g& and @: run an ex command without the prompt (None: the last one again)."""

        def __init__(self, editor: VimTextArea, command: str | None) -> None:
            super().__init__()
            self.editor = editor
            self.command = command

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
        """space r (r alone is vim's replace here; r 6 is cell mode's): the app waits for a cell number and runs that cell."""

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
        self._jumps: list[Location] = []
        self._jump_index = 0
        self._changes: list[Location] = []
        self._change_index = 0
        self._line_undo: tuple[int, str] | None = None
        self._inserted = ""
        self._insert_pending = ""
        self._block_eol = False
        self._block_insert: tuple[int, int, int, bool, int] | None = None
        self._theme_names: dict[str, str] = {}
        self._cell_cursor = False
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
        if VimTextArea.bar_editor is self:
            VimTextArea.bar_editor = None
            write_to_terminal(self.app, HIDE_CURSOR)

    def set_editor_theme(self, insert: TextAreaTheme, normal: TextAreaTheme) -> None:
        """Use the colorscheme of `insert` / `normal`, with a cursor coloured by mode (mode_themes)."""
        themes = mode_themes(insert, normal)
        for theme in themes.values():
            self.register_theme(theme)
        self._theme_names = {mode: theme.name for mode, theme in themes.items()}
        self._apply_mode()
        if self._cell_cursor:
            self._theme = self._themes[self._theme_names["cell"]]

    def show_cell_cursor(self, on: bool) -> None:
        """Cell mode: the selected cell's editor shows where its cursor is as a blue block
        (MODE_CURSOR_COLORS["cell"]) without having focus. Only the cursor style is swapped, not
        the theme, so a cell move restyles nothing; one line is repainted."""
        if on == self._cell_cursor:
            return
        self._cell_cursor = on
        name = self._theme_names["cell"] if on else self.theme
        theme = self._themes.get(name)
        if theme is not None:
            self._theme = theme
        if self.is_mounted:
            row = self.cursor_location[0]
            self.forget_cursor_line()
            self._refresh_rows(row, row)


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
        if line_index == self.debug_line:
            line.stylize(DEBUG_LINE_STYLE)
        elif line_index in self.debug_breaks:
            line.stylize(BREAK_LINE_STYLE)
        if self.visual in ("V", "b") and self._theme is not None and self._theme.selection_style is not None:
            first, last = sorted((self._anchor[0], self.cursor_location[0]))
            if first <= line_index <= last:
                if self.visual == "V":
                    line.stylize(self._theme.selection_style)
                else:
                    _, _, left, right = self._block_bounds()
                    line.stylize(self._theme.selection_style, left, right + 1)
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

    def set_debug_line(self, row: int | None) -> None:
        """The line a paused debugger is on, painted in DEBUG_LINE_STYLE (None clears it)."""
        if row != self.debug_line:
            self.debug_line = row
            self.repaint_search()

    def set_debug_breaks(self, rows) -> None:
        """The lines that hold a pdb breakpoint (`b 12` typed with e), painted in BREAK_LINE_STYLE."""
        rows = frozenset(rows)
        if rows != self.debug_breaks:
            self.debug_breaks = rows
            self.repaint_search()

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
        top = edit.top[0]
        if self._line_undo is None or self._line_undo[0] != top:
            self._line_undo = (top, self.document.get_line(top))
        result = super().edit(edit)
        self._note_change(result.end_location, self.document.line_count != rows_before)
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
        if mode in _BAR_MODES and VimTextArea.mode not in _BAR_MODES:
            self._inserted = self._insert_pending = ""
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
        sync_terminal_cursor(self.app)

    @property
    def _draw_cursor(self) -> bool:
        """While the terminal's own cursor (a bar or underline) marks the spot, no block is painted."""
        if self._cell_cursor and not self.has_focus:
            return True
        return VimTextArea.bar_editor is not self and TextArea._draw_cursor.fget(self)

    def forget_cursor_line(self) -> None:
        _, cursor_y = self._cursor_offset
        cache = self._line_cache
        for key in [key for key in cache.keys() if key[2] == cursor_y]:
            cache.discard(key)

    def _note_change(self, location: Location, lines_changed: bool) -> None:
        """Remember where the text changed (g; g,); a change that adds or removes lines ends U's line."""
        if lines_changed:
            self._line_undo = None
        changes = self._changes
        if changes and changes[-1][0] == location[0]:
            changes[-1] = location
        else:
            changes.append(location)
            if len(changes) > 100:
                del changes[0]
        self._change_index = len(changes)

    def _apply_mode(self) -> None:
        mode = self.mode if self.enabled else "insert"
        if mode == "normal" and self.visual:
            mode = "visual"
        theme = self._theme_names.get(mode) or self._theme_names["normal"]
        if self.theme != theme:
            self.theme = theme
        if self.has_focus:
            sync_terminal_cursor(self.app)
        self.cursor_blink = mode in _BAR_MODES and VimTextArea.bar_editor is not self

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
        """The insert-mode keys a replay needs: text, enter, backspace, tab, the ctrl keys and escape."""
        if self._insert_pending or key in _INSERT_CTRL:
            self._insert_ctrl(key, char)
        elif key == "escape":
            self._end_insert()
        elif key == "enter":
            self._inserted += "\n"
            if self.language == "python":
                self._newline()
            else:
                self.insert("\n")
        elif key == "backspace":
            self._inserted = self._inserted[:-1]
            if not self._dedent_on_backspace():
                self.action_delete_left()
        elif key == "tab":
            self._inserted += INDENT
            self.insert(INDENT)
        elif char and len(char) == 1 and char.isprintable():
            self._inserted += char
            self.insert(char)

    def _insert_ctrl(self, key: str, char: str | None) -> None:
        """vim's insert-mode ctrl keys: ctrl+w word and ctrl+u line start deleted, ctrl+t / ctrl+d
        indent / dedent the line, ctrl+r x puts register x, ctrl+a the text inserted last time,
        ctrl+e / ctrl+y the character below / above, ctrl+n / ctrl+p complete, ctrl+v the next
        key literally, ctrl+j a new line, ctrl+k and two characters a digraph (digraphs.py)."""
        row, col = self.cursor_location
        line = self.document.get_line(row)
        if self._insert_pending:
            pending, self._insert_pending = self._insert_pending, ""
            if pending == "ctrl+r" and char and len(char) == 1 and char.isprintable():
                self._use_register = char
                text, linewise = self._fetch()
                self._type(text + ("\n" if linewise else ""))
            elif pending == "ctrl+v":
                literal = "\t" if key == "tab" else "\n" if key == "enter" else char if char and len(char) == 1 else ""
                self._type(literal)
            elif pending == "ctrl+k" and char and len(char) == 1 and char.isprintable():
                self._insert_pending = pending + char
            elif pending.startswith("ctrl+k") and len(pending) == 7 and char and len(char) == 1 and char.isprintable():
                from .digraphs import lookup

                self._type(lookup(pending[-1], char))
            return
        match key:
            case "ctrl+w":
                before = line[:col]
                if not before:
                    self.action_delete_left()
                    return
                stripped = before.rstrip()
                words = list(_WORD.finditer(stripped))
                start = words[-1].start() if words else 0
                self.delete((row, start), (row, col), maintain_selection_offset=False)
            case "ctrl+u":
                indent = len(line) - len(line.lstrip())
                start = indent if col > indent else 0
                if start < col:
                    self.delete((row, start), (row, col), maintain_selection_offset=False)
                elif row:
                    self.action_delete_left()
            case "ctrl+t":
                self.insert(INDENT, (row, 0), maintain_selection_offset=False)
                self.move_cursor((row, col + len(INDENT)))
            case "ctrl+d":
                removed = min(len(INDENT), len(line) - len(line.lstrip(" ")))
                if removed:
                    self.delete((row, 0), (row, removed), maintain_selection_offset=False)
                    self.move_cursor((row, max(0, col - removed)))
            case "ctrl+r" | "ctrl+v" | "ctrl+k":
                self._insert_pending = key
            case "ctrl+a":
                self._type(VimTextArea.registers.get(".", ("", False))[0])
            case "ctrl+e" | "ctrl+y":
                other = row + (1 if key == "ctrl+e" else -1)
                if 0 <= other < self.document.line_count:
                    text = self.document.get_line(other)
                    if col < len(text):
                        self._type(text[col])
            case "ctrl+n" | "ctrl+p":
                if not self._replaying and self.language == "python":
                    self.run_worker(self._complete(), exclusive=True, group="complete")
            case "ctrl+j":
                self._insert_key("enter", None)

    def _type(self, text: str) -> None:
        """Insert `text` at the cursor as if typed (it counts for the . register)."""
        if text:
            self._inserted += text
            self.insert(text)

    def _end_insert(self) -> None:
        """Escape from insert or replace mode: back to normal mode, repeating a counted insert (3ix)."""
        if self._inserted:
            VimTextArea.registers["."] = (self._inserted, False)
        self._marks["^"] = self.cursor_location
        if self._block_insert is not None:
            self._finish_block_insert()
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
            elif event.key == "ctrl+o" and self.enabled and not self._insert_pending:
                event.stop()
                event.prevent_default()
                self._one_shot = True
                self.set_mode("normal")
            elif self.enabled and (self._insert_pending or event.key in _INSERT_CTRL):
                event.stop()
                event.prevent_default()
                self._insert_ctrl(event.key, event.character)
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
                self._inserted += "\n"
                self._newline()
            elif event.key == "backspace" and self._dedent_on_backspace():
                event.stop()
                event.prevent_default()
                self._inserted = self._inserted[:-1]
            else:
                await super()._on_key(event)
                if event.is_printable and event.character:
                    self._inserted += event.character
                elif event.key == "backspace":
                    self._inserted = self._inserted[:-1]
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
        if not self._wants_char():
            key = _NORMAL_ALIASES.get(key, key)
        had_count = bool(self.count)
        n = int(self.count or 1)
        pending, self.pending, self.count = self.pending, "", ""

        if (split := self._split_operator(pending)) is not None:
            op, suffix = split
            n *= self._op_count
            if suffix == "":
                if key in ("i", "a", "g", "[", "]", *_FIND_KEYS, *_MARK_KEYS):
                    self.pending, self._op_count = pending + key, n
                elif key == op[-1] or key == op:
                    row = self.cursor_location[0]
                    self._apply_operator(op, (row, 0), (min(self.document.line_count - 1, row + n - 1), 0), True)
                else:
                    self._op_count = 1
                    self._operator(op, key, n)
                return True
            self._op_count = 1
            if suffix in ("i", "a"):
                self._object_operator(op, suffix + key, n)
            elif suffix in ("[", "]"):
                self._operator(op, suffix + key, n)
            elif suffix == "g":
                if key == "g":
                    self._operator(op, "gg", n)
                elif key in ("e", "E", "_", "0", "$", "m"):
                    self._operator(op, "g" + key, n)
                elif len(op) == 2 and key == op[1]:
                    row = self.cursor_location[0]
                    self._apply_operator(op, (row, 0), (min(self.document.line_count - 1, row + n - 1), 0), True)
            elif len(key) == 1:
                self._operator(op, (suffix, key), n)
            return True
        if pending in _FIND_KEYS or pending in _MARK_KEYS:
            origin = self.cursor_location
            if len(key) == 1 and self._move((pending, key), n) is not None:
                self._clamp()
                if pending in _MARK_KEYS:
                    self._record_jump(origin)
            return True
        if pending == "r":
            if key == "enter" or (len(key) == 1 and key.isprintable()):
                self._replace_chars("\n" if key == "enter" else key, n)
            return True
        if pending == "z":
            self._z_scroll(key, n if had_count else None)
            return True
        if pending == "Z":
            if key in ("Z", "Q"):
                self.post_message(self.ExRun(self, "x" if key == "Z" else "q!"))
            return True
        if pending in ("[", "]"):
            self._jump_motion(pending + key, n)
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
            if key == ":":
                self.post_message(self.ExRun(self, None))
                return True
            name = VimTextArea.last_macro if key == "@" else key
            if name in VimTextArea.macros:
                VimTextArea.last_macro = name
                self._replay(VimTextArea.macros[name], n)
            return True
        if pending == "g":
            row, col = self.cursor_location
            if key in ("g", "d", "D"):
                self._jump_motion("g" + key, n)
            elif key in ("e", "E", "_", "0", "$", "m"):
                self._move("g" + key, n)
                if key != "$":
                    self._clamp()
            elif key in ("u", "U", "~", "?", "q", "w"):
                self.pending, self._op_count = "g" + key, n
            elif key == "v":
                self._reselect()
            elif key == "J":
                self._join_rows(row, row + max(n, 2) - 1, spaces=False)
            elif key in ("i", "I"):
                if key == "i" and "^" in self._marks:
                    self.move_cursor(self.clamp_visitable(self._marks["^"]))
                elif key == "I":
                    self.move_cursor((row, 0))
                self._insert_repeat = n
                self.set_mode("insert")
            elif key in ("p", "P"):
                self._paste(key == "p", n, cursor_after=True)
            elif key in ("*", "#"):
                self._search_word(key == "*", n)
            elif key in (";", ","):
                self._change_jump(-n if key == ";" else n)
            elif key == "a":
                line = self.document.get_line(row)
                if col < len(line):
                    char = line[col]
                    self.notify(f"<{char}> {ord(char)}, hex {ord(char):02x}, octal {ord(char):03o}")
            elif key == "&":
                self.post_message(self.ExRun(self, "%&&"))
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
            case "g" | " " | "f" | "F" | "t" | "T" | "r" | "z" | "Z" | "[" | "]":
                self.pending, self.count = key, str(n) if had_count else ""
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
            case "ctrl+v":
                self._block_eol = False
                self._enter_visual("b", self.cursor_location)
            case "J":
                self._join_rows(row, row + max(n, 2) - 1, spaces=True)
            case "U":
                self._undo_line()
            case "&":
                self.post_message(self.ExRun(self, "&"))
            case "ctrl+o":
                self._jump_older(n)
            case "tab" | "ctrl+i":
                self._jump_newer(n)
            case "ctrl+d" | "ctrl+u":
                rows = n if had_count else max(1, self._page() // 2)
                self._scroll_with_cursor(rows if key == "ctrl+d" else -rows)
            case "ctrl+f" | "ctrl+b":
                rows = max(1, self._page() - 2) * n
                self._scroll_with_cursor(rows if key == "ctrl+f" else -rows)
            case "ctrl+e" | "ctrl+y":
                box = self._scroller()
                if box is not None:
                    box.scroll_relative(y=n if key == "ctrl+e" else -n, animate=False, immediate=True)
            case "ctrl+g":
                total = self.document.line_count
                self.notify(f"line {row + 1} of {total} --{(row + 1) * 100 // total}%-- col {col + 1}")
            case "?":
                self.post_message(self.SearchRequested(self, backward=True))
            case "K":
                self._request_help(row, col, line)
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
            case "/":
                self.post_message(self.SearchRequested(self))
            case "n" | "N":
                self.post_message(self.SearchNext(self, n if key == "n" else -n))
            case "*" | "#":
                self._search_word(key == "*", n)
            case "u":
                self._no_dot = True
                self.undo()
            case "ctrl+r":
                self._no_dot = True
                self.redo()
            case ":":
                self.post_message(self.ExCommand(self))
            case _:
                if key in ("j", "down") and self.cursor_at_last_line:
                    self.post_message(self.EdgeReached(self, 1))
                elif key in ("k", "up") and self.cursor_at_first_line:
                    self.post_message(self.EdgeReached(self, -1))
                elif key in _JUMP_KEYS:
                    return self._jump_motion(key, n)
                elif self._move(key, n) is None:
                    return False
                else:
                    self._clamp()
        return True

    def _wants_char(self) -> bool:
        """The pending key takes any character next (f F t T r ` ' m \" q @), so a digit is not a count."""
        return self.pending[-1:] in (*_CHAR_KEYS, "r") and self.pending not in (" f", " r")

    def _search_word(self, forward: bool, n: int) -> None:
        """* # g* g#: search for the word under (or after) the cursor."""
        row, col = self.cursor_location
        word = word_at(self.document.get_line(row), col)
        if word is None:
            self.notify("No string under cursor", severity="warning")
            return
        self.move_cursor((row, word.start()))
        self.post_message(self.SearchNext(self, n if forward else -n, word.group()))

    def _jump_motion(self, key: str, n: int) -> bool:
        """A motion that counts as a jump (G gg % { } H M L [[ ...): the place it left joins the jump list."""
        origin = self.cursor_location
        if self._move(key, n) is None:
            return False
        self._clamp()
        if self.cursor_location != origin and key in _JUMP_KEYS:
            self._record_jump(origin)
        return True

    def _record_jump(self, origin: Location) -> None:
        """`origin` goes to the end of the jump list (ctrl+o returns there) and becomes the '' mark."""
        jumps = self._jumps
        del jumps[self._jump_index :]
        if not jumps or jumps[-1] != origin:
            jumps.append(origin)
            if len(jumps) > 100:
                del jumps[0]
        self._jump_index = len(jumps)
        self._marks["'"] = origin

    def _jump_older(self, n: int) -> None:
        """ctrl+o: back through the jump list; the first step remembers where it started."""
        if self._jump_index >= len(self._jumps):
            here = self.cursor_location
            self._record_jump(here)
            self._jump_index = len(self._jumps) - 1
        target = max(0, self._jump_index - n)
        if target != self._jump_index:
            self._jump_index = target
            self.move_cursor(self.clamp_visitable(self._jumps[target]))
            self._clamp()
            self._reveal_cursor()

    def _jump_newer(self, n: int) -> None:
        """tab (ctrl+i): forward through the jump list again."""
        target = min(len(self._jumps) - 1, self._jump_index + n)
        if target > self._jump_index:
            self._jump_index = target
            self.move_cursor(self.clamp_visitable(self._jumps[target]))
            self._clamp()
            self._reveal_cursor()

    def _change_jump(self, step: int) -> None:
        """g; / g,: older / newer places where the text changed."""
        if not self._changes:
            self.notify("Change list is empty", severity="warning")
            return
        target = max(0, min(len(self._changes) - 1, self._change_index + step))
        self._change_index = target
        self.move_cursor(self.clamp_visitable(self._changes[target]))
        self._clamp()

    def _replace_chars(self, char: str, n: int) -> None:
        """r<char>: replace n characters under the cursor with `char` (r<enter> breaks the line)."""
        row, col = self.cursor_location
        line = self.document.get_line(row)
        if col + n > len(line):
            return
        if char == "\n":
            self.replace("\n", (row, col), (row, col + n), maintain_selection_offset=False)
            self.move_cursor((row + 1, 0))
            return
        self.replace(char * n, (row, col), (row, col + n), maintain_selection_offset=False)
        self.move_cursor((row, col + n - 1))

    def _undo_line(self) -> None:
        """U: put the line back the way it was before the latest run of changes to it (U again redoes)."""
        row, _ = self.cursor_location
        saved = self._line_undo
        if saved is None or saved[0] != row:
            return
        current = self.document.get_line(row)
        if saved[1] != current:
            self.replace(saved[1], (row, 0), (row, len(current)), maintain_selection_offset=False)
            self._line_undo = (row, current)
            self.move_cursor((row, 0))

    def _scroller(self):
        """The nearest container that scrolls vertically (the notebook's list of cells)."""
        node = self.parent
        while node is not None and not getattr(node, "allow_vertical_scroll", False):
            node = node.parent
        return node

    def _page(self) -> int:
        """Rows the scrolling container shows (vim's window height)."""
        box = self._scroller()
        return max(1, box.scrollable_content_region.height if box is not None else self.size.height)

    def _cursor_offset_in(self, box) -> tuple[int, int]:
        """(screen row of the cursor - the top row of `box`, the rows `box` shows)."""
        row = self.cursor_location[0]
        y = self.content_region.y + row - self.scroll_offset.y
        area = box.scrollable_content_region
        return y - area.y, area.height

    def _reveal_cursor(self, place: str = "") -> None:
        """Scroll the container so the cursor line shows ("" just enough, or "top", "center", "bottom")."""
        box = self._scroller()
        if box is None or not self.is_mounted:
            return
        y, height = self._cursor_offset_in(box)
        if place == "top":
            delta = y
        elif place == "center":
            delta = y - height // 2
        elif place == "bottom":
            delta = y - height + 1
        elif y < 0:
            delta = y
        elif y >= height:
            delta = y - height + 1
        else:
            return
        if delta:
            box.scroll_relative(y=delta, animate=False, immediate=True)

    def _scroll_with_cursor(self, rows: int) -> None:
        """ctrl+d ctrl+u ctrl+f ctrl+b: scroll and move the cursor the same number of lines (within this cell)."""
        row, _ = self.cursor_location
        target = max(0, min(self.document.line_count - 1, row + rows))
        text = self.document.get_line(target)
        box = self._scroller()
        if box is not None:
            box.scroll_relative(y=rows, animate=False, immediate=True)
        self.move_cursor((target, len(text) - len(text.lstrip())))
        self._reveal_cursor()

    def _request_help(self, row: int, col: int, line: str) -> None:
        """K: ask the app for the help of the (dotted) name under the cursor."""
        word = word_at(line, col)
        if word is None or not (word.group()[0].isidentifier()):
            self.notify("No identifier under cursor", severity="warning")
            return
        start = word.start()
        while start > 1 and line[start - 1] == "." and (line[start - 2].isalnum() or line[start - 2] in "_)]"):
            start -= 1
            while start > 0 and (line[start - 1].isalnum() or line[start - 1] == "_"):
                start -= 1
        offset = self._offset((row, word.end()))
        self.post_message(self.HelpRequested(self, self.text, offset, line[start : word.end()]))

    def _z_scroll(self, key: str, line: int | None) -> None:
        """zz z. / zt z<enter> / zb z-: the cursor line to the middle / top / bottom (a count picks the line).
        zc zC za zA zM zR zE: folds, which are whole cells here (FoldRequested)."""
        fold = {"c": "close", "C": "close", "a": "toggle", "A": "toggle", "M": "close_all", "R": "open_all", "E": "open_all", "D": "open_all", "d": "open_all"}.get(key)
        if fold is not None:
            self.post_message(self.FoldRequested(self, fold))
            return
        place = {"z": "center", ".": "center", "t": "top", "enter": "top", "b": "bottom", "-": "bottom"}.get(key)
        if place is None:
            return
        if line is not None:
            self.move_cursor((min(self.document.line_count - 1, line - 1), self.cursor_location[1]))
        if key in (".", "enter", "-") or line is not None:
            text = self.document.get_line(self.cursor_location[0])
            self.move_cursor((self.cursor_location[0], len(text) - len(text.lstrip())))
        self._clamp()
        self._reveal_cursor(place)

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
            case "g_":
                target = min(last, row + n - 1)
                text = self.document.get_line(target)
                self.move_cursor((target, max(0, len(text.rstrip()) - 1)))
            case "g0":
                self.move_cursor((row, 0))
            case "g$":
                self.move_cursor((row, len(line)))
            case "gm":
                self.move_cursor((row, min(len(line), max(0, self.size.width - self.gutter_width) // 2)))
            case "(" | ")":
                self.move_cursor(self._sentence(1 if key == ")" else -1, n))
            case "[(" | "[{" | "])" | "]}":
                pair = _BRACKET_MOTIONS[key]
                target: Location | None = (row, col)
                for _ in range(n):
                    if target is None:
                        break
                    if key[0] == "[":
                        target = self._scan_open(pair, self._before(target) if target != (0, 0) else (0, -1))
                    else:
                        target = self._scan_close(pair, self._after(target) if target != self.document.end else target)
                if target is None:
                    return None
                self.move_cursor(target)
            case "[[" | "]]" | "[]" | "][" | "[m" | "]m" | "[M" | "]M":
                target_row = self._definition_row(1 if key[0] == "]" else -1, n)
                if target_row is None:
                    return None
                text = self.document.get_line(target_row)
                self.move_cursor((target_row, len(text) - len(text.lstrip())))
                return True
            case "gd" | "gD":
                word = word_at(line, col)
                if word is None or not word.group()[0].isidentifier():
                    return None
                found = re.search(rf"(?<!\w){re.escape(word.group())}(?!\w)", self.text)
                if found is None:
                    return None
                self.move_cursor(self._location(found.start()))
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
                mark = self._marks.get("'" if name in "'`" else name)
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


    def _sentence(self, direction: int, n: int) -> Location:
        """( / ): the start of the n-th sentence before / after the cursor. A sentence ends at
        . ! or ? followed by white space, or at a blank line."""
        text = self.text
        starts = [0] + [match.end() for match in _SENTENCE_START.finditer(text)]
        here = self._offset(self.cursor_location)
        if direction > 0:
            later = [start for start in starts if start > here]
            return self._location(later[n - 1]) if len(later) >= n else self.document.end
        earlier = [start for start in starts if start < here]
        return self._location(earlier[-n]) if len(earlier) >= n else (0, 0)

    def _definition_row(self, direction: int, n: int) -> int | None:
        """[[ ]] [m ]m: the n-th def or class line above / below the cursor (Python's sections)."""
        row = self.cursor_location[0]
        found = None
        for _ in range(n):
            rows = range(row + 1, self.document.line_count) if direction > 0 else range(row - 1, -1, -1)
            row = next((r for r in rows if _DEFINITION.match(self.document.get_line(r))), None)
            if row is None:
                break
            found = row
        return found

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
        if kind == "s":
            return self._sentence_object(around)
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

    def _sentence_object(self, around: bool):
        """is / as: the sentence under the cursor; as also takes the white space after it."""
        text = self.text
        here = self._offset(self.cursor_location)
        starts = [0] + [match.end() for match in _SENTENCE_START.finditer(text)]
        start = max(s for s in starts if s <= here)
        end = min((s for s in starts if s > here), default=len(text))
        if not around:
            end = start + len(text[start:end].rstrip())
        return self._location(start), self._location(end), False

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
        elif op in ("gq", "gw"):
            cursor = self.cursor_location
            self._format_rows(top, bottom)
            if op == "gw":
                self.move_cursor(self.clamp_visitable(cursor))
                self._clamp()
        else:
            change = {"g~": str.swapcase, "gu": str.lower, "gU": str.upper, "g?": rot13}[op]
            if linewise:
                a, b = (top, 0), (bottom, len(self.document.get_line(bottom)))
            self._case_edit(a, b, change)
            self._clamp()

    def _range_rows(self, span: str) -> tuple[int, int]:
        """An ex range as (top, bottom) rows of this editor: "" the cursor line, % every line,
        '<,'> the last visual area, N or N,M lines (1-based; . the cursor line, $ the last)."""
        row = self.cursor_location[0]
        last = self.document.line_count - 1
        if not span:
            return row, row
        if span == "%":
            return 0, last
        if span == "'<,'>":
            if self._last_visual is None:
                raise ValueError("No visual selection yet")
            _, anchor, head = self._last_visual
            return min(anchor[0], head[0], last), min(max(anchor[0], head[0]), last)

        def address(text: str) -> int:
            return row if text == "." else last if text == "$" else max(0, min(last, int(text) - 1))

        first, _, second = span.partition(",")
        top, bottom = address(first), address(second or first)
        return min(top, bottom), max(top, bottom)

    def substitute(self, span: str, pattern: str | None, replacement: str | None, flags: str) -> tuple[int, int]:
        """:[range]s/pattern/replacement/[flags] on this editor alone (the app spreads % over every
        cell with `substitute_rows`). Returns (substitutions, lines)."""
        prepared = prepare_substitute(pattern, replacement, flags)
        top, bottom = self._range_rows(span)
        return self.substitute_rows(top, bottom, *prepared)[:2]

    def substitute_rows(self, top: int, bottom: int, regex: re.Pattern, build, flags: str) -> tuple[int, int, int | None]:
        """Apply a prepared :s (prepare_substitute) to rows top..bottom as one undo step; the cursor
        goes to the last changed line. Returns (substitutions, lines, that line or None)."""
        count = 0
        changed_rows = 0
        new_lines = []
        last_changed = None
        for row in range(top, bottom + 1):
            line = self.document.get_line(row)
            new, made = regex.subn(build, line, count=0 if "g" in flags else 1)
            if made:
                count += made
                changed_rows += 1
                last_changed = row
            new_lines.append(new)
        if not count or "n" in flags:
            return count, changed_rows, None
        old_bottom = self.document.get_line(bottom)
        self.history.checkpoint()
        self.replace("\n".join(new_lines), (top, 0), (bottom, len(old_bottom)), maintain_selection_offset=False)
        self.history.checkpoint()
        target = last_changed + sum(line.count("\n") for line in new_lines[: last_changed - top + 1])
        target = min(target, self.document.line_count - 1)
        text = self.document.get_line(target)
        self.move_cursor((target, len(text) - len(text.lstrip())))
        return count, changed_rows, target

    def matching_rows(self, top: int, bottom: int, regex: re.Pattern, invert: bool = False) -> list[int]:
        """:g / :v: the rows in top..bottom whose text matches `regex` (or does not, with `invert`)."""
        return [row for row in range(top, bottom + 1) if bool(regex.search(self.document.get_line(row))) != invert]

    def global_command(self, rows: list[int], command: str) -> None:
        """:g/pattern/command on this editor: run `command` (s, d, j, >, <, m, t, co, normal) on each
        of `rows` in turn, following the lines as earlier commands add or remove lines."""
        substitute = parse_substitute(command)
        prepared = prepare_substitute(*substitute[1:]) if substitute is not None else None
        line_command = None if substitute is not None else parse_line_command(command)
        if prepared is None and line_command is None:
            raise ValueError(f"Not a command :g can run here: {command}")
        shift = 0
        for row in rows:
            current = row + shift
            if not 0 <= current < self.document.line_count:
                break
            before = self.document.line_count
            if prepared is not None:
                self.substitute_rows(current, current, *prepared)
                shift += self.document.line_count - before
                continue
            _, name, arg = line_command
            self.move_cursor((current, 0))
            destination = self.line_command(current, current, name, arg)
            if name in ("t", "co") and destination is not None and destination > current:
                continue
            if name in ("m", "move"):
                if destination is not None and destination > current:
                    shift -= 1
                continue
            shift += self.document.line_count - before

    def _address(self, text: str) -> int:
        """A line address for :m / :t: 0 is above the first line, . this line, $ the last, N line N (1-based)."""
        text = text.strip() or "."
        if text == ".":
            return self.cursor_location[0] + 1
        if text == "$":
            return self.document.line_count
        if text.isdigit():
            return min(int(text), self.document.line_count)
        raise ValueError(f"Invalid address: {text}")

    def line_command(self, top: int, bottom: int, name: str, arg: str) -> int | None:
        """:d :j :> :< :m :t :co :normal on rows top..bottom. :m and :t return the destination row
        (0-based, where the block now starts); the others None."""
        last = self.document.line_count - 1
        top, bottom = max(0, top), min(last, bottom)
        if name in ("d", "delete"):
            register = arg.strip()[:1]
            self._use_register = register if register and not register.isdigit() else ""
            self.move_cursor((top, 0))
            self._lines_operator("d", top, bottom)
        elif name in ("j", "join"):
            self._join_rows(top, max(bottom, top + 1), spaces=True)
        elif name[0] in "<>":
            self._shift(top, bottom, len(name), name[0] == ">")
        elif name in ("m", "move", "t", "co"):
            lines = [self.document.get_line(row) for row in range(self.document.line_count)]
            block = lines[top : bottom + 1]
            after = self._address(arg)
            if name in ("m", "move"):
                if top < after <= bottom:
                    raise ValueError("Cannot move a range of lines into itself")
                rest = lines[:top] + lines[bottom + 1 :]
                at = after if after <= top else after - len(block)
                new = rest[:at] + block + rest[at:]
            else:
                at = after
                new = lines[:at] + block + lines[at:]
            self.history.checkpoint()
            self.replace("\n".join(new), (0, 0), self.document.end, maintain_selection_offset=False)
            self.history.checkpoint()
            self.move_cursor((min(at + len(block) - 1, self.document.line_count - 1), 0))
            return at
        elif name in ("norm", "normal"):
            keys = [(char, char) for char in arg.lstrip()]
            shift = 0
            for row in range(top, bottom + 1):
                current = row + shift
                if current >= self.document.line_count:
                    break
                before = self.document.line_count
                if self.mode != "normal":
                    self.set_mode("normal")
                self.move_cursor((current, 0))
                self._replay(keys)
                if self.mode in _BAR_MODES:
                    self._feed("escape", None)
                self.pending = self.count = ""
                shift += self.document.line_count - before
        else:
            raise ValueError(f"Unknown command: {name}")
        return None

    def _format_rows(self, top: int, bottom: int) -> None:
        """gq / gw: re-wrap lines top..bottom at TEXT_WIDTH. Runs of lines with the same indent
        and comment marker form one paragraph; blank lines and code lines' structure are kept apart."""
        import textwrap

        lines = [self.document.get_line(row) for row in range(top, bottom + 1)]
        out: list[str] = []
        group: list[str] = []
        prefix = ""

        def flush() -> None:
            if group:
                words = " ".join(part[len(prefix) :].strip() for part in group)
                out.extend(textwrap.wrap(words, TEXT_WIDTH, initial_indent=prefix, subsequent_indent=prefix, break_long_words=False, break_on_hyphens=False) or [prefix.rstrip()])
                group.clear()

        for line in lines:
            if not line.strip():
                flush()
                out.append(line)
                continue
            marker = re.match(r"\s*(?:#+ ?)?", line).group()
            if group and marker != prefix:
                flush()
            prefix = marker
            group.append(line)
        flush()
        if out != lines:
            self.history.checkpoint()
            self.replace("\n".join(out), (top, 0), (bottom, len(lines[-1])), maintain_selection_offset=False)
            self.history.checkpoint()
        end = top + len(out) - 1
        text = self.document.get_line(end)
        self.move_cursor((end, len(text) - len(text.lstrip())))

    def _lines_operator(self, op: str, row: int, end_row: int) -> None:
        """d/c/y on whole lines row..end_row (dd, cc, yy and the linewise motions)."""
        last = self.document.line_count - 1
        self._yank_lines(row, end_row, yank=op == "y")
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

    def _store(self, text: str, linewise: bool | None, yank: bool = True) -> None:
        """A yank or delete fills the unnamed register and the named one chosen with \"x (\"X appends,
        \"_ discards). As in vim, a yank also fills \"0; a delete of lines (or across lines) shifts
        \"1..\"9 and fills \"1, a smaller one fills \"-. `linewise` None is a block (ctrl+v)."""
        name, self._use_register = self._use_register, ""
        if name == "_":
            return
        registers = VimTextArea.registers
        VimTextArea.register, VimTextArea.register_linewise = text, linewise
        if yank:
            registers["0"] = (text, linewise)
        elif linewise or "\n" in text:
            for slot in range(9, 1, -1):
                if str(slot - 1) in registers:
                    registers[str(slot)] = registers[str(slot - 1)]
            registers["1"] = (text, linewise)
        else:
            registers["-"] = (text, linewise)
        if name.isupper():
            old, _ = registers.get(name.lower(), ("", linewise))
            registers[name.lower()] = ((old + "\n" + text) if old and linewise else old + text, linewise)
        elif name:
            registers[name] = (text, linewise)
        if name in ("+", "*") and not yank:
            self._copy_register()

    def _fetch(self) -> tuple[str, bool | None]:
        """The register a put uses: the one chosen with \"x, else the unnamed one."""
        name, self._use_register = self._use_register, ""
        if name == '"':
            name = ""
        if name and (name not in ("+", "*") or name in VimTextArea.registers):
            return VimTextArea.registers.get(name.lower(), ("", False))
        return VimTextArea.register, VimTextArea.register_linewise

    def _chars_operator(self, op: str, start: Location, end: Location) -> None:
        """d/c/y on the characters from `start` up to, not including, `end`."""
        a, b = sorted([start, end])
        text = self.get_text_range(a, b)
        if text:
            self._store(text, False, yank=op == "y")
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

    def _yank_lines(self, first: int, last: int, yank: bool = True) -> None:
        text = self.get_text_range((first, 0), (last, len(self.document.get_line(last))))
        self._store(text, True, yank)

    def _paste(self, after: bool, n: int, cursor_after: bool = False) -> None:
        """p / P (gp / gP with `cursor_after`: the cursor ends just after the new text)."""
        text, linewise = self._fetch()
        if not text:
            return
        row, col = self.cursor_location
        line = self.document.get_line(row)
        if linewise is None:
            self._paste_block(text, row, min(len(line), col + 1) if after and line else col, n)
        elif linewise:
            block = "\n".join([text] * n)
            added = block.count("\n") + 1
            if after:
                self.insert("\n" + block, (row, len(line)), maintain_selection_offset=False)
                self.move_cursor((min(self.document.line_count - 1, row + 1 + added), 0) if cursor_after else (row + 1, 0))
            else:
                self.insert(block + "\n", (row, 0), maintain_selection_offset=False)
                self.move_cursor((row + added, 0) if cursor_after else (row, 0))
        else:
            at = (row, min(len(line), col + 1)) if after and line else (row, col)
            result = self.insert(text * n, at, maintain_selection_offset=False)
            if cursor_after:
                self.move_cursor(result.end_location)
                return
            self.move_cursor(self._before(result.end_location))
            self._clamp()

    def _paste_block(self, text: str, row: int, col: int, n: int) -> None:
        """Put a block (yanked with ctrl+v): its lines go into this and the following lines at
        column `col`; short lines are padded with spaces, missing lines are added."""
        pieces = text.split("\n")
        width = max(len(piece) for piece in pieces)
        last = self.document.line_count - 1
        end_row = min(last, row + len(pieces) - 1)
        old = [self.document.get_line(r) for r in range(row, end_row + 1)]
        new = []
        for index, piece in enumerate(pieces):
            padded = (old[index] if index < len(old) else "").ljust(col)
            rest = padded[col:]
            new.append(padded[:col] + (piece.ljust(width) * n if rest else (piece.ljust(width) * (n - 1)) + piece) + rest)
        self.history.checkpoint()
        self.replace("\n".join(new), (row, 0), (end_row, len(old[-1])), maintain_selection_offset=False)
        self.history.checkpoint()
        self.move_cursor((row, col))

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
        """Start (or switch to) charwise "v", linewise "V" or blockwise "b" (ctrl+v) visual mode, anchored at `anchor`."""
        self.pending = self.count = ""
        if not self.visual:
            self.highlight_cursor_line = False
        self.visual = kind
        self._anchor = anchor
        self._line_cache.clear()
        if head is not None:
            self.move_cursor(head)
        self._show_visual()
        self.refresh()
        self._apply_mode()
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
        self._apply_mode()
        if post:
            self.post_message(self.ModeChanged(self, self.mode))

    def _show_visual(self) -> None:
        """Turn the anchor and the cursor into the TextArea selection that draws like vim's.

        The selection ends at the cursor, which is drawn as a block. Charwise, the anchor's
        own character is included; linewise and blockwise, get_line paints the area and the
        TextArea selection is only the cursor.
        """
        anchor, head = self._anchor, self.cursor_location
        if self.visual == "b":
            self.selection = Selection.cursor(head)
            self._line_cache.clear()
            self.refresh()
            return
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
        if self.visual == "b":
            top, bottom, left, right = self._block_bounds()
            return (top, left), (bottom, min(right + 1, len(self.document.get_line(bottom)))), top, bottom
        return first, self._after(last), first[0], last[0]

    def _block_bounds(self) -> tuple[int, int, int, int]:
        """(top row, bottom row, left column, right column inclusive) of a ctrl+v block; after $ the right edge is every line's end."""
        (row_a, col_a), (row_b, col_b) = self._anchor, self.cursor_location
        top, bottom = sorted((row_a, row_b))
        left, right = sorted((col_a, col_b))
        return top, bottom, left, 10**9 if self._block_eol else right

    def _block_rows(self) -> list[tuple[int, str, int, int]]:
        """(row, line, start, end) of each line of the block; start..end is the part inside it."""
        top, bottom, left, right = self._block_bounds()
        rows = []
        for row in range(top, bottom + 1):
            line = self.document.get_line(row)
            rows.append((row, line, min(left, len(line)), min(right + 1, len(line))))
        return rows

    def _rewrite_rows(self, top: int, new_lines: list[str]) -> None:
        """Replace lines top.. with `new_lines` (same count) as one undo step."""
        bottom = top + len(new_lines) - 1
        old = [self.document.get_line(row) for row in range(top, bottom + 1)]
        if old != new_lines:
            self.history.checkpoint()
            self.replace("\n".join(new_lines), (top, 0), (bottom, len(old[-1])), maintain_selection_offset=False)
            self.history.checkpoint()

    def _block_key(self, key: str, n: int) -> bool | None:
        """Keys that act differently on a ctrl+v block; None for the ones visual mode handles the same way."""
        top, bottom, left, right = self._block_bounds()
        match key:
            case "$":
                self._block_eol = True
                self._move("$", 1)
                self._show_visual()
            case "O":
                (row_a, col_a), (row_b, col_b) = self._anchor, self.cursor_location
                self._anchor = (row_a, col_b)
                self.move_cursor((row_b, col_a))
                self._show_visual()
            case "y" | "Y":
                text = "\n".join(line[start:end] for _, line, start, end in self._block_rows())
                self._store(text, None)
                self._copy_register()
                self._exit_visual((top, left))
            case "d" | "x" | "D" | "X" | "c" | "s" | "C" | "S":
                if key in ("D", "X", "C", "S"):
                    self._block_eol = True
                rows = self._block_rows()
                self._store("\n".join(line[start:end] for _, line, start, end in rows), None, yank=False)
                self._exit_visual((top, left))
                self._rewrite_rows(top, [line[:start] + line[end:] for _, line, start, end in rows])
                self.move_cursor((top, min(left, len(self.document.get_line(top)))))
                if key in ("c", "s", "C", "S"):
                    self._start_block_insert(top, bottom, left, False)
                else:
                    self._clamp()
            case "I" | "A":
                eol = self._block_eol and key == "A"
                column = left if key == "I" else right + 1
                self._exit_visual((top, left))
                line = self.document.get_line(top)
                if key == "A" and not eol and len(line) < column:
                    self._rewrite_rows(top, [line.ljust(column)])
                self.move_cursor((top, len(self.document.get_line(top)) if eol else min(column, len(self.document.get_line(top)))))
                self._start_block_insert(top, bottom, column, eol)
            case "~" | "u" | "U":
                self._block_edit({"~": str.swapcase, "u": str.lower, "U": str.upper}[key])
            case "p" | "P":
                text, linewise = self._fetch()
                rows = self._block_rows()
                self._exit_visual((top, left))
                self._rewrite_rows(top, [line[:start] + line[end:] for _, line, start, end in rows])
                if text:
                    if linewise is None:
                        self._paste_block(text, top, left, n)
                    else:
                        self.insert(text * n, (top, min(left, len(self.document.get_line(top)))), maintain_selection_offset=False)
                        self.move_cursor((top, left))
                        self._clamp()
            case _:
                return None
        return True

    def _block_edit(self, change) -> None:
        """~ u U r<char> on a block: change the part of each line inside it."""
        top, _, left, _ = self._block_bounds()
        rows = self._block_rows()
        self._exit_visual((top, left))
        self._rewrite_rows(top, [line[:start] + change(line[start:end]) + line[end:] for _, line, start, end in rows])
        self.move_cursor((top, left))
        self._clamp()

    def _start_block_insert(self, top: int, bottom: int, column: int, eol: bool) -> None:
        """I A c on a block: type on the first line; escape copies the text to the other lines."""
        self._block_insert = (top, bottom, column, eol, len(self.document.get_line(top)))
        self.set_mode("insert")

    def _finish_block_insert(self) -> None:
        top, bottom, column, eol, length_before = self._block_insert
        self._block_insert = None
        if self.document.line_count <= bottom or bottom == top:
            return
        line = self.document.get_line(top)
        added = len(line) - length_before
        start = len(line) - added if eol else column
        typed = line[start : start + added] if added > 0 else ""
        if not typed:
            return
        new_lines = []
        for row in range(top + 1, bottom + 1):
            text = self.document.get_line(row)
            if eol:
                new_lines.append(text + typed)
            elif len(text) >= column:
                new_lines.append(text[:column] + typed + text[column:])
            else:
                new_lines.append(text)
        cursor = self.cursor_location
        self._rewrite_rows(top + 1, new_lines)
        self.move_cursor(cursor)

    def _visual(self, key: str) -> bool:
        """Handle one visual-mode key. Returns False if the key means nothing here."""
        if key.isdigit() and (key != "0" or self.count) and not self._wants_char():
            self.count += key
            return True
        if not self._wants_char():
            key = _NORMAL_ALIASES.get(key, key)
        n = int(self.count or 1)
        pending, self.pending, self.count = self.pending, "", ""
        if pending == "g":
            if key == "g":
                self._visual_motion("gg", n)
            elif key == "J":
                self._visual_join(spaces=False)
            elif key == "v":
                self._reselect()
            elif key == "?":
                if self.visual == "b":
                    self._block_edit(rot13)
                else:
                    self._visual_edit(rot13)
            elif key in ("q", "w"):
                _, _, top, bottom = self._visual_range()
                self._exit_visual()
                self._apply_operator("g" + key, (top, 0), (bottom, 0), True)
            elif key in ("ctrl+a", "ctrl+x"):
                self._visual_add(n if key == "ctrl+a" else -n, progressive=True)
            elif key in ("e", "E", "_", "0", "$", "m"):
                self._visual_motion("g" + key, n)
            return True
        if pending in ("i", "a"):
            self._visual_object(pending + key)
            return True
        if pending in _FIND_KEYS:
            if len(key) == 1:
                self._visual_motion((pending, key), n)
            return True
        if pending in ("[", "]"):
            self._visual_motion(pending + key, n)
            return True
        if pending == "r":
            if len(key) == 1:
                if self.visual == "b":
                    self._block_edit(lambda text: key * len(text))
                else:
                    self._visual_edit(lambda text: "".join(char if char == "\n" else key for char in text))
            return True
        if pending == '"':
            if len(key) == 1:
                self._use_register = key
            return True
        if self.visual == "b":
            handled = self._block_key(key, n)
            if handled is not None:
                return handled
            if key in ("h", "l", "left", "right", "0", "^", "|", "w", "b", "e", "W", "B", "E"):
                self._block_eol = False
        match key:
            case "v" | "V" | "ctrl+v":
                kind = "b" if key == "ctrl+v" else key
                if kind == self.visual:
                    self._exit_visual()
                else:
                    self._block_eol = False
                    self._enter_visual(kind, self._anchor)
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
            case "ctrl+a" | "ctrl+x":
                self._visual_add(n if key == "ctrl+a" else -n, progressive=False)
            case "g" | "i" | "a" | "r" | "f" | "F" | "t" | "T" | '"' | "[" | "]":
                self.pending, self.count = key, str(n) if n > 1 else ""
            case ":":
                self._exit_visual()
                self.post_message(self.ExCommand(self, "'<,'>"))
            case "/" | "K":
                pass
            case _:
                return self._visual_motion(key, n)
        return True

    def _visual_add(self, delta: int, progressive: bool) -> None:
        """ctrl+a / ctrl+x on a selection: change the first number on each selected line
        (g ctrl+a: the first by delta, the second by 2 * delta, ...)."""
        start, _, top, bottom = self._visual_range()
        block_left = self._block_bounds()[2] if self.visual == "b" else None
        visual = self.visual
        self._exit_visual(start)
        new_lines = []
        step = 0
        for row in range(top, bottom + 1):
            line = self.document.get_line(row)
            begin = block_left if block_left is not None else start[1] if (visual == "v" and row == top) else 0
            match = next((m for m in _NUMBER.finditer(line) if m.end() > begin), None)
            if match is None:
                new_lines.append(line)
                continue
            step += 1
            change = delta * (step if progressive else 1)
            new_lines.append(line[: match.start()] + str(int(match.group()) + change) + line[match.end() :])
        self._rewrite_rows(top, new_lines)
        self.move_cursor(start)
        self._clamp()

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
