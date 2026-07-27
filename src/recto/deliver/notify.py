"""macOS banner notification — disabled by default in config.toml.

A plain `osascript` notification can't reliably carry a click action (that
needs a signed helper or terminal-notifier); this is a fire-and-forget
banner only. Failures are logged, never raised — delivery is best-effort.
"""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)


def _escape_applescript(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def send_notification(*, title: str, subtitle: str, enabled: bool) -> None:
    if not enabled:
        return
    script = (
        f'display notification "{_escape_applescript(subtitle)}" '
        f'with title "{_escape_applescript(title)}"'
    )
    try:
        subprocess.run(["osascript", "-e", script], timeout=5, check=False, capture_output=True)
    except Exception:
        logger.warning("failed to show notification", exc_info=True)
