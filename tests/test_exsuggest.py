"""Suggestions in the ':' prompt: command names by prefix, then the arguments of a command."""

import asyncio

import pytest
from textual.widgets import Input, OptionList

from gpython import exsuggest
from gpython.app import GPython, Prompt
from gpython.vim import VimTextArea

COMMANDS = [
    (("w", "write", "w!"), "Save the notebook (:w name.py saves it with a new name)"),
    (("sp", "split"), "Split the window. More text"),
    (("set",), "Options"),
    (("show",), "Show this cell's code"),
    (("q!", "quit!"), "Close this window without saving"),
    (("Ex", "Explore"), "Show or hide the file explorer"),
    (("d", "delete"), "Delete the current cell"),
]


def labels(rows):
    return [row.label for row in rows]


def suggest(line, arguments=lambda command, typed: []):
    return exsuggest.suggest(line, COMMANDS, arguments)


def test_names_that_start_with_the_typed_text():
    rows = suggest("s")
    assert labels(rows) == ["s", "sp", "set", "show"]
    assert rows[1].line == "sp" and rows[1].detail == "Split the window"
    assert labels(suggest("sh")) == ["show"]
    assert labels(suggest("spl")) == ["split"]
    assert labels(suggest("zz")) == []


def test_an_exact_name_comes_first_and_capitals_do_not_matter():
    assert labels(suggest("set"))[0] == "set"
    assert labels(suggest("e")) == ["Ex"]
    assert labels(suggest("q")) == ["q!"]


def test_an_empty_prompt_lists_each_command_once():
    names = labels(suggest(""))
    assert names[:3] == ["w", "sp", "set"]
    assert names.count("d") == 1 and "norm" in names


def test_after_a_range_only_the_line_commands_show():
    rows = suggest("'<,'>")
    assert "w" not in labels(rows) and {"s", "d", "norm"} <= set(labels(rows))
    assert [row.line for row in suggest("%no")] == ["%norm"]


def test_a_pattern_command_and_an_unknown_command_give_nothing():
    assert suggest("s/old/new") == []
    assert suggest("%s/a/b/g") == []
    assert suggest("nothing arg") == []
    assert suggest("'<,'>d x") == []


def test_arguments_of_a_command_by_prefix():
    seen = []

    def arguments(command, typed):
        seen.append((command, typed))
        return [("all", "everything"), ("all metadata", ""), ("other", "")]

    rows = suggest("write  al", arguments)
    assert seen == [("w", "al")]
    assert [row.line for row in rows] == ["write all", "write all metadata"]


def test_a_value_shows_after_the_equals_sign():
    candidates = exsuggest.set_candidates()
    assert labels(exsuggest.argument_suggestions("set", "cu", candidates)) == ["cursor="]
    assert labels(exsuggest.argument_suggestions("set", "cursor=", candidates)) == ["cursor=", "cursor=bar", "cursor=underline", "cursor=block"]
    assert labels(exsuggest.argument_suggestions("set", "debug_detail=b", candidates)) == ["debug_detail=balanced"]
    assert "novim" in labels(exsuggest.argument_suggestions("set", "", candidates))


def test_the_debug_options_are_the_ones_the_kernel_knows():
    from gpython.kernel_side import DETAILS, SETTINGS

    assert set(exsuggest.DEBUG_OPTIONS) == {"detail", *SETTINGS}
    assert [value for value, _ in exsuggest.DEBUG_OPTIONS["detail"][1]] == list(DETAILS)


