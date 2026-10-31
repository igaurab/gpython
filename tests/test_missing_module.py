"""A cell that fails with ModuleNotFoundError offers to install the package and runs again, like marimo."""

import asyncio

import pytest
from textual.widgets import Input

from gpython import config, envs, notebook
from gpython.app import GPython
from gpython.envpicker import EnvPicker
from gpython.notebook import CellSpec


def ui(tmp_path, cells, test):
    path = tmp_path / "nb.py"
    notebook.save(path, cells)

    async def main():
        app = GPython(path)
        async with app.run_test(size=(120, 40)) as pilot:
            await app.loaded.wait()
            await asyncio.wait_for(app.kernel.ready.wait(), 60)
            await pilot.pause()
            await test(app, pilot)

    asyncio.run(asyncio.wait_for(main(), 120))


async def settle(app, pilot):
    """The run worker starts the install worker, which runs cells again: wait for the whole chain."""
    for _ in range(4):
        await pilot.pause()
        await app.workers.wait_for_complete()
    await pilot.pause()


def prompt(app) -> str | None:
    """The install prompt's prefilled packages, or None when no prompt is open."""
    return app.screen.query_one(Input).value if isinstance(app.screen, EnvPicker) else None


@pytest.fixture
def fake_install(tmp_path, monkeypatch):
    """An envs.install that 'installs' gp_fake_pkg by writing it next to the notebook (on the kernel's path)."""
    calls = []

    async def install(env, packages, on_line=None, tool="uv"):
        calls.append(list(packages))
        if on_line:
            on_line("Installed 1 package")
        if "gp_fake_pkg" in packages:
            (tmp_path / "gp_fake_pkg.py").write_text("VALUE = 42\n")

    monkeypatch.setattr(envs, "install", install)
    return calls


def test_missing_module_asks_then_installs_and_runs_the_cell_again(tmp_path, fake_install):
    async def test(app, pilot):
        assert app.autoinstall is False
        cell = app.cells()[0]
        cell.focus()
        await pilot.press("ctrl+e")
        await settle(app, pilot)
        assert cell.status == "error" and cell.missing == "gp_fake_pkg"
        assert "gp_fake_pkg is not installed" in cell.output_text and ":pip gp_fake_pkg" in cell.output_text
        assert prompt(app) == "gp_fake_pkg" and fake_install == []
        await pilot.press("enter")
        await settle(app, pilot)
        assert fake_install == [["gp_fake_pkg"]]
        assert cell.status == "ok" and "42" in cell.output_text
        assert cell.missing is None and prompt(app) is None
        assert app.pane.kernel_state == "kernel ready"

    ui(tmp_path, [CellSpec("import gp_fake_pkg\nprint(gp_fake_pkg.VALUE)")], test)


def test_every_run_asks_again_but_the_rerun_after_an_install_does_not(tmp_path, fake_install):
    async def test(app, pilot):
        a, b = app.cells()
        await pilot.press("f5")
        await settle(app, pilot)
        assert prompt(app) == "gp_fake_pkg"
        assert (a.status, b.status) == ("error", "error") and (a.missing, b.missing) == ("gp_fake_pkg", None)
        await pilot.press("escape")
        await pilot.pause()
        assert prompt(app) is None

        await pilot.press("f5")
        await settle(app, pilot)
        assert prompt(app) == "gp_fake_pkg"
        await pilot.press("enter")
        await settle(app, pilot)
        assert fake_install == [["gp_fake_pkg"]]
        assert a.status == "error" and a.missing == "yaml"
        assert prompt(app) == "pyyaml"
        await pilot.press("escape")
        await pilot.pause()

        app.pane.select(a, "cell")
        await pilot.pause()
        await app._run_ex("pip")
        await pilot.pause()
        assert prompt(app) == "pyyaml"
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("f5")
        await settle(app, pilot)
        assert prompt(app) == "pyyaml"
        await pilot.press("enter")
        await settle(app, pilot)
        assert fake_install == [["gp_fake_pkg"], ["pyyaml"]]
        assert a.status == "error" and a.missing == "yaml" and prompt(app) is None

        await pilot.press("f5")
        await settle(app, pilot)
        assert prompt(app) == "pyyaml"
        await pilot.press("escape")
        await pilot.pause()

    ui(tmp_path, [CellSpec("import gp_fake_pkg\nimport yaml"), CellSpec("1/0")], test)


def test_autoinstall_installs_without_asking_and_asks_after_a_failed_install(tmp_path, monkeypatch):
    calls = []

    async def failing(env, packages, on_line=None, tool="uv"):
        calls.append(list(packages))
        raise envs.EnvError("No solution found when resolving dependencies")

    monkeypatch.setattr(envs, "install", failing)

    async def test(app, pilot):
        await app._run_ex("set autoinstall")
        assert config.load()["autoinstall"] is True and app.autoinstall is True
        cell = app.cells()[0]
        cell.focus()
        await pilot.press("ctrl+e")
        await settle(app, pilot)
        assert calls == [["opencv-python"]] and prompt(app) is None
        assert cell.status == "error" and cell.missing == "cv2"
        assert ":pip opencv-python" in cell.output_text
        assert app.pane.kernel_state == "kernel ready"
        await pilot.press("ctrl+e")
        await settle(app, pilot)
        assert calls == [["opencv-python"]] and prompt(app) == "opencv-python"
        await pilot.press("escape")
        await pilot.pause()
        await app._run_ex("set noautoinstall")
        assert config.load()["autoinstall"] is False and app.autoinstall is False

    ui(tmp_path, [CellSpec("import cv2")], test)
