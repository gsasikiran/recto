"""Secret store backends. The macOS/Linux CLIs are stubbed rather than
invoked, and the file store is pointed at a tmp dir via RECTO_DATA_DIR, so
these run identically on every platform."""

import subprocess

import pytest

from recto import config, keychain


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("RECTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv(keychain.env_var_name(keychain.SERVICE_OPENROUTER), raising=False)


def _as_platform(monkeypatch, name):
    monkeypatch.setattr(config, "IS_MACOS", name == "macos")
    monkeypatch.setattr(config, "IS_WINDOWS", name == "windows")
    monkeypatch.setattr(config, "IS_LINUX", name == "linux")


def _no_cli(monkeypatch):
    monkeypatch.setattr(keychain.shutil, "which", lambda name: None)


def _cli(monkeypatch, available):
    monkeypatch.setattr(
        keychain.shutil, "which", lambda name: f"/usr/bin/{name}" if name == available else None
    )


def test_env_var_name():
    assert keychain.env_var_name("recto-openrouter") == "RECTO_OPENROUTER_SECRET"
    assert keychain.env_var_name(keychain.SERVICE_OUTLOOK) == "RECTO_OUTLOOK_SECRET"


def test_env_var_wins_over_every_backend(monkeypatch):
    _as_platform(monkeypatch, "linux")
    _no_cli(monkeypatch)
    keychain.set_secret(keychain.SERVICE_OPENROUTER, "api-key", "from-file")
    monkeypatch.setenv("RECTO_OPENROUTER_SECRET", "from-env")

    assert keychain.get_secret(keychain.SERVICE_OPENROUTER, "api-key") == "from-env"


def test_file_store_roundtrip_and_delete(monkeypatch):
    _as_platform(monkeypatch, "linux")
    _no_cli(monkeypatch)

    keychain.set_secret(keychain.SERVICE_IMAP, "me@example.com", "hunter2")
    assert keychain.get_secret(keychain.SERVICE_IMAP, "me@example.com") == "hunter2"

    keychain.delete_secret(keychain.SERVICE_IMAP, "me@example.com")
    assert keychain.get_secret(keychain.SERVICE_IMAP, "me@example.com") is None


def test_file_store_is_not_world_readable(monkeypatch):
    _as_platform(monkeypatch, "linux")
    _no_cli(monkeypatch)
    keychain.set_secret(keychain.SERVICE_IMAP, "me@example.com", "hunter2")

    mode = keychain._secrets_file().stat().st_mode & 0o777
    assert mode == 0o600


def test_file_store_keeps_accounts_apart(monkeypatch):
    _as_platform(monkeypatch, "linux")
    _no_cli(monkeypatch)

    keychain.set_secret(keychain.SERVICE_OUTLOOK, "a@example.com", "aaa")
    keychain.set_secret(keychain.SERVICE_OUTLOOK, "b@example.com", "bbb")

    assert keychain.get_secret(keychain.SERVICE_OUTLOOK, "a@example.com") == "aaa"
    assert keychain.get_secret(keychain.SERVICE_OUTLOOK, "b@example.com") == "bbb"


def test_file_store_survives_a_corrupt_file(monkeypatch):
    _as_platform(monkeypatch, "linux")
    _no_cli(monkeypatch)
    keychain._secrets_file().write_text("{ not json", encoding="utf-8")

    assert keychain.get_secret(keychain.SERVICE_OUTLOOK, "a@example.com") is None
    keychain.set_secret(keychain.SERVICE_OUTLOOK, "a@example.com", "aaa")
    assert keychain.get_secret(keychain.SERVICE_OUTLOOK, "a@example.com") == "aaa"


def test_macos_uses_security_cli(monkeypatch):
    _as_platform(monkeypatch, "macos")
    _cli(monkeypatch, "security")
    captured = []

    def fake_run(args, **kwargs):
        captured.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="s3cret\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    keychain.set_secret(keychain.SERVICE_OUTLOOK, "me@example.com", "s3cret")
    assert keychain.get_secret(keychain.SERVICE_OUTLOOK, "me@example.com") == "s3cret"
    assert captured[0][:2] == ["security", "add-generic-password"]
    assert captured[1][:2] == ["security", "find-generic-password"]


def test_macos_falls_back_to_file_when_security_fails(monkeypatch):
    _as_platform(monkeypatch, "macos")
    _cli(monkeypatch, "security")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **k: subprocess.CompletedProcess(args, 1, stdout="", stderr="denied"),
    )

    keychain.set_secret(keychain.SERVICE_OUTLOOK, "me@example.com", "s3cret")
    # security(1) keeps failing, so the read has to come back off the file store.
    assert keychain.get_secret(keychain.SERVICE_OUTLOOK, "me@example.com") == "s3cret"