def test_each_set_option_is_accepted_by_set(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    app = GPython(None)
    unknown = []
    monkeypatch.setattr(app, "notify", lambda message, **kw: unknown.append(message) if str(message).startswith("Unknown option") else None)
    for setter in ("set_vim", "set_lazy", "set_line_numbers", "set_timing", "set_autoinstall", "set_concurrent", "set_concurrency", "set_concurrent_verify", "set_status_bar", "set_markers", "set_insert_cursor", "set_debug_option"):
        monkeypatch.setattr(app, setter, lambda *args, **kw: None)
    for option, _ in exsuggest.set_candidates():
        app._ex_set(option)
    assert unknown == []


def test_argument_tables_name_real_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    first_names = {names[0] for names, _, _ in GPython(None).ex_commands()}
    named = {*exsuggest.STATIC_ARGUMENTS, *exsuggest.NOTEBOOK_FILE_COMMANDS, *exsuggest.FOLDER_COMMANDS, *exsuggest.BUFFER_COMMANDS, "set", "theme"}
    assert named <= first_names


def test_paths(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / ".hidden").mkdir()
    for name in ("b.py", "a.ipynb", "notes.txt", "sub/in.py"):
        (tmp_path / name).write_text("")
    notebooks = (".py", ".ipynb")
    assert exsuggest.path_candidates("", notebooks, tmp_path) == [("sub/", "folder"), ("a.ipynb", ""), ("b.py", "")]
    assert exsuggest.path_candidates("sub/i", notebooks, tmp_path) == [("sub/in.py", "")]
    assert exsuggest.path_candidates("", None, tmp_path) == [("sub/", "folder")]
    assert (".hidden/", "folder") in exsuggest.path_candidates(".h", None, tmp_path)
    assert exsuggest.path_candidates("missing/", notebooks, tmp_path) == []
    assert (f"{tmp_path}/b.py", "") in exsuggest.path_candidates(f"{tmp_path}/", notebooks)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.chdir(tmp_path)
    VimTextArea.enabled = True
    yield tmp_path
    VimTextArea.enabled = True


def ui(tmp_path, test):
    async def main():
        path = tmp_path / "nb.py"
        path.write_text("# %%\na = 1\n\n# %%\nb = 2\n")
        (tmp_path / "other.py").write_text("x = 1\n")
        app = GPython(path)
        async with app.run_test(size=(110, 30)) as pilot:
            await app.loaded.wait()
            await test(app, pilot)

    asyncio.run(main())


def shown(app) -> list[str]:
    options = app.screen.query_one(OptionList)
    if not options.display:
        return []
    return [str(options.get_option_at_index(i).prompt).split()[0] for i in range(options.option_count)]


def test_the_prompt_shows_commands_and_tab_takes_one(isolated):
    async def test(app, pilot):
        await pilot.press("escape", ":", "s", "h")
        await pilot.pause()
        field = app.screen.query_one(Input)
        assert shown(app) == ["show"]
        await pilot.press("backspace")
        await pilot.pause()
        assert shown(app)[:3] == ["s", "sp", "stale"]
        await pilot.press("tab", "tab")
        assert field.value == "sp" and field.cursor_position == 2
        assert shown(app)[:3] == ["s", "sp", "stale"]
        await pilot.press("shift+tab")
        assert field.value == "s"
        await pilot.press("up")
        assert field.value == shown(app)[-1]
        await pilot.press("down", "ctrl+n", "ctrl+p")
        assert field.value == "s" and len(app.cells()) == 2
        await pilot.press("escape")

    ui(isolated, test)


def test_the_prompt_shows_arguments_and_enter_runs_the_line(isolated):
    async def test(app, pilot):
        await pilot.press("escape", ":", *"set no")
        await pilot.pause()
        assert shown(app)[:2] == ["novim", "nolazy"]
        await pilot.press("escape", ":", *"e o")
        await pilot.pause()
        assert shown(app) == ["other.py"]
        await pilot.press("escape", ":", *"b ")
        await pilot.pause()
        assert shown(app) == ["nb.py"]
        await pilot.press("escape", ":", *"theme gruvbox-")
        await pilot.pause()
        assert shown(app) == ["gruvbox-light"]
        await pilot.press("tab", "enter")
        await pilot.pause()
        assert app.theme == "gruvbox-light" and not isinstance(app.screen, Prompt)

    ui(isolated, test)


def test_a_click_on_a_row_takes_it_and_the_input_keeps_the_keys(isolated):
    async def test(app, pilot):
        await pilot.press("escape", ":", *"sh")
        await pilot.pause()
        await pilot.click(OptionList, offset=(70, 1))
        await pilot.pause()
        field = app.screen.query_one(Input)
        assert field.value == "show" and app.focused is field

    ui(isolated, test)


def test_other_prompts_have_no_suggestions(isolated):
    async def test(app, pilot):
        await pilot.press("escape", "/")
        await pilot.pause()
        assert isinstance(app.screen, Prompt) and not app.screen.query(OptionList)
        await pilot.press("tab", "a", "enter")
        await pilot.pause()
        assert not isinstance(app.screen, Prompt)

    ui(isolated, test)
