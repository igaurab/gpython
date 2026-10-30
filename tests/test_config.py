import pytest

from gpython import config


@pytest.fixture(autouse=True)
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def path(xdg):
    return xdg / "gpython" / "config.toml"


def test_load_missing_is_empty():
    assert config.load() == {}


def test_roundtrip(xdg):
    values = {"theme": 'gruvbox "light"\\x', "vim": True, "width": 3}
    config.save(values)
    assert path(xdg).exists()
    assert config.load() == values
    assert not list(path(xdg).parent.glob("*.tmp"))


def test_invalid_toml_is_empty(xdg):
    path(xdg).parent.mkdir(parents=True)
    path(xdg).write_text("theme = = [")
    assert config.load() == {}


def test_save_merges(xdg):
    config.save({"theme": "nord", "vim": False})
    config.save({"theme": "dracula"})
    assert config.load() == {"theme": "dracula", "vim": False}


def test_save_ignores_unsupported_values(xdg):
    config.save({"theme": "nord", "bad": [1, 2], "none": None})
    assert config.load() == {"theme": "nord"}
