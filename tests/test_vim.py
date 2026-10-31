import asyncio

from textual.app import App

from gpython.vim import VimTextArea


class EditorApp(App):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text
        self.messages: list = []

    def compose(self):
        yield VimTextArea(self.text)

    def on_vim_text_area_edge_reached(self, event: VimTextArea.EdgeReached) -> None:
        self.messages.append(("edge", event.step))

    def on_vim_text_area_search_requested(self, event: VimTextArea.SearchRequested) -> None:
        self.messages.append(("search",))

    def on_vim_text_area_leave_editor(self, event: VimTextArea.LeaveEditor) -> None:
        self.messages.append(("leave",))


def run(text: str, *keys: str, messages: list | None = None) -> tuple[str, str, tuple[int, int]]:
    """Press keys in a fresh editor; return (text, mode, cursor). Posted messages go to `messages`."""

    async def main():
        app = EditorApp(text)
        VimTextArea.mode = "normal"
        async with app.run_test() as pilot:
            editor = app.query_one(VimTextArea)
            editor.focus()
            await pilot.press(*keys)
            await pilot.pause()
            if messages is not None:
                messages.extend(app.messages)
            return editor.text, editor.mode, editor.cursor_location

    return asyncio.run(main())


def test_starts_in_normal_mode_and_ignores_typing():
    assert run("abc", "z", "q") == ("abc", "normal", (0, 0))


def test_insert_and_escape():
    text, mode, cursor = run("", "i", "h", "i", "escape")
    assert (text, mode, cursor) == ("hi", "normal", (0, 1))


def test_motions():
    assert run("foo bar baz", "w", "w")[2] == (0, 8)
    assert run("foo bar baz", "$")[2] == (0, 10)
    assert run("foo bar baz", "e")[2] == (0, 2)
    assert run("a\nb\nc", "G")[2] == (2, 0)
    assert run("a\nb\nc", "G", "g", "g")[2] == (0, 0)
    assert run("a\nb\nc", "2", "j")[2] == (2, 0)
    assert run("a\nb\nc", "2", "G")[2] == (1, 0)


def test_delete_and_change():
    assert run("foo bar", "d", "w")[0] == "bar"
    assert run("foo bar", "x")[0] == "oo bar"
    assert run("one\ntwo\nthree", "j", "d", "d")[0] == "one\nthree"
    assert run("one\ntwo\nthree", "2", "d", "d")[0] == "three"
    assert run("one\ntwo\nthree", "G", "d", "d")[0] == "one\ntwo"
    assert run("one\ntwo\nthree", "j", "d", "d", "u")[0] == "one\ntwo\nthree"
    assert run("one\ntwo\nthree", "d", "j")[0] == "three"
    assert run("one\ntwo", "0", "D")[0] == "\ntwo"
    assert run("foo bar", "D")[0] == ""
    text, mode, _ = run("foo bar", "c", "w", "x", "escape")
    assert (text, mode) == ("x bar", "normal")


def test_open_line_and_append():
    assert run("    x = 1", "o", "y", "escape")[0] == "    x = 1\n    y"
    assert run("ab", "A", "c", "escape")[0] == "abc"


def test_yank_paste_undo():
    assert run("one\ntwo", "y", "y", "p")[0] == "one\none\ntwo"
    assert run("one\ntwo\nthree", "d", "j", "p")[0] == "three\none\ntwo"
    assert run("abc", "x", "u")[0] == "abc"


def test_join():
    assert run("a\n    b", "g", "J")[0] == "a b"


def test_cell_level_keys_post_messages():
    messages: list = []
    assert run("a\nb", "J", messages=messages)[0] == "a\nb"
    assert run("a\nb", "K", messages=messages)[0] == "a\nb"
    run("a\nb", "3", "J", messages=messages)
    assert messages == [("edge", 1), ("edge", -1), ("edge", 3)]
    messages.clear()
    run("a", "/", messages=messages)
    assert messages == [("search",)]
    messages.clear()
    run("a", "d", "escape", messages=messages)
    assert messages == []
    run("a", "escape", messages=messages)
    assert messages == [("leave",)]


def test_disabled_vim_is_plain_insert():
    VimTextArea.enabled = False
    try:
        assert run("", "a", "b", "escape", "c") == ("abc", "insert", (0, 3))
    finally:
        VimTextArea.enabled = True


def test_ctrl_bracket_leaves_insert_mode():
    assert run("", "i", "x", "ctrl+left_square_bracket", "y")[0:2] == ("x", "normal")


def test_auto_indent():
    assert run("def f():", "A", "enter", "x")[0] == "def f():\n    x"
    assert run("    return 1", "A", "enter", "x")[0] == "    return 1\nx"
    assert run("    y = 2", "A", "enter", "x")[0] == "    y = 2\n    x"
    assert run("xs = [", "A", "]", "left", "enter")[0] == "xs = [\n    \n]"
    assert run("def f():", "o", "x")[0] == "def f():\n    x"
    assert run("        x", "I", "backspace")[0] == "    x"


