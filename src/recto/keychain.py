"""macOS Keychain access via the `security` CLI.

Secrets (Outlook password, OpenRouter key) never live in config.toml or the
repo — Keychain only. Deliberately shells out to `security` instead of
adding the `keyring` PyPI package, per the "small dependency footprint"
working agreement.
"""

from __future__ import annotations

import subprocess

SERVICE_OUTLOOK = "recto-outlook"
SERVICE_OPENROUTER = "recto-openrouter"
SERVICE_SEMANTICSCHOLAR = "recto-semanticscholar"
SERVICE_IMAP = "recto-imap"


def set_secret(service: str, account: str, secret: str) -> None:
    subprocess.run(
        ["security", "add-generic-password", "-a", account, "-s", service, "-w", secret, "-U"],
        check=True,
        capture_output=True,
        text=True,
    )


def get_secret(service: str, account: str) -> str | None:
    result = subprocess.run(
        ["security", "find-generic-password", "-a", account, "-s", service, "-w"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.rstrip("\n")


def delete_secret(service: str, account: str) -> None:
    subprocess.run(
        ["security", "delete-generic-password", "-a", account, "-s", service],
        capture_output=True,
        text=True,
    )
