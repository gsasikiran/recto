"""Daily-run scheduling, one backend per OS. No long-lived daemon anywhere.

  macOS    launchd user agent      ~/Library/LaunchAgents/com.<user>.recto.plist
  Linux    systemd user timer      ~/.config/systemd/user/recto.{service,timer}
  Windows  Task Scheduler task     registered as "recto" via schtasks /XML

The shared requirement is "behave like the first time the machine is awake
each day." Sleep/wake catches up on its own under launchd; a full
shutdown/reboot spanning the scheduled time does not — a fresh scheduler has
no memory of a missed slot and just waits for the next one. Each backend gets
an equivalent catch-up switch for that gap, and since the run is idempotent an
extra trigger at login/boot is harmless:

  launchd  RunAtLoad
  systemd  Persistent=true on the timer
  Windows  <StartWhenAvailable> plus a logon trigger
"""

from __future__ import annotations

import getpass
import plistlib
import shutil
import subprocess
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from recto import config


def label() -> str:
    return f"com.{getpass.getuser()}.recto"


def backend_name() -> str:
    """Which scheduler this platform uses. For user-facing messages."""
    if config.IS_MACOS:
        return "launchd"
    if config.IS_WINDOWS:
        return "Task Scheduler"
    if config.IS_LINUX:
        return "systemd"
    return "unsupported"


def uv_path() -> str:
    """Absolute path to `uv` — schedulers run with a minimal PATH."""
    found = shutil.which("uv")
    if found:
        return found
    if config.IS_WINDOWS:
        return str(Path.home() / ".local" / "bin" / "uv.exe")
    if config.IS_MACOS:
        return "/usr/local/bin/uv"
    return str(Path.home() / ".local" / "bin" / "uv")


# --- macOS: launchd ------------------------------------------------------


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label()}.plist"


def build_plist(home: Path, cfg: config.Config) -> dict:
    logs = config.logs_dir()
    return {
        "Label": label(),
        "ProgramArguments": [uv_path(), "run", "recto", "run"],
        "WorkingDirectory": str(home.resolve()),
        "EnvironmentVariables": {"RECTO_HOME": str(home.resolve())},
        "StartCalendarInterval": {"Hour": cfg.schedule.hour, "Minute": cfg.schedule.minute},
        "StandardOutPath": str(logs / "recto.out.log"),
        "StandardErrorPath": str(logs / "recto.err.log"),
        "RunAtLoad": True,
    }


def write_plist(path: Path, plist: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        plistlib.dump(plist, fh)


def _install_launchd(home: Path, cfg: config.Config) -> Path:
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


# --- Linux: systemd user timer -------------------------------------------

SYSTEMD_UNIT_NAME = "recto"


def systemd_unit_dir() -> Path:
    base = config.env_dir("XDG_CONFIG_HOME") or Path.home() / ".config"
    return base / "systemd" / "user"


def build_systemd_units(home: Path, cfg: config.Config) -> tuple[str, str]:
    """Return (service_unit, timer_unit) file contents."""
    workdir = str(home.resolve())
    service = f"""\
[Unit]
Description=recto daily research digest
# No network-online.target ordering: that's a system unit and does not exist
# in the user manager, so Wants= on it only enqueues a job that fails. A dead
# network degrades the digest on its own.

[Service]
Type=oneshot
WorkingDirectory={workdir}
Environment=RECTO_HOME={workdir}
ExecStart="{uv_path()}" run recto run
"""
    timer = f"""\
[Unit]
Description=recto daily research digest

[Timer]
OnCalendar=*-*-* {cfg.schedule.hour:02d}:{cfg.schedule.minute:02d}:00
# Catch up after a suspend or a shutdown that spanned the scheduled time.
Persistent=true
AccuracySec=1min

[Install]
WantedBy=timers.target
"""
    return service, timer


def _install_systemd(home: Path, cfg: config.Config) -> Path:
    service, timer = build_systemd_units(home, cfg)
    unit_dir = systemd_unit_dir()
    unit_dir.mkdir(parents=True, exist_ok=True)
    service_path = unit_dir / f"{SYSTEMD_UNIT_NAME}.service"
    timer_path = unit_dir / f"{SYSTEMD_UNIT_NAME}.timer"
    service_path.write_text(service, encoding="utf-8")
    timer_path.write_text(timer, encoding="utf-8")

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, capture_output=True)
    subprocess.run(
        ["systemctl", "--user", "enable", "--now", f"{SYSTEMD_UNIT_NAME}.timer"],
        check=True,
        capture_output=True,
        text=True,
    )
    return timer_path


# --- Windows: Task Scheduler ---------------------------------------------

WINDOWS_TASK_NAME = "recto"


def build_task_xml(home: Path, cfg: config.Config) -> str:
    """Task Scheduler XML. Registered with `schtasks /Create /XML`, which
    requires the file be UTF-16 — see _install_schtasks.

    There is no Environment element for an Exec action, so RECTO_HOME is
    passed as `--home` on the command line instead.
    """
    workdir = xml_escape(str(home.resolve()))
    command = xml_escape(uv_path())
    start = f"2000-01-01T{cfg.schedule.hour:02d}:{cfg.schedule.minute:02d}:00"
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>recto daily research digest</Description>
    <URI>\\{WINDOWS_TASK_NAME}</URI>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{start}</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <StartWhenAvailable>true</StartWhenAvailable>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>run recto --home "{workdir}" run</Arguments>
      <WorkingDirectory>{workdir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def task_xml_path() -> Path:
    return config.data_dir() / f"{WINDOWS_TASK_NAME}-task.xml"


def _install_schtasks(home: Path, cfg: config.Config) -> Path:
    path = task_xml_path()
    # schtasks rejects a UTF-8 XML file; the declaration above says UTF-16 and
    # the bytes on disk have to agree with it.
    path.write_text(build_task_xml(home, cfg), encoding="utf-16")
    subprocess.run(
        ["schtasks", "/Create", "/TN", WINDOWS_TASK_NAME, "/XML", str(path), "/F"],
        check=True,
        capture_output=True,
        text=True,
    )
    return path


# --- Dispatch ------------------------------------------------------------


def install_agent(home: Path) -> Path:
    """Install and activate the daily schedule. Returns the file written."""
    cfg = config.load_config(home)
    if config.IS_MACOS:
        return _install_launchd(home, cfg)
    if config.IS_WINDOWS:
        return _install_schtasks(home, cfg)
    if config.IS_LINUX:
        return _install_systemd(home, cfg)
    raise RuntimeError(
        "no scheduler backend for this platform; run `recto run` from your own cron/scheduler"
    )
