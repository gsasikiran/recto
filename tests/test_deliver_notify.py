import subprocess

from recto.deliver import notify


def test_send_notification_noop_when_disabled(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    notify.send_notification(title="t", subtitle="s", enabled=False)
    assert called["n"] == 0


def test_send_notification_calls_osascript_when_enabled(monkeypatch):
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(subprocess, "run", fake_run)
    notify.send_notification(title="recto", subtitle="Some Paper", enabled=True)

    assert captured["args"][0] == "osascript"
    assert "Some Paper" in captured["args"][2]
    assert "recto" in captured["args"][2]


def test_send_notification_escapes_quotes_and_backslashes(monkeypatch):
    captured = {}
    monkeypatch.setattr(subprocess, "run", lambda args, **k: captured.__setitem__("args", args))

    notify.send_notification(
        title="t", subtitle='Title with "quotes" and \\backslash', enabled=True
    )
    script = captured["args"][2]
    assert '\\"quotes\\"' in script
    assert "\\\\backslash" in script


def test_send_notification_swallows_subprocess_errors(monkeypatch):
    def raise_err(*a, **k):
        raise OSError("osascript not found")

    monkeypatch.setattr(subprocess, "run", raise_err)
    notify.send_notification(title="t", subtitle="s", enabled=True)  # should not raise
