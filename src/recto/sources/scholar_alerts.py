"""Google Scholar alerts: read via IMAP, never scrape Google Scholar directly.

Scraping Scholar violates its ToS and gets IPs blocked fast. The supported
path: the user routes Scholar Alert emails to a dedicated mail folder, and
this module reads that folder over IMAP and parses the alert HTML. Titles
and links are reliable; abstracts are truncated snippets, meant to be
re-resolved against OpenAlex/Semantic Scholar by title (see
semanticscholar.search_by_title).

Disabled by default in config.toml until IMAP credentials/folder are set up
No real alert email has been captured yet; the fixture used in tests is a
hand-built synthetic .eml matching the documented Scholar Alert HTML shape
and should be revalidated against a real alert once IMAP is configured.
"""

from __future__ import annotations

import contextlib
import html
import imaplib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email import message_from_bytes
from email.message import Message
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

from recto.models import Paper

logger = logging.getLogger(__name__)

_TITLE_LINK_RE = re.compile(
    r'<a[^>]+href="([^"]*scholar_url[^"]*)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL
)


class _TagStripper(HTMLParser):
    """Minimal stdlib tag-stripper (no bs4) for cleaning snippet/title HTML."""

    def __init__(self):
        super().__init__()
        self._chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        self._chunks.append(data)

    def text(self) -> str:
        return " ".join("".join(self._chunks).split())


def _strip_tags(fragment: str) -> str:
    parser = _TagStripper()
    parser.feed(fragment)
    return html.unescape(parser.text())


def _extract_target_url(href: str) -> str:
    parsed = urlparse(html.unescape(href))
    qs = parse_qs(parsed.query)
    if qs.get("url"):
        return qs["url"][0]
    return html.unescape(href)


@dataclass(slots=True)
class ScholarAlertItem:
    title: str
    url: str
    snippet: str | None


def parse_alert_html(html_body: str) -> list[ScholarAlertItem]:
    matches = list(_TITLE_LINK_RE.finditer(html_body))
    items: list[ScholarAlertItem] = []
    for i, m in enumerate(matches):
        href, title_html = m.group(1), m.group(2)
        title = _strip_tags(title_html)
        if not title:
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(html_body)
        snippet = _strip_tags(html_body[start:end]) or None
        items.append(ScholarAlertItem(title=title, url=_extract_target_url(href), snippet=snippet))
    return items


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:80]


def item_to_paper(item: ScholarAlertItem, seen_at: datetime) -> Paper:
    return Paper(
        id=f"scholar_alerts:{_slug(item.title)}",
        source="scholar_alerts",
        title=item.title,
        authors=[],
        abstract=item.snippet,
        published_at=None,
        url=item.url,
        seen_at=seen_at,
    )


def _extract_html_body(msg: Message) -> str | None:
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.get_content_type() == "text/html":
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
    return None


Connect = Callable[[], object]


def _default_connect(host: str, port: int, username: str, password: str):
    conn = imaplib.IMAP4_SSL(host, port)
    conn.login(username, password)
    return conn


def fetch(
    *,
    imap_host: str,
    imap_port: int,
    imap_username: str,
    imap_password: str,
    mail_folder: str,
    lookback_days: int,
    mark_as_read: bool = False,
    _connect: Connect | None = None,
) -> list[Paper]:
    connect = _connect or (
        lambda: _default_connect(imap_host, imap_port, imap_username, imap_password)
    )
    try:
        conn = connect()
    except Exception:
        logger.warning("scholar_alerts: IMAP connection failed, skipping source", exc_info=True)
        return []

    papers: list[Paper] = []
    try:
        conn.select(mail_folder)
        since = (datetime.now() - timedelta(days=lookback_days)).strftime("%d-%b-%Y")
        status, data = conn.search(None, f"(SINCE {since})")
        if status != "OK" or not data or not data[0]:
            return []
        for msg_id in data[0].split():
            status, msg_data = conn.fetch(msg_id, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = message_from_bytes(msg_data[0][1])
            html_body = _extract_html_body(msg)
            if not html_body:
                continue
            seen_at = datetime.now(UTC)
            papers.extend(item_to_paper(item, seen_at) for item in parse_alert_html(html_body))
            if mark_as_read:
                conn.store(msg_id, "+FLAGS", "\\Seen")
    except Exception:
        logger.warning("scholar_alerts: error while reading mailbox, skipping", exc_info=True)
        return []
    finally:
        with contextlib.suppress(Exception):
            conn.logout()

    return papers
