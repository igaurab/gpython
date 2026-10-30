"""Vim visual mode (v, V) and the yank/put/operator keys around it."""

import asyncio

from test_repaint import same_as_full
from test_vim import EditorApp

from gpython.app import GPython
from gpython.vim import VimTextArea


def press(text: str, *keys: str) -> dict:
    """Press keys in a fresh editor; return its text, cursor, register, visual state and selection."""

    async def main():
        app = EditorApp(text)
        VimTextArea.mode = "normal"
        VimTextArea.register, VimTextArea.register_linewise = "", False
        async with app.run_test() as pilot:
            editor = app.query_one(VimTextArea)
            editor.focus()
            await pilot.press(*keys)
            await pilot.pause()
            return {
                "text": editor.text,
                "cursor": editor.cursor_location,
                "register": (VimTextArea.register, VimTextArea.register_linewise),
                "visual": editor.visual,
                "mode": editor.mode,
                "selected": editor.selected_text,
                "messages": list(app.messages),
            }

    return asyncio.run(main())


TEXT = "one two\nthree four\nfive six"


def test_v_G_y_yanks_to_the_end_charwise():
    r = press(TEXT, "w", "v", "G", "y")
    assert r["register"] == ("two\nthree four\nf", False)
    assert r["visual"] == "" and r["cursor"] == (0, 4) and r["text"] == TEXT


def test_V_G_y_and_ggVGy_yank_lines():
    assert press(TEXT, "j", "V", "G", "y")["register"] == ("three four\nfive six", True)
    r = press(TEXT, "j", "g", "g", "V", "G", "y")
    assert r["register"] == (TEXT, True) and r["cursor"] == (0, 0)


def test_selection_shows_vim_inclusive_range():
    assert press(TEXT, "v", "l", "l")["selected"] == "on"
    r = press(TEXT, "$", "v", "h", "h")
    assert r["visual"] == "v" and r["selected"] == "two" and r["cursor"] == (0, 4)
    r = press(TEXT, "V")
    assert r["visual"] == "V"


def test_linewise_yank_then_put():
    assert press(TEXT, "V", "y", "j", "p")["text"] == "one two\nthree four\none two\nfive six"
    assert press(TEXT, "V", "j", "y", "G", "P")["text"] == "one two\nthree four\none two\nthree four\nfive six"


def test_charwise_yank_then_put():
    r = press(TEXT, "v", "e", "y", "$", "p")
    assert r["text"] == "one twoone\nthree four\nfive six" and r["cursor"] == (0, 9)
    assert press(TEXT, "v", "e", "y", "2", "P")["text"] == "oneoneone two\nthree four\nfive six"


def test_delete_and_x():
    r = press(TEXT, "v", "e", "d")
    assert r["text"] == " two\nthree four\nfive six" and r["register"] == ("one", False)
    assert press(TEXT, "w", "v", "j", "x")["text"] == "one  four\nfive six"
    r = press(TEXT, "j", "V", "G", "d")
    assert r["text"] == "one two" and r["register"] == ("three four\nfive six", True) and r["cursor"] == (0, 0)
    assert press(TEXT, "v", "X")["text"] == "three four\nfive six"


def test_change():
    r = press(TEXT, "v", "e", "c", "Z", "escape")
    assert r["text"] == "Z two\nthree four\nfive six" and r["mode"] == "normal"
    r = press(TEXT, "j", "V", "c", "x", "y")
    assert r["text"] == "one two\nxy\nfive six" and r["mode"] == "insert"
    assert press(TEXT, "v", "s", "Q")["text"] == "Qne two\nthree four\nfive six"


def test_put_replaces_selection():
    r = press(TEXT, "y", "w", "j", "v", "e", "p")
    assert r["text"] == "one two\none  four\nfive six" and r["register"] == ("three", False)
    r = press(TEXT, "y", "w", "j", "v", "e", "P")
    assert r["register"] == ("one ", False)
    r = press(TEXT, "y", "y", "j", "V", "j", "p")
    assert r["text"] == "one two\none two" and r["register"] == ("three four\nfive six", True)


