import pytest


@pytest.fixture(autouse=True)
def _never_touch_the_real_config(tmp_path_factory, monkeypatch):
    """Every test gets its own config folder: themes and envs chosen in tests must not
    end up in ~/.config/gpython/config.toml (test_repaint once saved "nord" there)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg")))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path_factory.mktemp("cache")))
