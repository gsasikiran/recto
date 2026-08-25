"""SMTP delivery to the user's own address. Credentials come from the OS
secret store (see keychain.py), never config.toml. Raises on failure — cli.py
catches it and marks the run degraded, but the digest markdown file is already
written to disk regardless of delivery outcome.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
import subprocess
from email.message import EmailMessage

from recto import config

logger = logging.getLogger(__name__)


def _trust_context() -> ssl.SSLContext:
    """On macOS, Python's default SSL context reads a static /etc/ssl/cert.pem
    bundle that lags behind the OS's actual trust store — newer roots (e.g.
    academic CAs like HARICA, used by some institutional mail servers) are
    trusted by macOS itself but missing from that file. Building the context
    from the live System Roots keychain avoids spurious
    CERTIFICATE_VERIFY_FAILED errors against servers whose cert chains are
    otherwise perfectly valid.

    Linux and Windows don't have that gap: OpenSSL reads the distro's CA
    bundle directly, and on Windows Python's default context loads roots from
    the system certificate store. Both use the stdlib default.
    """
    if not config.IS_MACOS:
        return ssl.create_default_context()
    try:
        result = subprocess.run(
            [
                "security",
                "find-certificate",
                "-a",
                "-p",
                "/System/Library/Keychains/SystemRootCertificates.keychain",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return ssl.create_default_context(cadata=result.stdout)
    except Exception:
        logger.warning("could not load macOS system trust store, using default", exc_info=True)
        return ssl.create_default_context()


def send_email(
    *,
    subject: str,
    html_body: str,
    plain_body: str,
    smtp_host: str,
    smtp_port: int,
    use_starttls: bool,
    from_addr: str,
    to_addr: str,
    username: str,
    password: str | None,
) -> None:
    if not password:
        raise RuntimeError("no SMTP password available in the secret store")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(plain_body)
    msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as smtp:
        if use_starttls:
            smtp.starttls(context=_trust_context())
        smtp.login(username, password)
        smtp.send_message(msg)
