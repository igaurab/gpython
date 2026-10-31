"""Environments: discovery, defaults, uv/venv creation, kernel switching and the env picker."""

import asyncio
import os
import sys
import sysconfig
from pathlib import Path

import pytest
from rich.console import Console
from textual.widgets import Input, OptionList

from gpython import config, envpicker, envs
from gpython.app import GPython
from gpython.envpicker import EnvPicker
from gpython.kernel import Kernel
from gpython.vim import VimTextArea

needs_uv = pytest.mark.skipif(envs.uv() is None, reason="uv is not installed")


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    VimTextArea.enabled = True
    envpicker._cache.clear()


def fake_venv(folder: Path, uv: bool = True, version: str = "3.12.4", ipykernel: bool = False) -> Path:
    """A venv in seconds: pyvenv.cfg + bin/python; with ipykernel, it borrows gpython's site-packages."""
    (folder / envs.BIN).mkdir(parents=True)
    python = folder / envs.BIN / envs.EXE
    python.symlink_to(sys.executable)
    cfg = f"home = {Path(sys.base_prefix) / envs.BIN}\nversion_info = {version}\n" + ("uv = 0.9.0\n" if uv else "")
    (folder / "pyvenv.cfg").write_text(cfg)
    if ipykernel:
        lib = folder / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}"
        lib.mkdir(parents=True)
        (lib / "site-packages").symlink_to(sysconfig.get_path("purelib"))
    return python


def test_venv_at_reads_kind_and_version(tmp_path):
    fake_venv(tmp_path / "a", uv=True, version="3.12.4")
    fake_venv(tmp_path / "b", uv=False, version="3.11.2", ipykernel=True)
    a, b = envs.venv_at(tmp_path / "a"), envs.venv_at(tmp_path / "b")
    assert (a.kind, a.version, a.has_ipykernel) == ("uv", "3.12.4", False)
    assert (b.kind, b.version, b.has_ipykernel) == ("venv", "3.11.2", True)
    assert envs.venv_at(tmp_path) is None
    assert a.short == "Python 3.12 · a"


