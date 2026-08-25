"""Cross-platform secret storage for the Outlook password, OpenRouter key,
Semantic Scholar key and IMAP password.

Secrets never live in config.toml or the repo. Where they *do* live depends
on the OS, in this order:

1. An environment variable — `RECTO_<SERVICE>_SECRET`, e.g.
   `RECTO_OPENROUTER_SECRET`. Checked first everywhere, which is how a
   headless Linux box or a CI run supplies credentials with no keyring at all.
2. The OS keychain:
   - macOS: the `security` CLI (login keychain).
   - Linux: `secret-tool` (libsecret — GNOME Keyring, KWallet via the
     Secret Service API). Needs a D-Bus session; absent one it falls through.
   - Windows: a file store encrypted with DPAPI, which ties the ciphertext to
     the current Windows user account. There is no usable CLI for the Windows
     Credential Manager (`cmdkey` cannot read a password back), so DPAPI —
     the same primitive Credential Manager itself uses — is the honest option.
3. A last-resort plaintext file store under the data dir, mode 0600. Used
   only when nothing better exists; `set_secret` logs a warning when it
   lands here.

Deliberately shells out rather than adding the `keyring` PyPI package, per
the "small dependency footprint" working agreement.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from recto import config

logger = logging.getLogger(__name__)

SERVICE_OUTLOOK = "recto-outlook"
SERVICE_OPENROUTER = "recto-openrouter"
SERVICE_SEMANTICSCHOLAR = "recto-semanticscholar"
SERVICE_IMAP = "recto-imap"

_SECRETS_FILENAME = "secrets.json"

# `security ... -g` writes this to stderr when the secret isn't printable
# ASCII. See _security_get.
_HEX_PASSWORD_RE = re.compile(r"^password: 0x([0-9A-Fa-f]+)", re.MULTILINE)


def env_var_name(service: str) -> str:
    """`recto-openrouter` -> `RECTO_OPENROUTER_SECRET`."""
    return service.upper().replace("-", "_") + "_SECRET"


def backend_name() -> str:
    """Which store `set_secret` would use. For user-facing messages."""
    if config.IS_MACOS and shutil.which("security"):
        return "macOS Keychain"
    if config.IS_WINDOWS:
        return "DPAPI-encrypted file"
    if shutil.which("secret-tool"):
        return "Secret Service (secret-tool)"
    return "plaintext file"


# --- macOS: security(1) -------------------------------------------------


def _security_set(service: str, account: str, secret: str) -> bool:
    result = subprocess.run(
        ["security", "add-generic-password", "-a", account, "-s", service, "-w", secret, "-U"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _security_get(service: str, account: str) -> str | None:
    """Read a password back from the login keychain.

    `security ... -w` prints the secret hex-encoded, with no marker, whenever
    it isn't plain printable ASCII — a password with an umlaut in it comes
    back as `704073732077c3b67264`, which is itself a plausible-looking
    password, so there is no safe way to tell from `-w` alone. `-g` reports
    the same secret on stderr in an unambiguous form:

        password: 0x704073732077C3B67264  "p@ss w\\303\\266rd"   (non-ASCII)
        password: "hunter2"                                      (plain)

    So: ask `-g` first, decode the hex when it says hex, and otherwise fall
    back to `-w`, which is exact for the printable-ASCII case and avoids
    having to unescape the quoted rendering.
    """
    probe = subprocess.run(
        ["security", "find-generic-password", "-a", account, "-s", service, "-g"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if probe.returncode != 0:
        return None

    hex_match = _HEX_PASSWORD_RE.search(probe.stderr)
    if hex_match:
        try:
            return bytes.fromhex(hex_match.group(1)).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            logger.warning("keychain returned a password for %s that is not UTF-8", service)
            return None

    result = subprocess.run(
        ["security", "find-generic-password", "-a", account, "-s", service, "-w"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return None
    return result.stdout.rstrip("\n")


def _security_delete(service: str, account: str) -> None:
    subprocess.run(
        ["security", "delete-generic-password", "-a", account, "-s", service],
        capture_output=True,
        text=True,
    )


# --- Linux: secret-tool (libsecret) -------------------------------------


def _secret_tool_set(service: str, account: str, secret: str) -> bool:
    result = subprocess.run(
        [
            "secret-tool",
            "store",
            "--label",
            f"recto: {service}",
            "service",
            service,
            "account",
            account,
        ],
        input=secret,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.returncode == 0


def _secret_tool_get(service: str, account: str) -> str | None:
    result = subprocess.run(
        ["secret-tool", "lookup", "service", service, "account", account],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout.rstrip("\n")


def _secret_tool_delete(service: str, account: str) -> None:
    subprocess.run(
        ["secret-tool", "clear", "service", service, "account", account],
        capture_output=True,
        text=True,
    )


# --- Windows: DPAPI ------------------------------------------------------


def _dpapi(protect: bool, data: bytes) -> bytes | None:
    """CryptProtectData/CryptUnprotectData against the current user's key.

    Returns None if the call fails, e.g. decrypting a blob written by a
    different Windows user.
    """
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]

    src = _Blob(
        len(data), ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char))
    )
    out = _Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    args = [ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)]
    if not fn(*args):
        return None
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


# --- Fallback: file store under the data dir ----------------------------


def _secrets_file() -> Path:
    return config.data_dir() / _SECRETS_FILENAME


def _read_store() -> dict[str, dict[str, str]]:
    path = _secrets_file()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("could not read %s, treating it as empty", path, exc_info=True)
        return {}


def _write_store(store: dict[str, dict[str, str]]) -> None:
    path = _secrets_file()
    # Create restricted, then write: chmod-after-write would leave the
    # contents world-readable for the length of the write.
    path.touch(mode=0o600, exist_ok=True)
    path.write_text(json.dumps(store, indent=2, sort_keys=True), encoding="utf-8")
    if not config.IS_WINDOWS:
        # Also covers a file created before this ran. On Windows the data dir
        # is already per-user ACL'd and POSIX modes are meaningless there.
        os.chmod(path, 0o600)


def _file_set(service: str, account: str, secret: str) -> bool:
    raw = secret.encode("utf-8")
    encoding = "plain"
    if config.IS_WINDOWS:
        protected = _dpapi(True, raw)
        if protected is None:
            logger.warning("DPAPI encryption failed, storing secret unencrypted")
        else:
            raw, encoding = protected, "dpapi"
    store = _read_store()
    store[f"{service}\n{account}"] = {
        "encoding": encoding,
        "value": base64.b64encode(raw).decode("ascii"),
    }
    _write_store(store)
    if encoding == "plain":
        logger.warning(
            "stored %s for %s in plaintext at %s (no OS keyring available)",
            service,
            account,
            _secrets_file(),
        )
    return True


def _file_get(service: str, account: str) -> str | None:
    entry = _read_store().get(f"{service}\n{account}")
    if not entry:
        return None
    try:
        raw = base64.b64decode(entry["value"])
    except (KeyError, ValueError):
        return None
    if entry.get("encoding") == "dpapi":
        unprotected = _dpapi(False, raw)
        if unprotected is None:
            logger.warning(
                "could not decrypt %s for %s with this user's DPAPI key", service, account
            )
            return None
        raw = unprotected
    return raw.decode("utf-8")


def _file_delete(service: str, account: str) -> None:
    store = _read_store()
    if store.pop(f"{service}\n{account}", None) is not None:
        _write_store(store)


# --- Public API ----------------------------------------------------------


def set_secret(service: str, account: str, secret: str) -> None:
    if config.IS_MACOS and shutil.which("security"):
        if _security_set(service, account, secret):
            return
        logger.warning("security(1) could not store %s, falling back to file store", service)
    elif not config.IS_WINDOWS and shutil.which("secret-tool"):
        if _secret_tool_set(service, account, secret):
            return
        logger.warning("secret-tool could not store %s, falling back to file store", service)
    _file_set(service, account, secret)


def get_secret(service: str, account: str) -> str | None:
    from_env = os.environ.get(env_var_name(service))
    if from_env:
        return from_env
    if config.IS_MACOS and shutil.which("security"):
        found = _security_get(service, account)
        if found is not None:
            return found
    elif not config.IS_WINDOWS and shutil.which("secret-tool"):
        found = _secret_tool_get(service, account)
        if found is not None:
            return found
    return _file_get(service, account)


def delete_secret(service: str, account: str) -> None:
    if config.IS_MACOS and shutil.which("security"):
        _security_delete(service, account)
    elif not config.IS_WINDOWS and shutil.which("secret-tool"):
        _secret_tool_delete(service, account)
    _file_delete(service, account)
