import asyncio

from textual.app import App

from gpython.vim import VimTextArea


class EditorApp(App):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text

    def compose(self):
        yield VimTextArea(self.text)


def run(text: str, *keys: str) -> tuple[str, str, tuple[int, int]]:
    """Press keys in a fresh editor; return (text, mode, cursor)."""

    async def main():
        app = EditorApp(text)
        async with app.run_test() as pilot:
            editor = app.query_one(VimTextArea)
            editor.focus()
            await pilot.press(*keys)
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


def test_delete_and_change():
    assert run("foo bar", "d", "w")[0] == "bar"
    assert run("foo bar", "x")[0] == "oo bar"
    assert run("one\ntwo\nthree", "j", "d", "d")[0] == "one\nthree"
    assert run("one\ntwo\nthree", "2", "d", "d")[0] == "three"
    assert run("foo bar", "D")[0] == ""
    text, mode, _ = run("foo bar", "c", "w", "x", "escape")
    assert (text, mode) == ("x bar", "normal")


def test_open_line_and_append():
    assert run("    x = 1", "o", "y", "escape")[0] == "    x = 1\n    y"
    assert run("ab", "A", "c", "escape")[0] == "abc"


def test_yank_paste_undo():
    assert run("one\ntwo", "y", "y", "p")[0] == "one\none\ntwo"
    assert run("one\ntwo", "d", "d", "p")[0] == "two\none"
    assert run("abc", "x", "u")[0] == "abc"


def test_join():
    assert run("a\n    b", "J")[0] == "a b"


def test_disabled_vim_is_plain_insert():
    VimTextArea.enabled = False
    try:
        assert run("", "a", "b", "escape", "c") == ("abc", "insert", (0, 3))
    finally:
        VimTextArea.enabled = True
