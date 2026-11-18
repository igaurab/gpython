"""`gpython --completions SHELL`: the scripts, and what real bash and zsh do with them."""

import shutil
import subprocess
import sys

import pytest

from gpython import completions, main, parse_args

BASH_DRIVER = 'source "$1"; shift; COMP_WORDS=("$@"); COMP_CWORD=$((${#COMP_WORDS[@]}-1)); _gpython; printf "%s\\n" "${COMPREPLY[@]}"'


def bash_completes(tmp_path, *words) -> list[str]:
    script = tmp_path / "gpython.bash"
    script.write_text(completions.script("bash"))
    out = subprocess.run(["bash", "-c", BASH_DRIVER, "_", str(script), *words], capture_output=True, text=True, check=True, cwd=tmp_path)
    return [line for line in out.stdout.splitlines() if line]


def test_the_flag_is_parsed():
    assert parse_args(["--completions", "zsh"])["completions"] == "zsh"
    assert parse_args(["--completions=fish"])["completions"] == "fish"


@pytest.mark.parametrize("shell", completions.SHELLS)
def test_each_script_is_printed_and_names_both_commands(shell, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["gpy", "--completions", shell])
    with pytest.raises(SystemExit) as stop:
        main()
    out = capsys.readouterr().out
    assert stop.value.code == 0
    assert "gpython" in out and "gpy" in out and "monokai" in out and "scratchpad" in out


