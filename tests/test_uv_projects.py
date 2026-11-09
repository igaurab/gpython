"""uv projects: the kernel runs in the project's environment through `uv run --with ipykernel`,
gpython takes `uv run`'s flags, and a package declared in an extra is offered as `:sync`."""

import asyncio
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from gpython import config, envpicker, envs, notebook, packages, parse_args
from gpython.app import GPython
from gpython.envpicker import EnvPicker
from gpython.notebook import CellSpec

from test_envs import fake_venv, option_ids

PYPROJECT = """[project]
name = "proj"
version = "0.1"
requires-python = ">=3.10"
dependencies = ["Rich>=13", "httpx"]

[project.optional-dependencies]
synthesis = ["pymupdf>=1.2", "pydantic-ai-slim[openai]>=2"]

[dependency-groups]
dev = ["pytest>=8"]
"""


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("UV_PROJECT_ENVIRONMENT", raising=False)
    monkeypatch.setattr(envs, "launch_args", ())


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(envs, "uv", lambda: "/bin/uv")
    root = tmp_path / "proj"
    root.mkdir()
    (root / "pyproject.toml").write_text(PYPROJECT)
    return root


def test_uv_flags_are_parsed_like_uv_run():
    opts = parse_args(["--extra", "synthesis", "--with=polars", "--all-groups", "--no-sync", "nb.py", "--group", "dev"])
    assert opts["uv"] == ["--extra=synthesis", "--with=polars", "--all-groups", "--no-sync", "--group=dev"]
    assert opts["paths"] == [Path("nb.py")]
    with pytest.raises(SystemExit):
        parse_args(["--extra"])


def test_a_uv_project_venv_without_ipykernel_runs_through_uv_run(project):
    fake_venv(project / ".venv", ipykernel=False)
    env, hint = envs.default_for(project / "nb.py")
    assert (env.kind, env.prefix, env.project, hint) == ("uv-project", project / ".venv", project, None)
    assert env.has_ipykernel and env.name == "proj/.venv (uv)"
    assert envs.kernel_argv(env, "C") == [
        "/bin/uv", "run", "--project", str(project), "--with", "ipykernel", "python", "-m", "ipykernel_launcher", "-f", "C",
    ]
    assert envs.kernel_env(env)["VIRTUAL_ENV"] == str(project / ".venv")


def test_a_venv_with_ipykernel_is_still_used_as_it_is(project):
    fake_venv(project / ".venv", ipykernel=True)
    env, _ = envs.default_for(project / "nb.py")
    assert env.kind == "uv" and envs.kernel_argv(env, "C")[0] == str(project / ".venv" / envs.BIN / envs.EXE)


def test_flags_choose_the_uv_project_even_over_an_ipykernel_venv(project, monkeypatch):
    fake_venv(project / ".venv", ipykernel=True)
    monkeypatch.setattr(envs, "launch_args", ("--extra=synthesis", "--with=polars"))
    env, _ = envs.default_for(project / "nb.py", remembered=str(project / ".venv" / envs.BIN / envs.EXE))
    assert env.kind == "uv-project" and env.uv_args == ("--extra=synthesis", "--with=polars")
    argv = envs.kernel_argv(env, "C")
    assert argv[argv.index("--project") + 2:argv.index("python")] == ["--extra=synthesis", "--with=polars", "--with", "ipykernel"]


def test_a_project_never_synced_is_suggested_not_built(project):
    env, hint = envs.default_for(project / "nb.py")
    assert env.kind == "builtin" and hint.kind == "uv-project"
    assert not (project / ".venv").exists()


def test_without_uv_nothing_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(envs, "uv", lambda: None)
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    fake_venv(tmp_path / ".venv", ipykernel=False)
    env, hint = envs.default_for(tmp_path / "nb.py")
    assert env.kind == "builtin" and hint.prefix == tmp_path / ".venv"


def test_a_remembered_project_venv_runs_through_uv_with_the_saved_extras(project):
    python = fake_venv(project / ".venv", ipykernel=False)
    envs.remember_sync(project, ["--extra=synthesis", "--with=x"])
    envs.remember_sync(project, ["--group=dev", "--extra=synthesis"])
    assert config.load()[f"uv:{project}"] == "--extra=synthesis --group=dev"
    env, _ = envs.default_for(project / "nb.py", remembered=str(python))
    assert env.kind == "uv-project" and env.uv_args == ("--extra=synthesis", "--group=dev")


def test_declared_finds_main_extra_and_group_like_pip_names(project):
    assert packages.declared(project, "rich") == ("main", "")
    assert packages.declared(project, "PyMuPDF") == ("extra", "synthesis")
    assert packages.declared(project, "pydantic_ai_slim") == ("extra", "synthesis")
    assert packages.declared(project, "pytest") == ("group", "dev")
    assert packages.declared(project, "numpy") is None
    assert packages.declared(project.parent, "rich") is None
    assert (packages.dependency_table(project, "synthesis"), packages.dependency_table(project, "dev")) == ("extra", "group")
    assert packages.dependency_table(project, "nope") is None


