"""`gpython --update` and `:update`: the command is chosen from how gpython was installed."""

import subprocess
import sys

import pytest

from gpython import parse_args, run_update, update


def test_the_words_after_update_name_a_channel():
    assert update.channel_of("") == update.channel_of("stable") == update.channel_of("PyPI") == "stable"
    assert update.channel_of("dev") == update.channel_of("github") == "dev"
    assert update.channel_of("nightly") is None


def test_parse_args_takes_the_channel_with_or_without_an_equals_sign():
    assert parse_args([])["update"] is None
    assert parse_args(["--update"])["update"] == ""
    assert parse_args(["--update", "dev"])["update"] == "dev"
    assert parse_args(["--update=stable"])["update"] == "stable"
    assert parse_args(["--update", "nightly"])["update"] == "nightly"
    assert parse_args(["--update", "--no-vim"])["update"] == ""
    opts = parse_args(["dev"])
    assert opts["update"] is None and [str(p) for p in opts["paths"]] == ["dev"]


def test_the_install_kind_comes_from_the_environment_folder(tmp_path):
    assert update.install_kind(tmp_path) == "pip"
    (tmp_path / "pipx_metadata.json").write_text("{}")
    assert update.install_kind(tmp_path) == "pipx"
    (tmp_path / "uv-receipt.toml").write_text("")
    assert update.install_kind(tmp_path) == "uv-tool"


def test_each_install_kind_has_its_command(monkeypatch):
    monkeypatch.setattr(update.shutil, "which", lambda name: f"/bin/{name}")
    assert update.command("uv-tool", "stable") == ["uv", "tool", "install", "--force", "--refresh", "gpynb"]
    assert update.command("uv-tool", "dev")[-1] == update.REPOSITORY
    assert update.command("pipx", "stable") == ["pipx", "install", "--force", "gpynb"]
    assert update.command("pip", "stable", has_pip=True) == [sys.executable, "-m", "pip", "install", "--upgrade", "gpynb"]
    assert update.command("pip", "dev", has_pip=False)[:3] == ["uv", "pip", "install"]
    monkeypatch.setattr(update.shutil, "which", lambda name: None)
    assert update.command("uv-tool", "stable") is None
    assert update.command("pip", "stable", has_pip=False) is None


def test_the_summary_says_what_changed():
    assert update.summary("0.2.0", "0.2.1", "stable") == "gpython 0.2.0 is now 0.2.1. Start gpython again to use it"
    assert update.summary("0.2.1", "0.2.1", "stable") == "gpython 0.2.1 is the newest release"
    assert "GitHub" in update.summary("0.2.1", "0.2.1", "dev")


def fake_install(monkeypatch, kind="uv-tool", returncode=0, version="9.9.9"):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[:2] == [sys.executable, "-c"]:
            return subprocess.CompletedProcess(argv, 0, stdout=f"{version}\n", stderr="")
        return subprocess.CompletedProcess(argv, returncode, stdout="", stderr="no network\n")

    monkeypatch.setattr(update, "checkout", lambda: None)
    monkeypatch.setattr(update, "install_kind", lambda: kind)
    monkeypatch.setattr(update.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(update.subprocess, "run", run)
    return calls


def test_update_runs_the_installer_and_reports_the_new_version(monkeypatch, capsys):
    calls = fake_install(monkeypatch)
    assert run_update("") == 0
    assert calls[0] == ["uv", "tool", "install", "--force", "--refresh", "gpynb"]
    assert "is now 9.9.9" in capsys.readouterr().out


def test_update_dev_installs_from_github(monkeypatch, capsys):
    calls = fake_install(monkeypatch, kind="pipx")
    assert run_update("dev") == 0
    assert calls[0] == ["pipx", "install", "--force", update.REPOSITORY]


def test_a_failed_install_and_an_unknown_channel_are_errors(monkeypatch, capsys):
    fake_install(monkeypatch, returncode=1)
    assert run_update("stable") == 1
    assert "did not finish" in capsys.readouterr().err
    assert run_update("nightly") == 2
    outcome = update.run("stable", "0.2.0", quiet=True)
    assert not outcome.ok and outcome.message.endswith("no network")


def test_a_source_checkout_is_left_to_git(monkeypatch, tmp_path):
    monkeypatch.setattr(update, "checkout", lambda: tmp_path)
    monkeypatch.setattr(update.subprocess, "run", lambda *a, **k: pytest.fail("no installer for a checkout"))
    outcome = update.run("stable", "0.2.0")
    assert not outcome.ok and "git pull" in outcome.message and str(tmp_path) in outcome.message


def test_this_test_environment_is_an_editable_checkout():
    folder = update.checkout()
    assert folder is not None and (folder / "pyproject.toml").exists()