def test_discover_puts_project_venvs_first_nearest_first(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    sub = tmp_path / "notebooks"
    sub.mkdir()
    fake_venv(tmp_path / ".venv")
    fake_venv(sub / "venv")
    found = envs.discover(sub)
    assert [e.prefix for e in found[:2]] == [sub / "venv", tmp_path / ".venv"]
    assert found[1].name == f"{tmp_path.name}/.venv"
    assert any(e.kind == "builtin" or e.prefix == Path(sys.prefix) for e in found)
    assert len({str(e.prefix or e.python) for e in found}) == len(found)


def test_default_for_prefers_remembered_then_ipykernel_venv(tmp_path):
    nb = tmp_path / "nb.py"
    (tmp_path / ".git").mkdir()
    fake_venv(tmp_path / ".venv", ipykernel=False)
    env, hint = envs.default_for(nb)
    assert env.kind == "builtin" and hint.prefix == tmp_path / ".venv"

    fake_venv(tmp_path / "env", ipykernel=True)
    env, hint = envs.default_for(nb)
    assert env.prefix == tmp_path / "env" and hint is None

    other = fake_venv(tmp_path / "other")
    env, _ = envs.default_for(nb, remembered=str(other))
    assert env.prefix == tmp_path / "other"
    env, _ = envs.default_for(nb, remembered=str(tmp_path / "gone" / "python"))
    assert env.prefix == tmp_path / "env"


def test_kernel_env_points_path_and_virtual_env_at_the_env(tmp_path, monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", "/elsewhere/.venv")
    monkeypatch.setenv("PATH", f"/elsewhere/.venv/{envs.BIN}{os.pathsep}/usr/bin")
    fake_venv(tmp_path / "v")
    out = envs.kernel_env(envs.venv_at(tmp_path / "v"))
    assert out["VIRTUAL_ENV"] == str(tmp_path / "v")
    assert out["PATH"].split(os.pathsep) == [str(tmp_path / "v" / envs.BIN), "/usr/bin"]
    bare = envs.kernel_env(envs.Env(Path("/usr/bin/python3"), "python3", "system"))
    assert "VIRTUAL_ENV" not in bare and "/elsewhere" not in bare["PATH"]


def test_run_streams_lines_and_exit_code():
    lines = []
    code = asyncio.run(envs.run([sys.executable, "-c", "print('a'); print('b\\rc', end='')"], lines.append))
    assert code == 0 and lines == ["a", "b", "c"]
    assert asyncio.run(envs.run(["/no/such/binary"])) == 127


@needs_uv
def test_uv_env_kernel_switch_and_magics(tmp_path):
    async def main():
        lines = []
        env = await envs.create(tmp_path / ".venv", "uv", on_line=lines.append)
        assert env.kind == "uv" and env.has_ipykernel
        k = Kernel(file=tmp_path / "nb.py", env=envs.builtin())
        await k.start()
        try:
            out = []
            grab = lambda kind, c: out.append(c.get("text", "")) if kind == "stream" else None
            await k.switch(env)
            await k.execute("import sys; print(sys.prefix)", grab)
            assert Path("".join(out).strip()) == tmp_path / ".venv"
            out.clear()
            assert await k.execute("%uv pip list", grab)
            assert "ipykernel" in "".join(out)
            await k.switch(envs.builtin())
            out.clear()
            await k.execute("import sys; print(sys.prefix)", grab)
            assert Path("".join(out).strip()) == Path(sys.prefix)
        finally:
            await k.shutdown()

    asyncio.run(main())


def test_python_m_venv_env_end_to_end(tmp_path):
    """No uv: `python -m venv`, pip installs ipykernel, the kernel runs there (~10 s)."""

    async def main():
        env = await envs.create(tmp_path / "plain", "venv")
        assert env.kind == "venv" and env.has_ipykernel
        k = Kernel(env=env)
        await k.start()
        try:
            out = []
            assert await k.execute("import sys; print(sys.prefix)", lambda kind, c: out.append(c.get("text", "")))
            assert Path("".join(out).strip()) == tmp_path / "plain"
        finally:
            await k.shutdown()

    asyncio.run(main())


def test_switch_while_a_cell_runs():
    async def main():
        k = Kernel()
        await k.start()
        try:
            running = asyncio.create_task(k.execute("import time; time.sleep(30)"))
            await asyncio.sleep(0.5)
            await asyncio.wait_for(k.switch(envs.builtin()), 30)
            assert await asyncio.wait_for(running, 5) is False
            out = []
            assert await k.execute("print('ok')", lambda kind, c: out.append(c.get("text", "")))
            assert "ok" in "".join(out)
        finally:
            await k.shutdown()

    asyncio.run(main())


def test_failed_switch_falls_back_to_the_old_kernel(tmp_path):
    async def main():
        k = Kernel()
        await k.start()
        try:
            broken = envs.Env(Path("/bin/false"), "false", "system")
            with pytest.raises(Exception):
                await k.switch(broken)
            assert k.env.kind == "builtin"
            out = []
            assert await k.execute("print(1 + 1)", lambda kind, c: out.append(c.get("text", "")))
            assert "2" in "".join(out)
        finally:
            await k.shutdown()

    asyncio.run(main())


def ui(tmp_path, test):
    path = tmp_path / "nb.py"
    path.write_text("# %%\na = 1\n")

    async def main():
        app = GPython(path)
        async with app.run_test(size=(120, 40)) as pilot:
            await app.loaded.wait()
            await asyncio.wait_for(app.kernel.ready.wait(), 60)
            await pilot.pause()
            await test(app, pilot)

    asyncio.run(main())


def title(screen) -> str:
    return str(screen.query_one("#env-title").render())


def plain(renderable) -> str:
    console = Console(width=120, color_system=None)
    with console.capture() as capture:
        console.print(renderable)
    return capture.get().strip()


def option_ids(screen):
    options = screen.query_one(OptionList)
    return [options.get_option_at_index(i).id for i in range(options.option_count)]


def test_f6_picker_lists_actions_and_envs_then_switches(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    fake_venv(tmp_path / ".venv", ipykernel=True)
    switched = []

    async def fake_switch(env):
        switched.append(env)

    async def test(app, pilot):
        assert app.pane.kernel.env.prefix == tmp_path / ".venv"
        chip = str(app.query_one("#kernel").render())
        assert chip.startswith("Python 3") and ".venv" in chip
        monkeypatch.setattr(app.pane, "switch_env", fake_switch)

        await pilot.press("f6")
        await pilot.pause(0.3)
        screen = app.screen
        assert isinstance(screen, EnvPicker)
        ids = option_ids(screen)
        assert "act:new-venv" in ids and "act:install" in ids and "act:restart" in ids
        assert any(i and i.startswith("env:") for i in ids)
        await pilot.press(*"built-in")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause(0.3)
        assert not isinstance(app.screen, EnvPicker)
        assert switched and switched[0].kind == "builtin"

    ui(tmp_path, test)


def test_install_from_picker_and_pip_command(tmp_path, monkeypatch):
    calls = []

    async def fake_install(env, packages, on_line=None, tool="uv"):
        on_line and on_line("Resolved 1 package")
        calls.append(packages)

    monkeypatch.setattr(envs, "install", fake_install)

    async def test(app, pilot):
        await pilot.press("f6")
        await pilot.pause(0.2)
        screen = app.screen
        ids = option_ids(screen)
        screen.query_one(OptionList).highlighted = ids.index("act:install")
        await pilot.press("enter")
        await pilot.pause()
        assert screen.query_one(Input).placeholder.startswith("package names")
        await pilot.press(*"rich tqdm")
        await pilot.press("enter")
        await pilot.pause(0.5)
        assert calls == [["rich", "tqdm"]]
        assert app.pane.kernel_state == "kernel ready"

        await app._run_ex("pip install numpy")
        await pilot.pause(0.5)
        assert calls[-1] == ["numpy"]

    ui(tmp_path, test)


def test_env_switch_is_remembered_per_notebook(tmp_path):
    fake = fake_venv(tmp_path / "custom")

    async def test(app, pilot):
        await app.pane.switch_env(envs.builtin())
        assert config.load()[f"env:{(tmp_path / 'nb.py').resolve()}"] == sys.executable
        assert app.pane.kernel_state == "kernel ready"

    ui(tmp_path, test)
    config.save({f"env:{(tmp_path / 'nb.py').resolve()}": str(fake)})
    env, _ = envs.default_for(tmp_path / "nb.py", config.load()[f"env:{(tmp_path / 'nb.py').resolve()}"])
    assert env.prefix == tmp_path / "custom"


def test_explain_turns_uv_and_pip_output_into_sentences():
    uv_out = [
        "  × No solution found when resolving dependencies:",
        "  ╰─▶ Because numpyyy was not found in the package registry and you require",
        "      numpyyy, we can conclude that your requirements are unsatisfiable.",
    ]
    assert envs.explain(uv_out) == [
        "No solution found when resolving dependencies:",
        "Because numpyyy was not found in the package registry and you require numpyyy, we can conclude that your requirements are unsatisfiable.",
    ]
    pip_out = ["Collecting numpyyy", "ERROR: No matching distribution found for numpyyy"]
    assert envs.explain(pip_out) == ["ERROR: No matching distribution found for numpyyy"]
    assert envs.explain(["Resolved 1 package", "", "Prepared 1 package"]) == ["Resolved 1 package", "Prepared 1 package"]


def test_env_error_names_the_command_exit_code_and_output():
    code = "import sys; print('  × boom:'); print('  ╰─▶ because of'); print('      reasons'); sys.exit(3)"
    with pytest.raises(envs.EnvError) as info:
        asyncio.run(envs._check([sys.executable, "-c", code], None))
    text = str(info.value)
    assert text.splitlines()[0].endswith("(exit 3)") and text.splitlines()[0].startswith(sys.executable.replace(str(Path.home()), "~"))
    assert text.splitlines()[1:] == ["boom:", "because of reasons"]


@needs_uv
def test_install_prompt_offers_uv_add_in_a_project_and_remembers_the_choice(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    fake_venv(tmp_path / ".venv", ipykernel=True)
    calls = []

    async def fake_add(env, packages, start, on_line=None):
        calls.append(("add", list(packages)))
        return True

    async def fake_install(env, packages, on_line=None, tool="uv"):
        calls.append(("install", list(packages)))

    monkeypatch.setattr(envs, "add", fake_add)
    monkeypatch.setattr(envs, "install", fake_install)

    async def test(app, pilot):
        assert app.pane.kernel.env.prefix == tmp_path / ".venv"
        await app._run_ex("pip")
        await pilot.pause(0.2)
        screen = app.screen
        assert isinstance(screen, EnvPicker)
        ids = option_ids(screen)
        assert ids[:2] == ["mode:add", "mode:install"]
        options = screen.query_one(OptionList)
        assert options.highlighted == 1
        assert "enter: uv pip install …" in title(screen) and "↑↓ switch" in title(screen)
        assert plain(options.get_option_at_index(1).prompt).startswith("●")
        assert not plain(options.get_option_at_index(0).prompt).startswith("●")
        await pilot.press(*"rich", "enter")
        await pilot.pause(0.5)
        assert calls == [("install", ["rich"])]
        assert config.load()["install_mode"] == "install"

        await app._run_ex("pip")
        await pilot.pause(0.2)
        options = app.screen.query_one(OptionList)
        assert options.highlighted == 1
        await pilot.press("up")
        await pilot.press(*"httpx")
        await pilot.pause()
        assert options.highlighted == 0
        assert "enter: uv add httpx → " in title(app.screen) and "pyproject.toml" in title(app.screen)
        assert plain(options.get_option_at_index(0).prompt).startswith("●")
        await pilot.press("enter")
        await pilot.pause(0.5)
        assert calls[-1] == ("add", ["httpx"])
        assert config.load()["install_mode"] == "add"

        await app._run_ex("pip")
        await pilot.pause(0.2)
        assert app.screen.query_one(OptionList).highlighted == 0
        await pilot.press("escape")
        await pilot.pause()

    ui(tmp_path, test)