def test_sync_is_offered_only_in_the_projects_own_environment(project):
    fake_venv(project / ".venv", ipykernel=False)
    pane = SimpleNamespace(path=project / "nb.py", kernel=SimpleNamespace(env=envs.uv_project(project)))
    assert envpicker.project_sync(pane, ["pymupdf"]) == (["--extra=synthesis"], "the synthesis extra")
    assert envpicker.project_sync(pane, ["httpx", "pytest"]) == (["--group=dev"], "the dev group, the project's dependencies")
    assert envpicker.project_sync(pane, ["pymupdf", "numpy"]) is None
    pane.kernel.env = envs.builtin()
    assert envpicker.project_sync(pane, ["pymupdf"]) is None
    assert envs.sync_argv(project, ["--extra=synthesis"]) == ["/bin/uv", "sync", "--inexact", "--project", str(project), "--extra=synthesis"]


needs_uv = pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")


@needs_uv
def test_missing_package_in_an_extra_offers_sync_in_a_real_uv_kernel(tmp_path, monkeypatch):
    """A real `uv run --with ipykernel` kernel: the project's venv gets no ipykernel, the hint and
    the picker offer `uv sync --extra`, and enter runs it, then the cell again."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "pyproject.toml").write_text(PYPROJECT.replace('"pymupdf>=1.2"', '"gp-fake-pkg"').replace('"Rich>=13", "httpx"', ""))
    clean = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    subprocess.run([shutil.which("uv"), "venv", "-q", str(root / ".venv")], check=True, env=clean)
    monkeypatch.setattr(envs, "launch_args", ("--no-sync",))
    synced = []

    async def fake_sync(env, where, flags, on_line=None):
        synced.append((where, list(flags)))
        (root / "gp_fake_pkg.py").write_text("VALUE = 42\n")

    monkeypatch.setattr(envs, "sync", fake_sync)
    before = (root / "pyproject.toml").read_text()
    path = root / "nb.py"
    notebook.save(path, [CellSpec("import gp_fake_pkg\nprint(gp_fake_pkg.VALUE)")])

    async def main():
        app = GPython(path)
        async with app.run_test(size=(140, 40)) as pilot:
            await app.loaded.wait()
            await asyncio.wait_for(app.kernel.ready.wait(), 90)
            assert app.pane.kernel.env.kind == "uv-project"
            cell = app.cells()[0]
            cell.focus()
            await pilot.press("ctrl+e")
            for _ in range(4):
                await pilot.pause()
                await app.workers.wait_for_complete()
            assert "(uv)" in app.pane.kernel_label
            assert "in the synthesis extra" in cell.output_text and ":sync synthesis" in cell.output_text
            assert isinstance(app.screen, EnvPicker) and option_ids(app.screen)[0] == "mode:sync"
            await pilot.press("enter")
            for _ in range(4):
                await pilot.pause()
                await app.workers.wait_for_complete()
            assert synced == [(root, ["--extra=synthesis"])]
            assert cell.status == "ok" and "42" in cell.output_text

    asyncio.run(asyncio.wait_for(main(), 150))
    assert (root / "pyproject.toml").read_text() == before
    assert not list((root / ".venv").glob("lib/python*/site-packages/ipykernel"))


@needs_uv
def test_a_failed_sync_starts_the_kernel_on_the_environment_as_it_is(tmp_path):
    from gpython.kernel import Kernel

    root = tmp_path / "proj"
    root.mkdir()
    (root / "pyproject.toml").write_text(PYPROJECT.replace('"Rich>=13", "httpx"', f'"gone @ file://{tmp_path}/no-such-dir"'))
    clean = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    subprocess.run([shutil.which("uv"), "venv", "-q", str(root / ".venv")], check=True, env=clean)

    async def main():
        kernel = Kernel(file=root / "nb.py", env=envs.uv_project(root))
        await kernel.start()
        try:
            assert kernel.env.uv_args == ("--no-sync",)
            assert kernel.warning.startswith("uv could not sync the project") and "no-such-dir" in kernel.warning
            out = []
            await kernel.execute("print(6 * 7)", lambda kind, c: out.append(c.get("text", "")) if kind == "stream" else None)
            assert "".join(out).strip() == "42"
        finally:
            await kernel.shutdown()

    asyncio.run(asyncio.wait_for(main(), 120))
    assert not list(Path(tempfile.gettempdir()).glob(f"gpython-uv-{os.getpid()}-*.log"))