def test_dot_repeats_the_last_change():
    assert run("foo bar", "i", "X", "escape", ".")[0] == "XXfoo bar"
    assert run("foo bar baz", "d", "w", ".")[0] == "baz"
    assert run("foo bar baz", "c", "w", "X", "escape", "w", ".")[0] == "X X baz"
    assert run("foo\nbar", "A", "X", "escape", "j", ".")[0] == "fooX\nbarX"
    assert run("a b c d e", "d", "w", "2", ".")[0] == "d e"
    assert run("abc", "x", "u", ".")[0] == "bc"
    assert run("one two", "w", "i", "X", "escape", "0", ".")[0] == "Xone Xtwo"


def test_counted_inserts_repeat_the_text():
    assert run("abc", "3", "i", "X", "escape")[0] == "XXXabc"
    assert run("abc", "3", "a", "X", "escape")[0] == "aXXXbc"
    assert run("abc", "2", "A", "X", "escape")[0] == "abcXX"
    assert run("abc", "3", "o", "X", "escape")[0] == "abc\nX\nX\nX"
    assert run("abc", "2", "O", "X", "escape")[0] == "X\nX\nabc"


def test_case_operators():
    assert run("foo bar", "~") == ("Foo bar", "normal", (0, 1))
    assert run("foo bar", "3", "~")[0] == "FOO bar"
    assert run("foo bar", "g", "U", "w")[0] == "FOO bar"
    assert run("FOO bar", "g", "u", "w")[0] == "foo bar"
    assert run("foo bar", "g", "U", "U")[0] == "FOO BAR"
    assert run("foo bar", "g", "U", "g", "U")[0] == "FOO BAR"
    assert run("foo bar", "g", "~", "w")[0] == "FOO bar"
    assert run("foo bar", "w", "g", "U", "i", "w")[0] == "foo BAR"
    assert run("foo\nbar", "g", "U", "j")[0] == "FOO\nBAR"


def test_replace_mode():
    assert run("foo bar", "R", "X", "Y", "escape") == ("XYo bar", "normal", (0, 1))
    assert run("foo", "R", "X", "Y", "Z", "W", "escape")[0] == "XYZW"
    assert run("foo bar", "R", "X", "Y", "backspace", "escape")[0] == "Xoo bar"
    assert run("foo", "l", "R", "X", "Y", "Z", "backspace", "backspace", "escape")[0] == "fXo"
    assert run("foo bar", "R", "X", "escape", "w", ".")[0] == "Xoo Xar"


def test_shift_with_motions_and_objects():
    assert run("a\nb\nc\nd", ">", "j")[0] == "    a\n    b\nc\nd"
    assert run("a\nb\nc\nd", ">", "G")[0] == "    a\n    b\n    c\n    d"
    assert run("a\nb\n\nc", ">", "i", "p")[0] == "    a\n    b\n\nc"
    assert run("    a\n    b", "<", "j")[0] == "a\nb"
    assert run("a\nb\nc", "2", ">", ">")[0] == "    a\n    b\nc"
    assert run("a\nb\nc", ">", ">", "j", ".")[0] == "    a\n    b\nc"


def test_marks():
    assert run("foo bar", "m", "a", "$", "`", "a", "x")[0] == "oo bar"
    assert run("foo\nbar\nbaz", "m", "a", "G", "'", "a", "d", "d")[0] == "bar\nbaz"
    assert run("foo bar baz", "w", "m", "x", "$", "d", "`", "x")[0] == "foo z"
    assert run("a\nb\nc", "m", "q", "j", "j", "d", "'", "q")[0] == ""
    assert run("foo bar", "`", "z")[2] == (0, 0)


def test_named_registers():
    assert run("foo bar", '"', "a", "y", "w", "$", '"', "a", "p")[0] == "foo barfoo "
    assert run("foo bar", '"', "a", "y", "w", "w", '"', "b", "y", "w", "$", '"', "a", "p", '"', "b", "p")[0] == "foo barfoo bar"
    assert run("a\nb\nc", '"', "a", "y", "y", "j", '"', "A", "y", "y", "G", '"', "a", "p")[0] == "a\nb\nc\na\nb"
    assert run("foo bar", "y", "w", '"', "_", "d", "w", "p")[0] == "bfoo ar"
    assert run("foo bar", "v", "e", '"', "a", "y", "$", '"', "a", "p")[0] == "foo barfoo"


def test_macros():
    assert run("foo bar", "q", "a", "x", "q", "@", "a")[0] == "o bar"
    assert run("foo bar", "q", "a", "x", "q", "@", "a", "@", "@")[0] == " bar"
    assert run("foo bar", "q", "a", "x", "q", "3", "@", "a")[0] == "bar"
    assert run("a\nb\nc", "q", "w", "A", "!", "escape", "j", "q", "2", "@", "w")[0] == "a!\nb!\nc!"
    assert run("a b", "q", "a", "x", "q", "u", "@", "a")[0] == " b"


