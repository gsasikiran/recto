from email import message_from_bytes
from pathlib import Path

from recto.sources import scholar_alerts

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _fixture_html_body() -> str:
    raw = (FIXTURES_DIR / "scholar_alerts" / "sample_alert.eml").read_bytes()
    msg = message_from_bytes(raw)
    return scholar_alerts._extract_html_body(msg)


def test_extract_html_body_from_multipart_eml():
    body = _fixture_html_body()
    assert body is not None
    assert "Deep Learning for Neurosymbolic Reasoning" in body


def test_parse_alert_html_extracts_two_items():
    items = scholar_alerts.parse_alert_html(_fixture_html_body())
    assert len(items) == 2

    first = items[0]
    assert first.title == "Deep Learning for Neurosymbolic Reasoning"
    assert first.url == "https://arxiv.org/abs/2507.12345"
    assert "neurosymbolic" in first.snippet.lower() or "neural networks" in first.snippet.lower()

    second = items[1]
    assert second.title == "Scaling Laws for Multi-Agent Systems"
    assert "openreview.net" in second.url


def test_item_to_paper_builds_stable_slug_id():
    items = scholar_alerts.parse_alert_html(_fixture_html_body())
    from datetime import UTC, datetime

    paper = scholar_alerts.item_to_paper(items[0], datetime.now(UTC))
    assert paper.id == "scholar_alerts:deep-learning-for-neurosymbolic-reasoning"
    assert paper.source == "scholar_alerts"
    assert paper.abstract is not None


class _FakeImapConn:
    def __init__(self, raw_eml: bytes):
        self.raw_eml = raw_eml
        self.selected = None
        self.marked_read = []

    def select(self, folder):
        self.selected = folder
        return "OK", [b"1"]

    def search(self, charset, criterion):
        return "OK", [b"1"]

    def fetch(self, msg_id, parts):
        return "OK", [(b"1 (RFC822 {123}", self.raw_eml)]

    def store(self, msg_id, flag_cmd, flags):
        self.marked_read.append(msg_id)

    def logout(self):
        pass


def test_fetch_end_to_end_with_fake_imap_connection():
    raw = (FIXTURES_DIR / "scholar_alerts" / "sample_alert.eml").read_bytes()
    fake_conn = _FakeImapConn(raw)

    papers = scholar_alerts.fetch(
        imap_host="imap.example.com",
        imap_port=993,
        imap_username="user@example.com",
        imap_password="secret",
        mail_folder="Scholar Alerts",
        lookback_days=2,
        _connect=lambda: fake_conn,
    )
    assert len(papers) == 2
    assert fake_conn.selected == "Scholar Alerts"
    assert fake_conn.marked_read == []  # mark_as_read defaults False


def test_fetch_degrades_silently_on_connection_failure():
    def failing_connect():
        raise ConnectionRefusedError("no route to host")

    papers = scholar_alerts.fetch(
        imap_host="imap.example.com",
        imap_port=993,
        imap_username="user@example.com",
        imap_password="secret",
        mail_folder="Scholar Alerts",
        lookback_days=2,
        _connect=failing_connect,
    )
    assert papers == []
