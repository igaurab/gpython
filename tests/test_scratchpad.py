"""The scratchpad folder: `gpython --scratchpad DIR`, and where an untitled notebook is saved."""

import asyncio
import sys
from types import SimpleNamespace

import pytest
from textual.widgets import Input

from gpython import config, exsuggest, main, parse_args, scratchpad
from gpython.app import GPython, Prompt


@pytest.fixture
def started_in(tmp_path, monkeypatch):
    here = tmp_path / "here"
    here.mkdir()
    monkeypatch.chdir(here)
    return here


def run_cli(monkeypatch, *args) -> int:
    monkeypatch.setattr(sys, "argv", ["gpy", *args])
    with pytest.raises(SystemExit) as stop:
        main()
    return stop.value.code


def test_the_flag_is_parsed_with_a_space_or_an_equals_sign():
    assert parse_args(["--scratchpad", "."])["scratchpad"] == "."
    assert parse_args(["--scratchpad=~/pads"])["scratchpad"] == "~/pads"
    assert parse_args(["--scratchpad", ""])["scratchpad"] == ""
    assert parse_args(["nb.py"])["scratchpad"] is None
    with pytest.raises(SystemExit):
        parse_args(["--scratchpad"])


def test_a_dot_stores_the_folder_gpython_is_started_in(started_in, monkeypatch, capsys):
    assert run_cli(monkeypatch, "--scratchpad", ".") == 0
    assert config.load()["scratchpad"] == str(started_in.resolve())
    assert scratchpad.folder() == started_in.resolve()
    assert str(started_in.resolve()) in capsys.readouterr().out


def test_a_missing_folder_is_made(started_in, monkeypatch):
    assert run_cli(monkeypatch, "--scratchpad", "new/pads") == 0
    assert (started_in / "new" / "pads").is_dir()
    assert scratchpad.folder() == (started_in / "new" / "pads").resolve()


def test_a_file_is_refused(started_in, monkeypatch, capsys):
    (started_in / "file.txt").write_text("")
    assert run_cli(monkeypatch, "--scratchpad", "file.txt") == 1
    assert "file.txt" in capsys.readouterr().err
    assert scratchpad.folder() is None


def test_an_empty_value_forgets_the_folder(started_in, monkeypatch):
    run_cli(monkeypatch, "--scratchpad", ".")
    assert run_cli(monkeypatch, "--scratchpad", "") == 0
    assert scratchpad.folder() is None


def test_names_with_no_folder_go_to_the_scratchpad(started_in, tmp_path):
    pads = tmp_path / "pads"
    assert scratchpad.resolve("save.py").resolve() == started_in.resolve() / "save.py"
    scratchpad.set_folder(str(pads))
    assert scratchpad.resolve("save.py") == pads.resolve() / "save.py"
    assert scratchpad.resolve("sub/save.py") == pads.resolve() / "sub" / "save.py"
    assert scratchpad.resolve("./save.py").resolve() == started_in.resolve() / "save.py"
    assert scratchpad.resolve("../save.py").resolve() == tmp_path.resolve() / "save.py"
    assert scratchpad.resolve(str(tmp_path / "abs.py")) == tmp_path / "abs.py"
    assert scratchpad.resolve("~/save.py").is_absolute() and pads.resolve() not in scratchpad.resolve("~/save.py").parents


def untitled(test):
    async def scenario():
        app = GPython(None)
        async with app.run_test(size=(110, 30)) as pilot:
            await app.loaded.wait()
            await test(app, pilot)

    asyncio.run(scenario())


def test_w_with_a_name_saves_an_untitled_notebook_in_the_scratchpad(started_in, tmp_path):
    pads = tmp_path / "pads"
    scratchpad.set_folder(str(pads))

    async def test(app, pilot):
        await app._run_ex("w save.py")
        await pilot.pause()
        assert app.pane.path == pads.resolve() / "save.py"
        assert (pads / "save.py").is_file() and not (started_in / "save.py").exists()
        await app._run_ex("w copy.py")
        await pilot.pause()
        assert (started_in / "copy.py").is_file() and not (pads / "copy.py").exists()

    untitled(test)


def test_w_saves_where_gpython_started_when_no_scratchpad_is_set(started_in):
    async def test(app, pilot):
        await app._run_ex("w save.py")
        await pilot.pause()
        assert (started_in / "save.py").is_file()

    untitled(test)


def test_the_save_prompt_names_the_scratchpad_and_saves_there(started_in, tmp_path):
    pads = tmp_path / "pads"
    scratchpad.set_folder(str(pads))

    async def test(app, pilot):
        app.pane.save()
        await pilot.pause()
        assert isinstance(app.screen, Prompt)
        field = app.screen.query_one(Input)
        assert scratchpad.shown(pads.resolve()) in field.placeholder
        field.value = "typed.py"
        await pilot.press("enter")
        await pilot.pause()
        assert (pads / "typed.py").is_file()

    untitled(test)


def test_w_suggests_the_files_of_the_scratchpad_for_an_untitled_notebook(started_in, tmp_path):
    pads = tmp_path / "pads"
    scratchpad.set_folder(str(pads))
    (pads / "old.py").write_text("")
    (started_in / "local.py").write_text("")
    new, named = SimpleNamespace(pane=SimpleNamespace(path=None)), SimpleNamespace(pane=SimpleNamespace(path=started_in / "nb.py"))
    assert exsuggest.argument_candidates(new, "w", "") == [("old.py", "")]
    assert exsuggest.argument_candidates(new, "w", "./") == [("./local.py", "")]
    assert exsuggest.argument_candidates(new, "e", "") == [("local.py", "")]
    assert exsuggest.argument_candidates(named, "w", "") == [("local.py", "")]
