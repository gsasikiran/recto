"""launchd user agent installer: StartCalendarInterval, wake-tolerant, no
long-lived daemon. If the Mac was asleep at the scheduled time, launchd
fires on wake — that's what makes this behave like "first time you open
your Mac each day" rather than needing an always-on watcher process.
"""

from __future__ import annotations

import getpass
import plistlib
import shutil
import subprocess
from pathlib import Path

from recto import config


def label() -> str:
    return f"com.{getpass.getuser()}.recto"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label()}.plist"


def build_plist(home: Path, cfg: config.Config) -> dict:
    uv_path = shutil.which("uv") or "/usr/local/bin/uv"
    logs = config.logs_dir()
    return {
        "Label": label(),
        "ProgramArguments": [uv_path, "run", "recto", "run"],
        "WorkingDirectory": str(home.resolve()),
        "EnvironmentVariables": {"RECTO_HOME": str(home.resolve())},
        "StartCalendarInterval": {"Hour": cfg.schedule.hour, "Minute": cfg.schedule.minute},
        "StandardOutPath": str(logs / "recto.out.log"),
        "StandardErrorPath": str(logs / "recto.err.log"),
        "RunAtLoad": False,
    }


def write_plist(path: Path, plist: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        plistlib.dump(plist, fh)


def install_agent(home: Path) -> Path:
    cfg = config.load_config(home)
    plist = build_plist(home, cfg)
    path = plist_path()
    write_plist(path, plist)

    uid = subprocess.run(["id", "-u"], capture_output=True, text=True, check=True).stdout.strip()
    # bootout first so re-running install-agent after an edit takes effect;
    # ignore failure since it errors if nothing was loaded yet.
    subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(path)], capture_output=True)
    subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(["launchctl", "enable", f"gui/{uid}/{label()}"], capture_output=True)
    return path