def test_pwsh_is_powershell(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["gpy", "--completions", "pwsh"])
    with pytest.raises(SystemExit):
        main()
    assert capsys.readouterr().out == completions.script("powershell")
    assert "Register-ArgumentCompleter -Native -CommandName gpython, gpy" in completions.script("powershell")


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="needs PowerShell")
def test_powershell_completes_options_and_values(tmp_path):
    script = tmp_path / "gpython.ps1"
    script.write_text(completions.script("powershell"))
    ask = ". $args[0]; foreach ($line in 'gpy --scra', 'gpy --theme monok') { (TabExpansion2 $line $line.Length).CompletionMatches.CompletionText }"
    out = subprocess.run(["pwsh", "-NoProfile", "-Command", ask, str(script)], capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["--scratchpad", "monokai", "monokai-light"]


def test_an_unknown_shell_is_refused(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["gpy", "--completions", "tcsh"])
    with pytest.raises(SystemExit) as stop:
        main()
    assert stop.value.code == 2
    assert "bash, zsh, fish, powershell" in capsys.readouterr().err


def test_the_parser_takes_every_option_that_is_completed():
    for names, value, _ in completions.OPTIONS:
        for name in names:
            parse_args([name] if value is None else [name, "zsh"])


def test_the_theme_names_are_the_ones_the_app_takes():
    names = completions.theme_names()
    assert {"terminal", "monokai", "catppuccin", "gruvbox"} <= set(names)
    assert names == sorted(names)


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_bash_completes_options_values_and_notebooks(tmp_path):
    for name in ("a.py", "b.ipynb", "c.txt", "sp ace.py"):
        (tmp_path / name).write_text("")
    (tmp_path / "sub").mkdir()
    assert bash_completes(tmp_path, "gpy", "--scra") == ["--scratchpad"]
    assert bash_completes(tmp_path, "gpy", "--theme", "monok") == ["monokai", "monokai-light"]
    assert bash_completes(tmp_path, "gpy", "--scratchpad", "") == ["sub"]
    assert bash_completes(tmp_path, "gpy", "--completions", "") == list(completions.SHELLS)
    assert sorted(bash_completes(tmp_path, "gpy", "")) == ["a.py", "b.ipynb", "sp ace.py", "sub"]
    assert bash_completes(tmp_path, "gpy", "--extra", "") == []


@pytest.mark.skipif(shutil.which("zsh") is None, reason="needs zsh")
def test_zsh_reads_the_script_without_an_error(tmp_path):
    script = tmp_path / "_gpython"
    script.write_text(completions.script("zsh"))
    subprocess.run(["zsh", "-n", str(script)], check=True)
    loaded = subprocess.run(
        ["zsh", "-f", "-c", f"autoload -Uz compinit; compinit -u -d {tmp_path}/zcompdump; source {script}; print $_comps[gpy] $_comps[gpython]"],
        capture_output=True, text=True, check=True,
    )
    assert loaded.stdout.split() == ["_gpython", "_gpython"]


@pytest.mark.skipif(shutil.which("fish") is None, reason="needs fish")
def test_fish_reads_the_script_without_an_error(tmp_path):
    script = tmp_path / "gpython.fish"
    script.write_text(completions.script("fish"))
    subprocess.run(["fish", "--no-execute", str(script)], check=True)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.delenv("ZDOTDIR", raising=False)
    return tmp_path


def run_install(monkeypatch, *args) -> int:
    monkeypatch.setattr(sys, "argv", ["gpy", *args])
    with pytest.raises(SystemExit) as stop:
        main()
    return stop.value.code


def test_the_install_flag_is_parsed():
    assert parse_args(["--install-completions"])["install_completions"] == ""
    assert parse_args(["--install-completions=fish"])["install_completions"] == "fish"
    assert parse_args(["nb.py"])["install_completions"] is None


def test_install_takes_the_shell_from_the_environment(home, monkeypatch, capsys):
    monkeypatch.setenv("SHELL", "/bin/zsh")
    (home / ".zshrc").write_text("export A=1")
    assert run_install(monkeypatch, "--install-completions") == 0
    script = home / ".config" / "gpython" / "completions" / "gpython.zsh"
    assert script.read_text() == completions.script("zsh")
    assert (home / ".zshrc").read_text() == f'export A=1\n[ -f "{script}" ] && source "{script}"\n'
    assert "added a line to" in capsys.readouterr().out


def test_a_second_install_adds_no_second_line(home, monkeypatch, capsys):
    run_install(monkeypatch, "--install-completions=zsh")
    before = (home / ".zshrc").read_text()
    capsys.readouterr()
    assert run_install(monkeypatch, "--install-completions=zsh") == 0
    assert (home / ".zshrc").read_text() == before
    assert "loaded already" in capsys.readouterr().out


def test_zsh_follows_zdotdir(home, monkeypatch):
    monkeypatch.setenv("ZDOTDIR", str(home / "zsh"))
    run_install(monkeypatch, "--install-completions=zsh")
    assert "gpython.zsh" in (home / "zsh" / ".zshrc").read_text()
    assert not (home / ".zshrc").exists()


def test_fish_needs_no_start_up_line(home, monkeypatch, capsys):
    assert run_install(monkeypatch, "--install-completions=fish") == 0
    assert (home / ".config" / "fish" / "conf.d" / "gpython.fish").read_text() == completions.script("fish")
    assert "a line" not in capsys.readouterr().out


def test_bash_start_up_file(home, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert completions.startup_file("bash") == home / ".bashrc"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert completions.startup_file("bash") == home / ".bash_profile"
    (home / ".bashrc").write_text("")
    assert completions.startup_file("bash") == home / ".bashrc"


def test_powershell_asks_powershell_for_its_profile(home, monkeypatch):
    profile = home / "Documents" / "PowerShell" / "profile.ps1"
    monkeypatch.setattr(completions, "_powershell_startup", lambda: profile)
    done = completions.install("powershell")
    assert done.added and profile.read_text() == f"if (Test-Path '{done.script}') {{ . '{done.script}' }}\n"


def test_powershell_that_is_not_installed_says_what_to_run(home, monkeypatch, capsys):
    monkeypatch.setattr(shutil, "which", lambda program: None)
    assert run_install(monkeypatch, "--install-completions=powershell") == 1
    assert "$PROFILE" in capsys.readouterr().err


def test_an_unknown_shell_is_not_installed(home, monkeypatch, capsys):
    monkeypatch.setenv("SHELL", "/bin/tcsh")
    assert run_install(monkeypatch, "--install-completions") == 2
    assert "--install-completions=zsh" in capsys.readouterr().err
    assert not (home / ".config").exists()


@pytest.mark.skipif(shutil.which("zsh") is None, reason="needs zsh")
def test_a_new_zsh_loads_the_installed_completions(home, monkeypatch):
    run_install(monkeypatch, "--install-completions=zsh")
    loaded = subprocess.run(["zsh", "-c", "source ~/.zshrc; print $_comps[gpy]"], capture_output=True, text=True, check=True, env={"HOME": str(home), "PATH": "/usr/bin:/bin"})
    assert loaded.stdout.strip() == "_gpython"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_a_new_bash_loads_the_installed_completions(home, monkeypatch):
    run_install(monkeypatch, "--install-completions=bash")
    startup = completions.startup_file("bash")
    loaded = subprocess.run(["bash", "-c", f"source {startup}; complete -p gpy"], capture_output=True, text=True, check=True, env={"HOME": str(home), "PATH": "/usr/bin:/bin"})
    assert "_gpython" in loaded.stdout
