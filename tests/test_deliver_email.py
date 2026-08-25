import smtplib

import pytest

from recto import config
from recto.deliver import email as deliver_email


class _FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.calls = []
        _FakeSMTP.instances.append(self)

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, username, password):
        self.calls.append(("login", username, password))

    def send_message(self, msg):
        self.calls.append(("send_message", msg))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def fake_smtp(monkeypatch, request):
    _FakeSMTP.instances.clear()
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    # Avoid shelling out to `security` (real Keychain trust store) in tests,
    # except in the tests that specifically exercise that function.
    if "trust_context" not in request.node.name:
        monkeypatch.setattr(deliver_email, "_trust_context", lambda: "fake-context")
    yield _FakeSMTP


def test_send_email_happy_path_calls_starttls_login_send():
    deliver_email.send_email(
        subject="Subj",
        html_body="<p>hi</p>",
        plain_body="hi",
        smtp_host="smtp.office365.com",
        smtp_port=587,
        use_starttls=True,
        from_addr="me@example.com",
        to_addr="me@example.com",
        username="me@example.com",
        password="secret",
    )
    inst = _FakeSMTP.instances[0]
    assert inst.host == "smtp.office365.com"
    assert inst.calls[0] == "starttls"
    assert inst.calls[1] == ("login", "me@example.com", "secret")
    assert inst.calls[2][0] == "send_message"
    msg = inst.calls[2][1]
    assert msg["Subject"] == "Subj"


def test_send_email_skips_starttls_when_disabled():
    deliver_email.send_email(
        subject="Subj",
        html_body="<p>hi</p>",
        plain_body="hi",
        smtp_host="smtp.example.com",
        smtp_port=25,
        use_starttls=False,
        from_addr="me@example.com",
        to_addr="me@example.com",
        username="me@example.com",
        password="secret",
    )
    inst = _FakeSMTP.instances[0]
    assert "starttls" not in inst.calls


def test_send_email_raises_when_no_password():
    with pytest.raises(RuntimeError, match="no SMTP password"):
        deliver_email.send_email(
            subject="Subj",
            html_body="<p>hi</p>",
            plain_body="hi",
            smtp_host="smtp.example.com",
            smtp_port=587,
            use_starttls=True,
            from_addr="me@example.com",
            to_addr="me@example.com",
            username="me@example.com",
            password=None,
        )
    assert _FakeSMTP.instances == []


def test_macos_trust_context_uses_keychain_cadata(monkeypatch):
    import subprocess

    monkeypatch.setattr(config, "IS_MACOS", True)
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args

        class Result:
            stdout = "-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----\n"

        return Result()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("ssl.create_default_context", lambda cadata=None: ("context", cadata))

    ctx = deliver_email._trust_context()
    assert "SystemRootCertificates.keychain" in captured["args"][-1]
    assert ctx[1] is not None


def test_macos_trust_context_falls_back_on_subprocess_failure(monkeypatch):
    import subprocess

    monkeypatch.setattr(config, "IS_MACOS", True)

    def fake_run(args, **kwargs):
        raise FileNotFoundError("security not found")

    monkeypatch.setattr(subprocess, "run", fake_run)

    ctx = deliver_email._trust_context()
    assert ctx is not None  # falls back to ssl.create_default_context()


def test_trust_context_off_macos_uses_stdlib_default(monkeypatch):
    """Linux and Windows have no keychain to consult; shelling out to
    `security` there would just fail on every send."""
    import subprocess

    monkeypatch.setattr(config, "IS_MACOS", False)

    def explode(*a, **k):
        raise AssertionError("must not shell out off macOS")

    monkeypatch.setattr(subprocess, "run", explode)
    monkeypatch.setattr("ssl.create_default_context", lambda cadata=None: ("context", cadata))

    assert deliver_email._trust_context() == ("context", None)
