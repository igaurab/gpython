"""The vim features added on top of the basics in test_vim.py: replace, joins, the jump and
change lists, scrolling, sentence and bracket motions, registers, insert-mode ctrl keys,
blockwise visual mode, :s and the editor messages for ex commands."""

import asyncio

import pytest
from textual.app import App
from textual.containers import VerticalScroll

from gpython.vim import VimTextArea, parse_substitute, rot13, vim_regex


class EditorApp(App):
    def __init__(self, text: str, tall: bool = False) -> None:
        super().__init__()
        self.text = text
        self.tall = tall
        self.messages: list = []

    def compose(self):
        if self.tall:
            with VerticalScroll():
                yield VimTextArea(self.text)
        else:
            yield VimTextArea(self.text)

    def on_vim_text_area_ex_run(self, event: VimTextArea.ExRun) -> None:
        self.messages.append(("ex", event.command))

    def on_vim_text_area_ex_command(self, event: VimTextArea.ExCommand) -> None:
        self.messages.append((":", event.initial))

    def on_vim_text_area_search_requested(self, event: VimTextArea.SearchRequested) -> None:
        self.messages.append(("search", event.backward))


@pytest.fixture(autouse=True)
def _clean_registers():
    VimTextArea.registers = {}
    VimTextArea.register, VimTextArea.register_linewise = "", False
    VimTextArea.last_substitute = None
    VimTextArea.mode = "normal"
    yield
    VimTextArea.registers = {}
    VimTextArea.mode = "normal"


def run(text: str, *keys: str, after=None, tall: bool = False, messages: list | None = None):
    """Press keys in a fresh editor; return (text, mode, cursor), or after(editor) if given."""

    async def main():
        app = EditorApp(text, tall)
        async with app.run_test(size=(80, 20)) as pilot:
            editor = app.query_one(VimTextArea)
            editor.focus()
            await pilot.press(*keys)
            await pilot.pause()
            if messages is not None:
                messages.extend(app.messages)
            if after is not None:
                return after(editor)
            return editor.text, editor.mode, editor.cursor_location

    return asyncio.run(main())


def test_r_replaces_characters_and_repeats_with_dot():
    assert run("abcd", "r", "x")[0:3:2] == ("xbcd", (0, 0))
    assert run("abcd", "3", "r", "x")[0:3:2] == ("xxxd", (0, 2))
    assert run("abcd", "5", "r", "x")[0] == "abcd"
    assert run("ab cd", "l", "l", "r", "enter")[0] == "ab\ncd"
    assert run("abcd", "r", "x", "l", ".")[0] == "xxcd"
    assert run("a1", "r", "1")[0] == "11"


def test_J_joins_lines_in_normal_mode():
    assert run("a\n  b\nc", "J")[0] == "a b\nc"
    assert run("a\nb\nc", "u", "J", "u")[0] == "a\nb\nc"


def test_U_restores_the_line():
    assert run("abc\nx", "x", "x", "U")[0] == "abc\nx"
    assert run("abc\nx", "x", "U", "U")[0] == "bc\nx"


def test_gi_gI_gp():
    assert run("abc", "A", "x", "escape", "0", "g", "i", "y", "escape")[0] == "abcxy"
    assert run("  abc", "g", "I", "x", "escape")[0] == "x  abc"
    assert run("ab", "y", "l", "g", "p")[2] == (0, 2)
    assert run("a\nb", "y", "y", "g", "p")[0:3:2] == ("a\na\nb", (2, 0))


def test_new_motions():
    assert run("ab  ", "g", "_")[2] == (0, 1)
    assert run("One. Two. Three.", ")")[2] == (0, 5)
    assert run("One. Two. Three.", "$", "(")[2] == (0, 10)
    assert run("f(a, (b))", "$", "h", "[", "(")[2] == (0, 5)
    assert run("f(a, (b))", "$", "[", "(")[2] == (0, 1)
    assert run("f(a, (b))", "l", "l", "]", ")")[2] == (0, 8)
    assert run("x = 1\ndef f():\n    pass\nclass C:\n    pass", "]", "]")[2] == (1, 0)
    assert run("x = 1\ndef f():\n    pass\nclass C:\n    pass", "2", "]", "]")[2] == (3, 0)
    assert run("x = 1\ndef f():\n    pass\nclass C:\n    pass", "G", "[", "[")[2] == (3, 0)
    assert run("total = 1\nprint(total)", "j", "w", "w", "g", "d")[2] == (0, 0)
    assert run("abc def", "d", "g", "_")[0] == ""
    assert run("f(a, b)", "l", "l", "d", "]", ")")[0] == "f("


