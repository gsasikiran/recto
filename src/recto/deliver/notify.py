"""Desktop banner notification — disabled by default in config.toml.

One backend per OS, all fire-and-forget:

  macOS    osascript `display notification`
  Linux    notify-send (libnotify; present on most desktops)
  Windows  a PowerShell balloon tip, which Win10/11 surface as a toast

None of these carry a click action reliably — on macOS that needs a signed
helper or terminal-notifier, and the PowerShell tray-icon route has no
useful activation handler — so this is a banner only. Failures are logged,
never raised: delivery is best-effort, and the digest is already on disk.
"""

from __future__ import annotations

import logging
import shutil
import subprocess

from recto import config

logger = logging.getLogger(__name__)


def _escape_applescript(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _escape_powershell(s: str) -> str:
    """Single-quoted PowerShell strings escape a quote by doubling it."""
    return s.replace("'", "''")


def _macos_command(title: str, subtitle: str) -> list[str]:
    script = (
        f'display notification "{_escape_applescript(subtitle)}" '
        f'with title "{_escape_applescript(title)}"'
    )
    return ["osascript", "-e", script]


def _linux_command(title: str, subtitle: str) -> list[str]:
    return ["notify-send", "--app-name=recto", "--expire-time=10000", title, subtitle]


def _windows_command(title: str, subtitle: str) -> list[str]:
    script = (
        # System.Drawing is implicit under Windows PowerShell 5.1 but not
        # under pwsh 7, and SystemIcons needs it either way.
        "Add-Type -AssemblyName System.Windows.Forms, System.Drawing; "
        "$n = New-Object System.Windows.Forms.NotifyIcon; "
        "$n.Icon = [System.Drawing.SystemIcons]::Information; "
        f"$n.BalloonTipTitle = '{_escape_powershell(title)}'; "
        f"$n.BalloonTipText = '{_escape_powershell(subtitle)}'; "
        "$n.Visible = $true; $n.ShowBalloonTip(10000); "
        "Start-Sleep -Seconds 5; $n.Dispose()"
    )
    powershell = shutil.which("pwsh") or "powershell"
    return [powershell, "-NoProfile", "-NonInteractive", "-Command", script]


def _notify_command(title: str, subtitle: str) -> list[str] | None:
    if config.IS_MACOS:
        return _macos_command(title, subtitle)
    if config.IS_WINDOWS:
        return _windows_command(title, subtitle)
    if shutil.which("notify-send"):
        return _linux_command(title, subtitle)
    return None


def send_notification(*, title: str, subtitle: str, enabled: bool) -> None:
    if not enabled:
        return
    command = _notify_command(title, subtitle)
    if command is None:
        logger.warning("no notification backend on this platform, skipping banner")
        return
    try:
        # The Windows backend sleeps while the balloon is on screen, so the
        # timeout has to outlast that; 5s is plenty everywhere else.
        timeout = 20 if config.IS_WINDOWS else 5
        subprocess.run(command, timeout=timeout, check=False, capture_output=True)
    except Exception:
        logger.warning("failed to show notification", exc_info=True)