def test_indent_join_case_replace():
    assert press("a\nb\n\nc", "V", "G", ">")["text"] == "    a\n    b\n\n    c"
    assert press("        a\n    b", "V", "j", "<")["text"] == "    a\nb"
    assert press("a\nb", "V", "j", "2", ">")["text"] == "        a\n        b"
    assert press("a\n    b\nc", "V", "G", "J")["text"] == "a b c"
    assert press("a\n    b", "v", "g", "J")["text"] == "a    b"
    assert press("Hello World", "v", "e", "~")["text"] == "hELLO World"
    assert press("Hello World", "V", "U")["text"] == "HELLO WORLD"
    assert press("Hello World", "V", "u")["text"] == "hello world"
    r = press("abc\ndef", "v", "j", "r", "x")
    assert r["text"] == "xxx\nxef" and r["cursor"] == (0, 0)


def test_counts_motions_and_o():
    text = "\n".join(f"line {i}" for i in range(1, 8))
    assert press(text, "v", "3", "j", "y")["register"] == ("line 1\nline 2\nline 3\nl", False)
    assert press(text, "V", "5", "G", "y")["register"][0].count("\n") == 4
    r = press(text, "j", "j", "V", "j", "o", "k", "y")
    assert r["register"] == ("line 2\nline 3\nline 4", True)
    r = press("one two three", "w", "v", "e", "o", "b", "y")
    assert r["register"] == ("one two", False)
    assert press("a(b, c)d", "f", "(", "v", "%", "y")["register"] == ("(b, c)", False)
    assert press("x = a.b(c)", "v", "f", "(", "y")["register"] == ("x = a.b(", False)
    assert press("x = a.b(c)", "v", "t", "(", "y")["register"] == ("x = a.b", False)
    assert press("a\nb\n\nc", "v", "}", "y")["register"] == ("a\nb\n\n", False)
    assert press("foo.bar baz", "v", "E", "y")["register"] == ("foo.bar", False)


def test_text_objects_visual_and_operator():
    assert press("foo bar baz", "w", "v", "i", "w", "y")["register"] == ("bar", False)
    assert press("foo bar baz", "w", "v", "a", "w", "y")["register"] == ("bar ", False)
    assert press("a\nb\n\nc", "v", "i", "p", "y")["register"] == ("a\nb", True)
    assert press("a\nb\n\nc", "v", "a", "p", "y")["register"] == ("a\nb\n", True)
    assert press('x = "hello" + y', "f", "e", "v", "i", '"', "y")["register"] == ("hello", False)
    assert press("f(a, (b))", "f", "a", "v", "i", "(", "y")["register"] == ("a, (b)", False)
    assert press("f(a, (b))", "f", "a", "v", "a", "(", "y")["register"] == ("(a, (b))", False)
    assert press("d = {1: [2]}", "f", "2", "v", "i", "[", "d")["text"] == "d = {1: []}"
    assert press("foo bar", "d", "i", "w")["text"] == " bar"
    assert press("foo bar", "c", "a", "w", "Z", "escape")["text"] == "Zbar"
    assert press("f(a, b)", "f", "b", "d", "i", "(")["text"] == "f()"
    assert press("def f():\n    a\n    b\n\nc", "j", "d", "a", "p")["text"] == "c"
    r = press("if x:\n    a\n    b\n", "y", "i", "p", "G", "p")
    assert r["register"] == ("if x:\n    a\n    b", True)
    assert press("s = 'ab'", "d", "i", "'")["text"] == "s = ''"
    assert press("x = {\n    a\n}", "j", "d", "i", "{")["text"] == "x = {\n}"


def test_normal_mode_yank_put_keys():
    assert press(TEXT, "y", "y", "p")["text"] == "one two\none two\nthree four\nfive six"
    assert press(TEXT, "Y", "P")["text"] == "one two\none two\nthree four\nfive six"
    assert press(TEXT, "3", "y", "y")["register"] == (TEXT, True)
    assert press(TEXT, "y", "y", "2", "p")["text"].count("one two") == 3
    assert press(TEXT, "y", "w", "P")["text"].startswith("one one two")
    assert press(TEXT, "w", "y", "$")["register"] == ("two", False)
    assert press(TEXT, "w", "d", "w")["text"].startswith("one \nthree")
    assert press(TEXT, "d", "d", "p")["text"] == "three four\none two\nfive six"
    assert press(TEXT, "x", "p")["text"].startswith("noe two")
    assert press("foo bar", "d", "e")["text"] == " bar"
    assert press("a(b)c", "d", "f", ")")["text"] == "c"
    assert press("a.b.c.d", "f", ".", ";", "d", "t", ".")["text"] == "a.b.d"
    assert press("a\nb", ">", ">")["text"] == "    a\nb"
    assert press("a\nb\nc", "j", "y", "g", "g")["register"] == ("a\nb", True)


