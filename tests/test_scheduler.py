import plistlib

import pytest

from recto import config, scheduler


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    """logs_dir()/data_dir() are used while building units; keep them out of
    the real user directories on whatever OS the tests run on."""
    monkeypatch.setenv("RECTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RECTO_LOGS_DIR", str(tmp_path / "logs"))


def _cfg(tmp_path):
    config.write_default_config(
        home=tmp_path,
        contact_email="me@example.com",
        orcid_id="0000-0003-3098-4592",
        email_address="me@outlook.com",
    )
    return config.load_config(tmp_path)


def _as_platform(monkeypatch, name):
    for attr, value in (
        ("IS_MACOS", name == "macos"),
        ("IS_WINDOWS", name == "windows"),
        ("IS_LINUX", name == "linux"),
    ):
        monkeypatch.setattr(config, attr, value)


# --- macOS / launchd -----------------------------------------------------


def test_build_plist_has_expected_schedule_and_paths(tmp_path):
    cfg = _cfg(tmp_path)
    plist = scheduler.build_plist(tmp_path, cfg)

    assert plist["Label"] == scheduler.label()
    assert plist["StartCalendarInterval"] == {"Hour": 8, "Minute": 0}
    assert plist["ProgramArguments"][1:] == ["run", "recto", "run"]
    assert plist["WorkingDirectory"] == str(tmp_path.resolve())
    assert plist["EnvironmentVariables"]["RECTO_HOME"] == str(tmp_path.resolve())
    assert plist["RunAtLoad"] is True


def test_write_plist_produces_valid_plist_xml(tmp_path):
    cfg = _cfg(tmp_path)
    plist = scheduler.build_plist(tmp_path, cfg)
    out_path = tmp_path / "com.test.recto.plist"

    scheduler.write_plist(out_path, plist)

    assert out_path.exists()
    with out_path.open("rb") as fh:
        loaded = plistlib.load(fh)
    assert loaded["Label"] == plist["Label"]
    assert loaded["StartCalendarInterval"]["Hour"] == 8


def test_label_uses_current_username():
    import getpass

    assert scheduler.label() == f"com.{getpass.getuser()}.recto"


# --- Linux / systemd -----------------------------------------------------


def test_systemd_units_carry_schedule_and_workdir(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.schedule.hour = 7
    cfg.schedule.minute = 5
    service, timer = scheduler.build_systemd_units(tmp_path, cfg)

    assert "OnCalendar=*-*-* 07:05:00" in timer
    # The catch-up switch: without it a shutdown spanning 07:05 loses the day.
    assert "Persistent=true" in timer
    assert "WantedBy=timers.target" in timer
    assert f"WorkingDirectory={tmp_path.resolve()}" in service
    assert f"Environment=RECTO_HOME={tmp_path.resolve()}" in service
    assert "run recto run" in service
    assert "Type=oneshot" in service


def test_systemd_unit_dir_follows_xdg_config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert scheduler.systemd_unit_dir() == tmp_path / "cfg" / "systemd" / "user"


# --- Windows / Task Scheduler --------------------------------------------


def test_task_xml_is_wellformed_and_carries_schedule(tmp_path):
    from xml.etree import ElementTree

    cfg = _cfg(tmp_path)
    cfg.schedule.hour = 9
    cfg.schedule.minute = 30
    xml = scheduler.build_task_xml(tmp_path, cfg)

    root = ElementTree.fromstring(xml)
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert root.find(".//t:StartBoundary", ns).text.endswith("T09:30:00")
    assert root.find(".//t:StartWhenAvailable", ns).text == "true"
    assert root.find(".//t:LogonTrigger", ns) is not None
    args = root.find(".//t:Exec/t:Arguments", ns).text
    assert str(tmp_path.resolve()) in args
    assert args.startswith("run recto --home")


def test_task_xml_escapes_special_characters(tmp_path, monkeypatch):
    from xml.etree import ElementTree

    cfg = _cfg(tmp_path)
    home = tmp_path / "R&D papers"
    home.mkdir()
    monkeypatch.setattr(scheduler, "uv_path", lambda: r"C:\A&B\uv.exe")

    root = ElementTree.fromstring(scheduler.build_task_xml(home, cfg))
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert root.find(".//t:Exec/t:Command", ns).text == r"C:\A&B\uv.exe"
    assert "R&D papers" in root.find(".//t:Exec/t:WorkingDirectory", ns).text


# --- Dispatch ------------------------------------------------------------


@pytest.mark.parametrize(
    ("platform", "expected"),
    [("macos", "launchd"), ("windows", "Task Scheduler"), ("linux", "systemd")],
)
def test_backend_name_per_platform(monkeypatch, platform, expected):
    _as_platform(monkeypatch, platform)
    assert scheduler.backend_name() == expected


def test_install_agent_dispatches_per_platform(tmp_path, monkeypatch):
    _cfg(tmp_path)
    calls = []
    for name in ("_install_launchd", "_install_systemd", "_install_schtasks"):
        monkeypatch.setattr(
            scheduler,
            name,
            lambda home, cfg, _n=name: (calls.append(_n), tmp_path / _n)[1],
        )

    for platform, expected in (
        ("macos", "_install_launchd"),
        ("windows", "_install_schtasks"),
        ("linux", "_install_systemd"),
    ):
        _as_platform(monkeypatch, platform)
        assert scheduler.install_agent(tmp_path).name == expected
    assert calls == ["_install_launchd", "_install_schtasks", "_install_systemd"]


def test_install_agent_raises_on_unknown_platform(tmp_path, monkeypatch):
    _cfg(tmp_path)
    _as_platform(monkeypatch, "aix")
    with pytest.raises(RuntimeError, match="no scheduler backend"):
        scheduler.install_agent(tmp_path)
