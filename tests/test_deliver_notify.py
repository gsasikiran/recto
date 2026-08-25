import subprocess

import pytest

from recto import config
from recto.deliver import notify


@pytest.fixture
def as_macos(monkeypatch):
    monkeypatch.setattr(config, "IS_MACOS", True)
    monkeypatch.setattr(config, "IS_WINDOWS", False)


@pytest.fixture
def as_windows(monkeypatch):
    monkeypatch.setattr(config, "IS_MACOS", False)
    monkeypatch.setattr(config, "IS_WINDOWS", True)


@pytest.fixture
def as_linux(monkeypatch):
    monkeypatch.setattr(config, "IS_MACOS", False)
    monkeypatch.setattr(config, "IS_WINDOWS", False)
    monkeypatch.setattr(notify.shutil, "which", lambda name: f"/usr/bin/{name}")


def test_send_notification_noop_when_disabled(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    notify.send_notification(title="t", subtitle="s", enabled=False)
    assert called["n"] == 0


def test_send_notification_calls_osascript_on_macos(monkeypatch, as_macos):
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(subprocess, "run", fake_run)
    notify.send_notification(title="recto", subtitle="Some Paper", enabled=True)

    assert captured["args"][0] == "osascript"
    assert "Some Paper" in captured["args"][2]
    assert "recto" in captured["args"][2]


def test_send_notification_escapes_quotes_and_backslashes(monkeypatch, as_macos):
    captured = {}
    monkeypatch.setattr(subprocess, "run", lambda args, **k: captured.__setitem__("args", args))

    notify.send_notification(
        title="t", subtitle='Title with "quotes" and \\backslash', enabled=True
    )
    script = captured["args"][2]
    assert '\\"quotes\\"' in script
    assert "\\\\backslash" in script


def test_send_notification_uses_notify_send_on_linux(monkeypatch, as_linux):
    captured = {}
    monkeypatch.setattr(subprocess, "run", lambda args, **k: captured.__setitem__("args", args))

    notify.send_notification(title="recto", subtitle="Some Paper", enabled=True)

    assert captured["args"][0] == "notify-send"
    assert captured["args"][-2:] == ["recto", "Some Paper"]


def test_send_notification_skips_when_linux_has_no_notify_send(monkeypatch):
    monkeypatch.setattr(config, "IS_MACOS", False)
    monkeypatch.setattr(config, "IS_WINDOWS", False)
    monkeypatch.setattr(notify.shutil, "which", lambda name: None)
    called = {"n": 0}
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: called.__setitem__("n", called["n"] + 1))

    notify.send_notification(title="t", subtitle="s", enabled=True)

    assert called["n"] == 0


def test_send_notification_uses_powershell_on_windows(monkeypatch, as_windows):
    captured = {}
    monkeypatch.setattr(notify.shutil, "which", lambda name: None)
    monkeypatch.setattr(subprocess, "run", lambda args, **k: captured.__setitem__("args", args))

    notify.send_notification(title="recto", subtitle="O'Brien et al.", enabled=True)

    assert captured["args"][0] == "powershell"
    script = captured["args"][-1]
    assert "ShowBalloonTip" in script
    # Single quotes are doubled, not backslash-escaped, in PowerShell.
    assert "O''Brien et al." in script


def test_send_notification_swallows_subprocess_errors(monkeypatch, as_macos):
    def raise_err(*a, **k):
        raise OSError("osascript not found")

    monkeypatch.setattr(subprocess, "run", raise_err)
    notify.send_notification(title="t", subtitle="s", enabled=True)  # should not raise