def test_jump_list_and_previous_context_mark():
    text = "a\nb\nc\nd"
    assert run(text, "G", "ctrl+o")[2] == (0, 0)
    assert run(text, "G", "ctrl+o", "tab")[2] == (3, 0)
    assert run(text, "G", "g", "g", "ctrl+o", "ctrl+o")[2] == (0, 0)
    assert run(text, "j", "G", "'", "'")[2] == (1, 0)
    assert run(text, "j", "G", "'", "'", "'", "'")[2] == (3, 0)
    assert run(text, "j", "l", "G", "`", "`")[2] == (1, 0)


def test_change_list():
    assert run("a\nb\nc", "x", "G", "g", ";")[2] == (0, 0)


def test_scrolling_moves_the_cursor_within_the_cell():
    text = "\n".join(f"line {i}" for i in range(60))
    _, _, cursor = run(text, "ctrl+d", tall=True)
    assert cursor[0] > 0
    _, _, after_up = run(text, "ctrl+d", "ctrl+u", tall=True)
    assert after_up[0] == 0
    assert run(text, "ctrl+f", tall=True)[2][0] > cursor[0]
    assert run(text, "5", "0", "G", "z", "z", tall=True)[2] == (49, 0)
    assert run(text, "1", "0", "z", "t", tall=True)[2] == (9, 0)
    assert run(text, "ctrl+e", "ctrl+y", "z", "b", tall=True)[2] == (0, 0)


def test_gq_g_question_and_operators():
    long = "# " + " ".join(["word"] * 30)
    lines = run(long, "g", "q", "q")[0].split("\n")
    assert len(lines) > 1 and all(line.startswith("# ") and len(line) <= 79 for line in lines)
    assert run("Hello", "g", "?", "?")[0] == "Uryyb"
    assert run("Hello there", "g", "?", "w")[0] == "Uryyb there"
    assert rot13(rot13("Abc xyz")) == "Abc xyz"


def test_numbered_and_small_delete_registers():
    assert run("one\ntwo\nthree", "d", "d", "d", "d", '"', "2", "p")[0] == "three\none"
    assert run("one two", "d", "w", "y", "w", '"', "-", "P")[0] == "one two"
    assert run("one two", "y", "w", "w", "d", "w", "0", '"', "0", "P")[0] == "one one "


def test_dot_register_and_insert_ctrl_a():
    assert run("", "i", "h", "i", "escape", "o", "ctrl+a", "escape")[0] == "hi\nhi"
    assert run("", "i", "h", "i", "escape", '"', ".", "p")[0] == "hihi"


def test_insert_mode_ctrl_keys():
    assert run("foo bar", "A", "ctrl+w", "escape")[0] == "foo "
    assert run("    foo bar", "A", "ctrl+u", "escape")[0] == "    "
    assert run("foo", "A", "ctrl+t", "escape")[0] == "    foo"
    assert run("        foo", "A", "ctrl+d", "escape")[0] == "    foo"
    assert run("abc", "y", "w", "o", "ctrl+r", '"', "escape")[0] == "abc\nabc"
    assert run("abc\n", "j", "i", "ctrl+y", "ctrl+y", "escape")[0] == "abc\nab"
    assert run("\nxyz", "i", "ctrl+e", "escape")[0] == "x\nxyz"
    assert run("", "i", "ctrl+v", "tab", "escape")[0] == "\t"
    assert run("ab", "A", "ctrl+j", "c", "escape")[0] == "ab\nc"


def test_visual_block_delete_yank_put_and_change_case():
    text = "abcd\nefgh\nijkl"
    assert run(text, "l", "ctrl+v", "j", "j", "l", "d")[0] == "ad\neh\nil"
    assert run(text, "l", "ctrl+v", "j", "l", "y", "G", "$", "p")[0] == "abcd\nefgh\nijklbc\n    fg"
    assert run(text, "ctrl+v", "j", "~")[0] == "Abcd\nEfgh\nijkl"
    assert run(text, "ctrl+v", "j", "l", "r", "x")[0] == "xxcd\nxxgh\nijkl"
    assert run(text, "l", "ctrl+v", "j", "O", "o", "d")[0] == "acd\negh\nijkl"
    assert run(text, "ctrl+v", "j", "escape")[2] == (1, 0)


