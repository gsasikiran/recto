import plistlib

from recto import config, launchd


def _cfg(tmp_path):
    config.write_default_config(
        home=tmp_path,
        contact_email="me@example.com",
        orcid_id="0000-0003-3098-4592",
        email_address="me@outlook.com",
    )
    return config.load_config(tmp_path)


def test_build_plist_has_expected_schedule_and_paths(tmp_path):
    cfg = _cfg(tmp_path)
    plist = launchd.build_plist(tmp_path, cfg)

    assert plist["Label"] == launchd.label()
    assert plist["StartCalendarInterval"] == {"Hour": 8, "Minute": 0}
    assert plist["ProgramArguments"][1:] == ["run", "recto", "run"]
    assert plist["WorkingDirectory"] == str(tmp_path.resolve())
    assert plist["EnvironmentVariables"]["RECTO_HOME"] == str(tmp_path.resolve())
    assert plist["RunAtLoad"] is True


def test_write_plist_produces_valid_plist_xml(tmp_path):
    cfg = _cfg(tmp_path)
    plist = launchd.build_plist(tmp_path, cfg)
    out_path = tmp_path / "com.test.recto.plist"

    launchd.write_plist(out_path, plist)

    assert out_path.exists()
    with out_path.open("rb") as fh:
        loaded = plistlib.load(fh)
    assert loaded["Label"] == plist["Label"]
    assert loaded["StartCalendarInterval"]["Hour"] == 8


def test_label_uses_current_username():
    import getpass

    assert launchd.label() == f"com.{getpass.getuser()}.recto"
