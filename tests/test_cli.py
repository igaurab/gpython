import subprocess
import sys
from importlib.metadata import version

import pytest

from gpython import main, parse_args


@pytest.mark.parametrize("flag", ["-v", "-V", "--version"])
def test_version_flag_is_parsed(flag):
    assert parse_args([flag])["version"] is True


def test_version_prints_installed_package_version(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["gpy", "--version"])
    main()
    assert capsys.readouterr().out.strip() == f"gpython {version('gpynb')}"


def test_version_does_not_import_the_app():
    code = "import sys, gpython; sys.argv = ['gpy', '--version']; gpython.main(); print('gpython.app' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.splitlines()[-1] == "False"


@pytest.mark.parametrize("command", ["version", "ver"])
def test_version_ex_command_notifies_the_version(tmp_path, command):
    import asyncio

    from gpython.app import GPython

    path = tmp_path / "nb.py"
    path.write_text("# %%\na = 1\n")

    async def scenario():
        app = GPython(path)
        async with app.run_test(size=(120, 40)) as pilot:
            await app._run_ex(command)
            await pilot.pause()
            assert any(n.message == f"gpython {version('gpynb')}" for n in app._notifications)

    asyncio.run(scenario())


def test_groups_prints_the_groups_without_the_app(tmp_path):
    nb = tmp_path / "nb.py"
    nb.write_text("# %%\nimport math\n# %%\na = math.pi\n# %%\nb = math.e\n# %%\nc = a + b\n")
    code = f"import sys, gpython; sys.argv = ['gpy', '--groups', {str(nb)!r}]\ntry:\n    gpython.main()\nexcept SystemExit as e:\n    print('exit', e.code)\nprint('gpython.app' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert "group 2: c2 c3   together" in out
    assert "group 3: c4" in out
    assert out.splitlines()[-2:] == ["exit 0", "False"]


def test_groups_needs_a_file(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["gpy", "--groups"])
    with pytest.raises(SystemExit) as stop:
        main()
    assert stop.value.code == 2