def test_visual_block_insert_append_and_change():
    text = "abc\ndef\nghi"
    assert run(text, "l", "ctrl+v", "j", "j", "I", "-", "-", "escape")[0] == "a--bc\nd--ef\ng--hi"
    assert run(text, "ctrl+v", "j", "A", "+", "escape")[0] == "a+bc\nd+ef\nghi"
    assert run("ab\nabcd\nabc", "ctrl+v", "j", "j", "$", "A", ";", "escape")[0] == "ab;\nabcd;\nabc;"
    assert run(text, "l", "ctrl+v", "j", "c", "X", "escape")[0] == "aXc\ndXf\nghi"
    assert run("a\nabc", "ctrl+v", "j", "l", "A", "!", "escape")[0] == "a !\nab!c"


def test_visual_ctrl_a_and_progressive():
    assert run("x0\nx0\nx0", "V", "G", "ctrl+a")[0] == "x1\nx1\nx1"
    assert run("x0\nx0\nx0", "V", "G", "g", "ctrl+a")[0] == "x1\nx2\nx3"


def test_visual_colon_offers_the_range_and_gq():
    messages: list = []
    run("a\nb", "V", "j", ":", messages=messages)
    assert messages == [(":", "'<,'>")]


def test_editor_messages_for_ex_and_backward_search():
    messages: list = []
    run("a", "Z", "Z", "Z", "Q", "&", "g", "&", "@", ":", "?", messages=messages)
    assert messages == [("ex", "x"), ("ex", "q!"), ("ex", "&"), ("ex", "%&&"), ("ex", None), ("search", True)]


def test_sentence_text_objects():
    assert run("One two. Three four.", "w", "w", "w", "d", "i", "s")[0] == "One two. "
    assert run("One two. Three four.", "d", "a", "s")[0] == "Three four."


def test_parse_substitute():
    assert parse_substitute("s/a/b/g") == ("", "a", "b", "g")
    assert parse_substitute("%s#a/b#c#") == ("%", "a/b", "c", "")
    assert parse_substitute(r"s/a\/b/c/") == ("", "a/b", "c", "")
    assert parse_substitute("'<,'>s/x/y/") == ("'<,'>", "x", "y", "")
    assert parse_substitute("2,$s/x/y") == ("2,$", "x", "y", "")
    assert parse_substitute("&&") == ("", None, None, "&")
    assert parse_substitute("%&&") == ("%", None, None, "&")
    assert parse_substitute("set lazy") is None
    assert parse_substitute("sp file") is None
    assert parse_substitute("stale") is None
    assert vim_regex(r"\(ab\)\+\|c(d)") == r"(ab)+|c\(d\)"
    assert vim_regex(r"\<word\>") == r"\bword\b"


def test_substitute_on_the_editor():
    def sub(span, pattern, replacement, flags, text="foo foo\nfoo bar\nbaz", keys=()):
        def act(editor):
            count = editor.substitute(span, pattern, replacement, flags)
            return editor.text, count

        return run(text, *keys, after=act)

    assert sub("", "foo", "x", "") == ("x foo\nfoo bar\nbaz", (1, 1))
    assert sub("", "foo", "x", "g") == ("x x\nfoo bar\nbaz", (2, 1))
    assert sub("%", "foo", "x", "g") == ("x x\nx bar\nbaz", (3, 2))
    assert sub("2,3", "ba", "BA", "") == ("foo foo\nfoo BAr\nBAz", (2, 2))
    assert sub("%", r"\(f\)oo", r"\u\1&", "") == ("Ffoo foo\nFfoo bar\nbaz", (2, 2))
    assert sub("%", "FOO", "x", "i") == ("x foo\nx bar\nbaz", (2, 2))
    assert sub("%", "FOO", "x", "") == ("foo foo\nfoo bar\nbaz", (0, 0))
    assert sub("%", " ", r"\r", "") == ("foo\nfoo\nfoo\nbar\nbaz", (2, 2))
    assert sub("%", "o", "0", "gn") == ("foo foo\nfoo bar\nbaz", (6, 2))
    assert sub("'<,'>", "a", "A", "g", keys=("j", "V", "j", "escape")) == ("foo foo\nfoo bAr\nbAz", (2, 2))


def test_substitute_repeats():
    def act(editor):
        editor.substitute("", "o", "0", "g")
        editor.move_cursor((1, 0))
        editor.substitute("", None, None, "&")
        editor.move_cursor((2, 0))
        editor.substitute("", None, None, "")
        return editor.text

    assert run("oo\noo\noo", after=act) == "00\n00\n0o"


