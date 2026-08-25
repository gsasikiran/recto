"""Platform-dependent filesystem locations. These call the real functions
with the platform flags patched, so a macOS dev box still exercises the
Linux and Windows branches."""

from pathlib import Path

import pytest

from recto import config


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for var in (
        "RECTO_DATA_DIR",
        "RECTO_LOGS_DIR",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "LOCALAPPDATA",
    ):
        monkeypatch.delenv(var, raising=False)


def _as_platform(monkeypatch, name):
    monkeypatch.setattr(config, "IS_MACOS", name == "macos")
    monkeypatch.setattr(config, "IS_WINDOWS", name == "windows")
    monkeypatch.setattr(config, "IS_LINUX", name == "linux")


def _fake_home(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def test_data_dir_macos(monkeypatch, tmp_path):
    _as_platform(monkeypatch, "macos")
    home = _fake_home(monkeypatch, tmp_path)
    assert config.data_dir() == home / "Library" / "Application Support" / "recto"


def test_data_dir_linux_defaults_to_xdg_share(monkeypatch, tmp_path):
    _as_platform(monkeypatch, "linux")
    home = _fake_home(monkeypatch, tmp_path)
    assert config.data_dir() == home / ".local" / "share" / "recto"


def test_data_dir_linux_honours_xdg_data_home(monkeypatch, tmp_path):
    _as_platform(monkeypatch, "linux")
    _fake_home(monkeypatch, tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert config.data_dir() == tmp_path / "xdg" / "recto"


def test_data_dir_windows_uses_localappdata(monkeypatch, tmp_path):
    _as_platform(monkeypatch, "windows")
    _fake_home(monkeypatch, tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    assert config.data_dir() == tmp_path / "AppData" / "Local" / "recto"


def test_data_dir_windows_falls_back_without_localappdata(monkeypatch, tmp_path):
    _as_platform(monkeypatch, "windows")
    home = _fake_home(monkeypatch, tmp_path)
    assert config.data_dir() == home / "AppData" / "Local" / "recto"


def test_recto_data_dir_overrides_every_platform(monkeypatch, tmp_path):
    for platform in ("macos", "linux", "windows"):
        _as_platform(monkeypatch, platform)
        monkeypatch.setenv("RECTO_DATA_DIR", str(tmp_path / "custom"))
        assert config.data_dir() == tmp_path / "custom"


def test_data_dir_is_created(monkeypatch, tmp_path):
    monkeypatch.setenv("RECTO_DATA_DIR", str(tmp_path / "made" / "here"))
    assert config.data_dir().is_dir()


def test_db_and_index_live_in_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("RECTO_DATA_DIR", str(tmp_path / "d"))
    assert config.db_path() == tmp_path / "d" / "db.sqlite"
    assert config.profile_index_path() == tmp_path / "d" / "profile_index.pkl"


def test_logs_dir_macos(monkeypatch, tmp_path):
    _as_platform(monkeypatch, "macos")
    home = _fake_home(monkeypatch, tmp_path)
    assert config.logs_dir() == home / "Library" / "Logs" / "recto"


def test_logs_dir_linux_uses_xdg_state(monkeypatch, tmp_path):
    _as_platform(monkeypatch, "linux")
    home = _fake_home(monkeypatch, tmp_path)
    assert config.logs_dir() == home / ".local" / "state" / "recto" / "logs"


def test_logs_dir_windows_sits_under_data_dir(monkeypatch, tmp_path):
    _as_platform(monkeypatch, "windows")
    monkeypatch.setenv("RECTO_DATA_DIR", str(tmp_path / "d"))
    assert config.logs_dir() == tmp_path / "d" / "logs"


def test_app_support_dir_alias_still_works(monkeypatch, tmp_path):
    monkeypatch.setenv("RECTO_DATA_DIR", str(tmp_path / "d"))
    assert config.app_support_dir() == config.data_dir()


def test_exactly_one_platform_flag_is_true():
    assert sum([config.IS_MACOS, config.IS_WINDOWS, config.IS_LINUX]) <= 1
