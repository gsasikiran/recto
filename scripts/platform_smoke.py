"""Run the platform-specific code paths against the real OS.

The pytest suite patches `config.IS_*` so every OS branch is exercised from
one machine — which proves the branching, not that the OS accepts what the
branch produces. This script takes the other half: no mocks, no patched
flags, real directories, the real secret store, and the OS's own validator
for the scheduler artifact (`plutil` / `systemd-analyze` / `schtasks`).

It is what CI runs on macOS, Linux, and Windows. Offline by design — no
network, no config.toml, no LLM. Run it directly:

    uv run python scripts/platform_smoke.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from recto import config, keychain, scheduler
from recto.deliver import notify
from recto.models import Paper
from recto.render import render_markdown, write_digest
from recto.store import Store

# Deliberately not one of the real SERVICE_* names: this writes to the actual
# secret store, and must not collide with a working install's credentials.
SMOKE_SERVICE = "recto-smoketest"

# Non-ASCII on purpose. A cp1252 default encoding on Windows dies here.
UNICODE_TITLE = "Modèles d'attention — Müller & Søren, 北京 (2024)"

failures: list[str] = []


def check(name: str, fn) -> None:
    try:
        detail = fn()
    except Exception as exc:  # noqa: BLE001 - a smoke run reports, never crashes
        failures.append(f"{name}: {type(exc).__name__}: {exc}")
        print(f"FAIL  {name}\n      {type(exc).__name__}: {exc}")
        return
    print(f"ok    {name}" + (f"  ({detail})" if detail else ""))


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=120)


# --- checks --------------------------------------------------------------


def check_platform_flags() -> str:
    flags = [config.IS_MACOS, config.IS_WINDOWS, config.IS_LINUX]
    assert sum(flags) == 1, f"expected exactly one platform flag, got {flags}"
    return sys.platform


def check_directories() -> str:
    data, logs = config.data_dir(), config.logs_dir()
    for d in (data, logs):
        assert d.is_dir(), f"{d} was not created"
        probe = d / "_smoke_probe.txt"
        probe.write_text(UNICODE_TITLE, encoding="utf-8")
        assert probe.read_text(encoding="utf-8") == UNICODE_TITLE, f"{d} lost UTF-8 content"
        probe.unlink()
    return f"data={data}"


def check_secret_store() -> str:
    """The real backend for this OS — on Windows this is the only place the
    DPAPI ctypes code actually executes."""
    account = "smoke@example.com"
    secret = "p@ss wörd \"quoted\" 'single'"
    try:
        keychain.set_secret(SMOKE_SERVICE, account, secret)
        got = keychain.get_secret(SMOKE_SERVICE, account)
        assert got == secret, f"roundtrip mismatch: {got!r} != {secret!r}"

        if config.IS_WINDOWS:
            entry = keychain._read_store()[f"{SMOKE_SERVICE}\n{account}"]
            assert entry["encoding"] == "dpapi", (
                "secret was stored unencrypted — CryptProtectData failed, "
                "check the ctypes call in keychain._dpapi"
            )

        keychain.delete_secret(SMOKE_SERVICE, account)
        assert keychain.get_secret(SMOKE_SERVICE, account) is None, "delete_secret left the value"
    finally:
        keychain.delete_secret(SMOKE_SERVICE, account)
    return keychain.backend_name()


def check_notification_command() -> str:
    """Build the command without firing it — CI has no desktop session."""
    command = notify._notify_command("recto", UNICODE_TITLE)
    if command is None:
        # Only legitimate on a Linux box with no notify-send installed.
        assert not config.IS_MACOS and not config.IS_WINDOWS, "no notification backend"
        return "none (notify-send absent)"
    assert command[0], "empty program name"
    return command[0]


def check_digest_roundtrip() -> str:
    paper = Paper(
        id="smoke:1",
        source="arxiv",
        title=UNICODE_TITLE,
        authors=["Ada Lovelace", "Søren Kierkegaard"],
        abstract="Une étude — with an em dash and a ‘curly quote’.",
        published_at=datetime.now(UTC),
        url="https://example.com/paper",
    )
    markdown = render_markdown(
        run_date="2026-08-25",
        shortlisted=[paper],
        paper_of_day=paper,
        rationale="smoke test",
        degraded=False,
        degraded_reasons=[],
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = write_digest("2026-08-25", markdown, Path(tmp))
        assert UNICODE_TITLE in path.read_text(encoding="utf-8"), "digest lost its UTF-8 title"
        # Re-running a day overwrites rather than duplicating.
        again = write_digest("2026-08-25", markdown, Path(tmp))
        assert again == path
    return "utf-8 preserved"


def check_sqlite_store() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "db.sqlite")
        try:
            store.upsert_papers(
                [
                    Paper(
                        id="smoke:1",
                        source="arxiv",
                        title=UNICODE_TITLE,
                        authors=["Ada Lovelace"],
                        abstract="Une étude.",
                        published_at=datetime.now(UTC),
                        url="https://example.com/paper",
                    )
                ]
            )
            got = store.get_paper("smoke:1")
            assert got is not None and got.title == UNICODE_TITLE, "sqlite roundtrip failed"
        finally:
            store.close()
    return "roundtrip ok"


def _cfg(home: Path) -> config.Config:
    config.write_default_config(
        home=home,
        contact_email="smoke@example.com",
        orcid_id="0000-0000-0000-0000",
        email_address="smoke@example.com",
    )
    return config.load_config(home)


def check_scheduler_artifact() -> str:
    """Hand the generated file to the OS's own validator. This is the check
    that catches a plist/unit/XML the platform would reject at install time."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        cfg = _cfg(home)

        if config.IS_MACOS:
            path = home / "com.smoke.recto.plist"
            scheduler.write_plist(path, scheduler.build_plist(home, cfg))
            result = _run(["plutil", "-lint", str(path)])
            assert result.returncode == 0, f"plutil rejected the plist: {result.stdout.strip()}"
            return "plutil -lint"

        if config.IS_LINUX:
            service, timer = scheduler.build_systemd_units(home, cfg)
            (home / "recto.service").write_text(service, encoding="utf-8")
            (home / "recto.timer").write_text(timer, encoding="utf-8")
            if not shutil.which("systemd-analyze"):
                return "skipped (no systemd-analyze)"
            result = _run(
                [
                    "systemd-analyze",
                    "verify",
                    "--user",
                    str(home / "recto.service"),
                    str(home / "recto.timer"),
                ]
            )
            assert result.returncode == 0, f"systemd rejected the units:\n{result.stderr.strip()}"
            return "systemd-analyze verify"

        if config.IS_WINDOWS:
            # Register for real under a throwaway name, then remove it. This
            # is the only way to learn that schtasks accepts the XML.
            path = home / "recto-smoke-task.xml"
            path.write_text(scheduler.build_task_xml(home, cfg), encoding="utf-16")
            task = "recto-smoke-test"
            created = _run(["schtasks", "/Create", "/TN", task, "/XML", str(path), "/F"])
            try:
                assert created.returncode == 0, (
                    f"schtasks rejected the task XML: {created.stdout.strip()} "
                    f"{created.stderr.strip()}"
                )
                query = _run(["schtasks", "/Query", "/TN", task])
                assert query.returncode == 0, "task did not register"
            finally:
                _run(["schtasks", "/Delete", "/TN", task, "/F"])
            return "schtasks /Create + /Delete"

    return "no backend for this platform"


def check_cli_help() -> str:
    from recto.cli import main

    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0, f"--help exited {exc.code}"
    return "recto --help"


def main() -> int:
    # A failure message can carry the non-ASCII probe strings, and this runs
    # under a cp1252 console in CI on purpose. Never let the report itself be
    # the thing that dies.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(f"recto platform smoke — {sys.platform}, python {sys.version.split()[0]}\n")
    check("platform flags", check_platform_flags)
    check("data/log directories", check_directories)
    check("secret store roundtrip", check_secret_store)
    check("notification command", check_notification_command)
    check("digest utf-8 roundtrip", check_digest_roundtrip)
    check("sqlite store", check_sqlite_store)
    check("scheduler artifact", check_scheduler_artifact)
    check("cli --help", check_cli_help)

    if failures:
        print(f"\n{len(failures)} check(s) failed:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
