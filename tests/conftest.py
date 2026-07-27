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