def test_linux_uses_secret_tool_when_present(monkeypatch):
    _as_platform(monkeypatch, "linux")
    _cli(monkeypatch, "secret-tool")
    captured = []

    def fake_run(args, **kwargs):
        captured.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="s3cret\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    keychain.set_secret(keychain.SERVICE_IMAP, "me@example.com", "s3cret")
    assert keychain.get_secret(keychain.SERVICE_IMAP, "me@example.com") == "s3cret"
    assert captured[0][:2] == ["secret-tool", "store"]
    assert captured[1][:2] == ["secret-tool", "lookup"]


def test_linux_falls_back_to_file_without_a_keyring_daemon(monkeypatch):
    _as_platform(monkeypatch, "linux")
    _cli(monkeypatch, "secret-tool")
    # No D-Bus session: secret-tool is installed but every call fails.
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **k: subprocess.CompletedProcess(args, 1, stdout="", stderr="no daemon"),
    )

    keychain.set_secret(keychain.SERVICE_IMAP, "me@example.com", "s3cret")
    assert keychain.get_secret(keychain.SERVICE_IMAP, "me@example.com") == "s3cret"


def test_windows_never_shells_out_to_a_unix_keyring(monkeypatch):
    _as_platform(monkeypatch, "windows")
    monkeypatch.setattr(keychain.shutil, "which", lambda name: f"C:\\bin\\{name}.exe")

    def explode(*a, **k):
        raise AssertionError("no subprocess on the Windows path")

    monkeypatch.setattr(subprocess, "run", explode)
    # DPAPI is unavailable off Windows; _dpapi returning None makes the store
    # fall through to an unencrypted payload, which is what we can assert here.
    monkeypatch.setattr(keychain, "_dpapi", lambda protect, data: None)
    monkeypatch.setattr(keychain.os, "chmod", lambda *a, **k: None)

    keychain.set_secret(keychain.SERVICE_OPENROUTER, "api-key", "k")
    assert keychain.get_secret(keychain.SERVICE_OPENROUTER, "api-key") == "k"


def test_windows_roundtrips_through_dpapi(monkeypatch):
    _as_platform(monkeypatch, "windows")
    _no_cli(monkeypatch)
    calls = []

    def fake_dpapi(protect, data):
        calls.append(protect)
        return data[::-1]  # stand-in for encrypt/decrypt

    monkeypatch.setattr(keychain, "_dpapi", fake_dpapi)

    keychain.set_secret(keychain.SERVICE_OPENROUTER, "api-key", "sk-abc")
    stored = keychain._read_store()["recto-openrouter\napi-key"]
    assert stored["encoding"] == "dpapi"
    assert keychain.get_secret(keychain.SERVICE_OPENROUTER, "api-key") == "sk-abc"
    assert calls == [True, False]


def test_backend_name_reports_the_store_in_use(monkeypatch):
    _as_platform(monkeypatch, "macos")
    _cli(monkeypatch, "security")
    assert keychain.backend_name() == "macOS Keychain"

    _as_platform(monkeypatch, "windows")
    _no_cli(monkeypatch)
    assert keychain.backend_name() == "DPAPI-encrypted file"

    _as_platform(monkeypatch, "linux")
    _cli(monkeypatch, "secret-tool")
    assert keychain.backend_name() == "Secret Service (secret-tool)"

    _no_cli(monkeypatch)
    assert keychain.backend_name() == "plaintext file"