def test_increment_and_decrement():
    assert run("x = 1", "ctrl+a")[0] == "x = 2"
    assert run("x = 1", "5", "ctrl+a")[0] == "x = 6"
    assert run("x = 1", "ctrl+x")[0] == "x = 0"
    assert run("x = 1", "2", "ctrl+x")[0] == "x = -1"
    assert run("x = -1", "ctrl+a")[0] == "x = 0"
    assert run("a1 b22", "w", "ctrl+a")[0] == "a1 b23"
    assert run("x = 9", "ctrl+a", ".")[0] == "x = 11"
    assert run("none", "ctrl+a")[0] == "none"


def test_more_motions():
    assert run("a\nb\nc\nd\ne", "L", "d", "d")[0] == "a\nb\nc\nd"
    assert run("a\nb\nc\nd\ne", "G", "H", "d", "d")[0] == "b\nc\nd\ne"
    assert run("a\nb\nc\nd\ne", "M")[2] == (2, 0)
    assert run("foo bar baz", "$", "d", "g", "e")[0] == "foo baz"
    assert run("foo bar baz", "$", "g", "e")[2] == (0, 6)
    assert run("foo bar", "5", "|", "D")[0] == "foo "
    assert run("foo bar", "$", "d", "_")[0] == ""
    assert run("a\nb\nc", "d", "+")[0] == "c"
    assert run("a\nb\nc", "j", "d", "-")[0] == "c"
    assert run("a\nb\nc", "j", "d", "enter")[0] == "a"
    assert run("  a\n  b", "+")[2] == (1, 2)


def test_paragraph_motions_follow_vim():
    assert run("a\n\nb\nc\n\nd", "}")[2] == (1, 0)
    assert run("a\n\nb\nc\n\nd", "}", "}")[2] == (4, 0)
    assert run("a\n\nb\nc\n\nd", "}", "d", "}")[0] == "a\n\nd"
    assert run("a\n\nb\nc\n\nd", "}", "j", "d", "a", "p")[0] == "a\n\nd"
    assert run("a\n\nb\nc\n\nd", "}", "j", "d", "i", "p")[0] == "a\n\n\nd"
    assert run("a\n\nb\nc\n\nd", "G", "{", "{")[2] == (1, 0)


def test_counted_bracket_objects():
    assert run("foo(bar(baz)) q", "f", "z", "d", "2", "i", "(")[0] == "foo() q"
    assert run("foo(bar(baz)) q", "f", "z", "2", "d", "i", "(")[0] == "foo() q"
    assert run("foo(bar(baz)) q", "f", "z", "d", "2", "a", "(")[0] == "foo q"


def test_ctrl_o_runs_one_normal_command_in_insert_mode():
    assert run("foo", "a", "ctrl+o", "0", "X", "escape")[0] == "Xfoo"
    assert run("foo bar", "A", "ctrl+o", "d", "b", "X", "escape")[0] == "foo X"
    assert run("foo", "i", "ctrl+o", "$", "X", "escape")[0] == "fooX"


def test_replace_mode_is_its_own_mode():
    assert run("foo", "R")[1] == "replace"


def test_reindent_normalises_python_levels():
    from gpython.vim import reindent

    assert reindent(["def f(x):", "      if x:", "            y = 1", "      return y"]) == ["def f(x):", "    if x:", "        y = 1", "    return y"]
    assert reindent(["def f(x):", "if x:", "return 1", "return 2"]) == ["def f(x):", "    if x:", "        return 1", "    return 2"]
    assert reindent(["if a:", "    b", "    else:", "    c"]) == ["if a:", "    b", "else:", "    c"]
    assert reindent(["try:", "  x()", "except E:", "  pass", "y()"]) == ["try:", "    x()", "except E:", "    pass", "y()"]
    assert reindent(["x = f(", "a,", "b,", ")", "y"]) == ["x = f(", "    a,", "    b,", ")", "y"]
    assert reindent(["def g(", "  a,", "):", "return a"]) == ["def g(", "    a,", "):", "    return a"]
    assert reindent(["for i in x:", "\tif i:", "\t\tprint(i)", "", "\tprint(':')"]) == ["for i in x:", "    if i:", "        print(i)", "", "    print(':')"]
    assert reindent(["a = 1", "b = 2"]) == ["a = 1", "b = 2"]
    assert reindent(["if a:  # note:", "x"]) == ["if a:  # note:", "    x"]


def test_equals_operator_reindents():
    messy = "def f(x):\n      if x:\n            y = 1\n      return y"
    clean = "def f(x):\n    if x:\n        y = 1\n    return y"
    assert run(messy, "V", "G", "=")[0] == clean
    assert run(messy, "g", "g", "=", "G")[0] == clean
    assert run(messy, "=", "i", "p")[0] == clean
    assert run(messy, "j", "=", "=")[0] == "def f(x):\n    if x:\n            y = 1\n      return y"
    assert run(messy, "j", "=", "=", "j", ".")[0] == "def f(x):\n    if x:\n        y = 1\n      return y"
    assert run(messy, "G", "=", "=")[2] == (3, 4)