def test_escape_v_toggle_and_gv():
    r = press(TEXT, "v", "l", "escape")
    assert r["visual"] == "" and r["selected"] == "" and r["cursor"] == (0, 1) and r["messages"] == []
    assert press(TEXT, "v", "v")["visual"] == ""
    assert press(TEXT, "V", "V")["visual"] == ""
    assert press(TEXT, "v", "V")["visual"] == "V"
    assert press(TEXT, "V", "v")["visual"] == "v"
    r = press(TEXT, "v", "e", "escape", "0", "g", "v", "y")
    assert r["register"] == ("one", False)


def test_visual_stays_in_the_cell():
    r = press(TEXT, "v", "j", "j", "j", "J", "y")
    assert all(m[0] != "edge" for m in r["messages"])


def test_undo_restores_a_visual_operation_in_one_step():
    for keys in (("V", "j", "d"), ("v", "e", "d"), ("v", "j", "~"), ("V", "G", ">"), ("V", "G", "J"), ("y", "w", "j", "v", "e", "p"), ("v", "x")):
        r = press(TEXT, *keys, "u")
        assert r["text"] == TEXT and r["selected"] == "", keys


def test_status_bar_shows_visual_modes_and_focus_ends_visual(tmp_path):
    path = tmp_path / "nb.py"
    path.write_text("# %%\na = 1\nb = 2\n\n# %%\nc = 3\n")

    async def main():
        app = GPython(path)
        async with app.run_test(size=(100, 30)) as pilot:
            await app.loaded.wait()
            await pilot.pause()
            first = app.cells()[0].editor
            first.focus()
            await pilot.pause()
            status = lambda: str(app._status.render())
            await pilot.press("v")
            await pilot.pause()
            assert app.ui_mode == "visual" and "VISUAL" in status()
            await pilot.press("V")
            await pilot.pause()
            assert app.ui_mode == "v-line" and "V-LINE" in status()
            await pilot.press("escape")
            await pilot.pause()
            assert app.ui_mode == "normal" and "NORMAL" in status() and app.focused is first
            await pilot.press("v", "j")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert app.ui_mode == "cell"
            await pilot.press("v", "j", "escape", "escape")
            await pilot.pause()
            assert first.visual == "" and first.selected_text == ""
            first.focus()
            await pilot.press("V")
            await pilot.pause()
            app.cells()[1].editor.focus()
            await pilot.pause()
            assert first.visual == "" and app.ui_mode == "normal" and "NORMAL" in status()

    asyncio.run(asyncio.wait_for(main(), 60))


def test_visual_repaints_match_full_render(tmp_path):
    path = tmp_path / "nb.py"
    path.write_text("# %%\ndef f(x):\n    return (x + 1) * 2\n\n\nprint(f(3))\nzz = [1, 2]\n\n# %%\nb = 2\n")

    async def main():
        app = GPython(path)
        async with app.run_test(size=(100, 40)) as pilot:
            await app.loaded.wait()
            await pilot.pause()
            editor = app.cells()[0].editor
            editor.focus()
            await pilot.pause()
            steps = [
                ("v",), ("l", "l"), ("j",), ("j",), ("k", "k", "k"), ("$",), ("o",), ("escape",),
                ("j", "V"), ("j",), ("j", "j"), ("k",), ("o",), ("G",), ("g", "g"), ("v",), ("V",),
                ("y",), ("j", "v", "i", "("), ("escape",), ("V", "j", ">"), ("g", "v"), ("<",),
                ("V", "d"), ("u",), ("v", "e", "~"), ("u",), ("V", "G", "y"), ("escape",),
                ("j", "j", "v", "%"), ("d",), ("u",),
            ]
            for keys in steps:
                await pilot.press(*keys)
                await pilot.pause()
                await same_as_full(editor, pilot)

    asyncio.run(asyncio.wait_for(main(), 120))