def test_app_ctrl_keys_are_vim_in_the_editor_and_app_keys_in_cell_mode(tmp_path, monkeypatch):
    from test_commands import sources, ui

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    async def test(app, pilot):
        first = app.cells()[0]
        first.editor.focus()
        await pilot.pause()
        await pilot.press("ctrl+d", "ctrl+e", "ctrl+b")
        await pilot.pause()
        assert len(app.cells()) == 3 and app.focused is first.editor
        await pilot.press("ctrl+n")
        await pilot.pause()
        assert len(app.cells()) == 3 and app.focused is app.cells()[1].editor
        await pilot.press("ctrl+p")
        await pilot.pause()
        assert app.focused is first.editor
        await pilot.press("ctrl+v")
        assert app.ui_mode == "v-block"
        await pilot.press("escape", "ctrl+o")
        assert app.ui_mode == "normal" and app.focused is first.editor
        await pilot.press("i", "ctrl+w", "ctrl+t", "escape")
        await pilot.pause()
        assert first.source == "    a = 1" and app.focused is first.editor
        await pilot.press(":", *"s/1/2/", "enter")
        await pilot.pause()
        assert first.source == "    a = 2"
        await pilot.press(":", *"%s/a/A/", "enter", "u", "&")
        await pilot.pause()
        assert first.source == "    A = 2"
        await pilot.press("escape")
        await pilot.pause()
        assert app.ui_mode == "cell"
        await pilot.press("ctrl+n")
        await pilot.pause()
        assert len(app.cells()) == 4
        app.cells()[1].focus()
        await pilot.pause()
        await pilot.press("ctrl+d")
        await pilot.pause()
        assert sources(app) == ["    A = 2", "b = 2", "print('c')"]

    ui(tmp_path, test)


def test_question_mark_searches_backward_and_n_follows(tmp_path, monkeypatch):
    from test_commands import ui

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    async def test(app, pilot):
        first, second, third = app.cells()
        third.editor.focus()
        await pilot.pause()
        await pilot.press("?", *"= ", "enter")
        await pilot.pause()
        assert app.focused is second.editor
        await pilot.press("n")
        await pilot.pause()
        assert app.focused is first.editor
        await pilot.press("N")
        await pilot.pause()
        assert app.focused is second.editor

    ui(tmp_path, test)


def test_digraphs_in_insert_mode():
    assert run("", "i", "ctrl+k", "a", "colon", "ctrl+k", "minus", "greater_than_sign", "ctrl+k", "a", "asterisk", "escape")[0] == "ä→α"
    assert run("", "i", "ctrl+k", "colon", "a", "ctrl+k", "x", "q", "escape")[0] == "äq"


def test_K_and_folds_post_messages():
    class Recorder(EditorApp):
        def on_vim_text_area_help_requested(self, event):
            self.messages.append(("help", event.name, event.code[: event.offset]))

        def on_vim_text_area_fold_requested(self, event):
            self.messages.append(("fold", event.action))

    async def main():
        app = Recorder("x = os.path.join(a)")
        async with app.run_test() as pilot:
            app.query_one(VimTextArea).focus()
            await pilot.press("f", "j", "K", "z", "c", "z", "a", "z", "M", "z", "R")
            await pilot.pause()
            return app.messages

    assert asyncio.run(main()) == [("help", "os.path.join", "x = os.path.join"), ("fold", "close"), ("fold", "toggle"), ("fold", "close_all"), ("fold", "open_all")]


def test_line_commands_on_the_editor():
    def act(*commands):
        def go(editor):
            for name, top, bottom, arg in commands:
                editor.line_command(top, bottom, name, arg)
            return editor.text

        return go

    text = "a\nb\nc\nd"
    assert run(text, after=act(("d", 1, 2, ""))) == "a\nd"
    assert run(text, after=act(("m", 3, 3, "0"))) == "d\na\nb\nc"
    assert run(text, after=act(("m", 0, 0, "$"))) == "b\nc\nd\na"
    assert run(text, after=act(("t", 0, 1, "$"))) == "a\nb\nc\nd\na\nb"
    assert run(text, after=act((">", 1, 2, ""))) == "a\n    b\n    c\nd"
    assert run(text, after=act(("j", 0, 2, ""))) == "a b c\nd"
    assert run(text, after=act(("normal", 0, 3, "A;"))) == "a;\nb;\nc;\nd;"
    assert run(text, after=act(("normal", 0, 3, "dd"))) == ""


def test_global_command_on_the_editor():
    from gpython.vim import compile_pattern

    def act(pattern, command, invert=False):
        def go(editor):
            rows = editor.matching_rows(0, editor.document.line_count - 1, compile_pattern(pattern), invert)
            editor.global_command(rows, command)
            return editor.text

        return go

    text = "x = 1\n# note\ny = 2\n# more"
    assert run(text, after=act("^#", "d")) == "x = 1\ny = 2"
    assert run(text, after=act("^#", "d", invert=True)) == "# note\n# more"
    assert run(text, after=act("#", "s/#/##/")) == "x = 1\n## note\ny = 2\n## more"
    assert run(text, after=act("=", "normal A  # set")) == "x = 1  # set\n# note\ny = 2  # set\n# more"
    assert run(text, after=act("^#", "m0")) == "# more\n# note\nx = 1\ny = 2"
    assert run(text, after=act("^#", "t$")) == "x = 1\n# note\ny = 2\n# more\n# note\n# more"


