from pathlib import Path

import httpx
import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def block_real_network(monkeypatch):
    """Tests never hit the network. Any real HTTP call that slips past
    dependency-injected fetch seams fails loudly instead of hanging/flaking."""

    def _blocked(*args, **kwargs):
        raise RuntimeError("Real network call attempted during tests")

    monkeypatch.setattr(httpx.Client, "send", _blocked)


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Tests never touch the real data dir, log dir, or file secret store —
    on any OS, including the developer's own machine. Anything that reaches
    config.data_dir()/logs_dir() lands in tmp_path instead."""
    monkeypatch.setenv("RECTO_DATA_DIR", str(tmp_path / "_data"))
    monkeypatch.setenv("RECTO_LOGS_DIR", str(tmp_path / "_logs"))
