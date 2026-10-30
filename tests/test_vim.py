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

    def on_vim_text_area_delete_cell(self, event: VimTextArea.DeleteCell) -> None:
        self.messages.append(("delete", event.count))

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
    # dd deletes the cell (the app handles DeleteCell); the text stays as it is.
    messages: list = []
    assert run("one\ntwo\nthree", "j", "d", "d", messages=messages)[0] == "one\ntwo\nthree"
    assert messages == [("delete", 1)]
    messages.clear()
    assert run("one\ntwo\nthree", "2", "d", "d", messages=messages)[0] == "one\ntwo\nthree"
    assert messages == [("delete", 2)]
    assert run("one\ntwo\nthree", "d", "j")[0] == "three"  # linewise motions still delete lines
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
    # escape in normal mode: first clears a pending operator, then leaves the editor.
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
    # Backspace in leading whitespace removes a whole indent level.
    assert run("        x", "I", "backspace")[0] == "    x"