def _notebook_ui(tmp_path, monkeypatch, test, sources=("a = 1\nfoo = a", "foo = 2", "print(foo)"), kernel=False):
    from test_commands import ui

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    ui(tmp_path, test, sources=list(sources), kernel=kernel)


def test_percent_s_changes_the_whole_notebook_like_vims_file(tmp_path, monkeypatch):
    async def test(app, pilot):
        first, second, third = app.cells()
        second.editor.focus()
        await pilot.press(":", *"%s/foo/bar/g", "enter")
        await pilot.pause()
        assert [c.source for c in app.cells()] == ["a = 1\nbar = a", "bar = 2", "print(bar)"]
        await pilot.press(":", *"s/bar/baz/", "enter")
        await pilot.pause()
        assert [c.source for c in app.cells()] == ["a = 1\nbar = a", "baz = 2", "print(bar)"]
        first.editor.focus()
        await pilot.press(":", *".,$s/bar/qux/", "enter")
        await pilot.pause()
        assert [c.source for c in app.cells()] == ["a = 1\nqux = a", "baz = 2", "print(qux)"]
        await pilot.press("escape")
        await pilot.pause()
        assert app.ui_mode == "cell"
        await pilot.press(":", *"s/a/A/g", "enter")
        await pilot.pause()
        assert app.cells()[0].source == "A = 1\nqux = A"

    _notebook_ui(tmp_path, monkeypatch, test)


def test_g_and_line_commands_across_the_notebook(tmp_path, monkeypatch):
    from gpython.textview import TextView

    async def test(app, pilot):
        app.cells()[1].editor.focus()
        await pilot.press(":", *"g/foo/", "enter")
        await pilot.pause()
        assert isinstance(app.screen, TextView)
        await pilot.press("q")
        await pilot.pause()
        await pilot.press(":", *"g/foo/normal A  # x", "enter")
        await pilot.pause()
        assert [c.source for c in app.cells()] == ["a = 1\nfoo = a  # x", "foo = 2  # x", "print(foo)  # x"]
        await pilot.press(":", *"v/print/d", "enter")
        await pilot.pause()
        assert [c.source for c in app.cells()] == ["", "", "print(foo)  # x"]
        app.cells()[2].editor.focus()
        await pilot.press(":", *"t0", "enter", ":", *"d", "enter")
        await pilot.pause()
        assert app.cells()[2].source == "print(foo)  # x" and len(app.cells()) == 3
        await pilot.press("escape", ":", *"d", "enter")
        await pilot.pause()
        assert len(app.cells()) == 2

    _notebook_ui(tmp_path, monkeypatch, test)


def test_folds_hide_cells_and_H_toggles_all(tmp_path, monkeypatch):
    async def test(app, pilot):
        first, second, third = app.cells()
        first.editor.focus()
        await pilot.press("z", "c")
        await pilot.pause()
        assert first.code_hidden and app.ui_mode == "cell"
        await pilot.press("H")
        await pilot.pause()
        assert not any(c.code_hidden for c in app.cells())
        await pilot.press("H")
        await pilot.pause()
        assert all(c.code_hidden for c in app.cells())
        await app._run_ex("foldopen all")
        await pilot.pause()
        assert not any(c.code_hidden for c in app.cells())

    _notebook_ui(tmp_path, monkeypatch, test)


def test_K_shows_the_kernels_help_and_ctrl_k_is_a_digraph_in_insert_mode(tmp_path, monkeypatch):
    from gpython.textview import TextView

    async def test(app, pilot):
        cell = app.cells()[0]
        cell.editor.focus()
        await pilot.press("K")
        for _ in range(50):
            await pilot.pause(0.1)
            if isinstance(app.screen, TextView):
                break
        assert isinstance(app.screen, TextView)
        await pilot.press("q")
        await pilot.pause()
        await pilot.press("A", "ctrl+k", "e", "apostrophe", "escape")
        await pilot.pause()
        assert cell.source == "len([1])é"
        await app._run_ex("digraphs")
        await pilot.pause()
        assert isinstance(app.screen, TextView)

    _notebook_ui(tmp_path, monkeypatch, test, sources=("len([1])",), kernel=True)
