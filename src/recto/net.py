"""Shared network wrapper: timeout, retry/backoff, per-source rate limiting.

Every source module funnels its HTTP calls through `request()`. On exhausted
retries or unexpected errors it logs and returns None rather than raising —
a dead API degrades that source's results, it never crashes the run. Only
programmer errors (bad arguments) are allowed to propagate.
"""

from __future__ import annotations

import logging
import random
import threading
import time

import httpx

logger = logging.getLogger(__name__)

DEFAULT_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class RateLimiter:
    """Blocks the calling thread until at least `min_interval_seconds` have
    elapsed since this limiter's last call. One instance per source."""

    def __init__(self, min_interval_seconds: float):
        self.min_interval_seconds = min_interval_seconds
        self._lock = threading.Lock()
        self._last_call: float | None = None

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if self._last_call is not None:
                elapsed = now - self._last_call
                remaining = self.min_interval_seconds - elapsed
                if remaining > 0:
                    time.sleep(remaining)
            self._last_call = time.monotonic()


def build_user_agent(source: str, contact_email: str) -> str:
    return f"recto/0.1 ({source}; mailto:{contact_email})"


def request(
    method: str,
    url: str,
    *,
    rate_limiter: RateLimiter | None = None,
    headers: dict | None = None,
    params: dict | None = None,
    json: dict | None = None,
    timeout: float = 15.0,
    max_retries: int = 3,
    retry_statuses: frozenset[int] = DEFAULT_RETRY_STATUSES,
) -> httpx.Response | None:
    attempt = 0
    while attempt <= max_retries:
        if rate_limiter is not None:
            rate_limiter.wait()
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.request(method, url, headers=headers, params=params, json=json)
        except httpx.HTTPError as exc:
            attempt += 1
            if attempt > max_retries:
                logger.warning(
                    "giving up on %s %s after %d attempts: %s", method, url, attempt, exc
                )
                return None
            _sleep_backoff(attempt)
            continue

        if response.status_code in retry_statuses:
            attempt += 1
            if attempt > max_retries:
                logger.warning(
                    "giving up on %s %s after %d attempts: status %d",
                    method,
                    url,
                    attempt,
                    response.status_code,
                )
                return response
            retry_after = response.headers.get("Retry-After")
            if retry_after is not None:
                try:
                    time.sleep(float(retry_after))
                except ValueError:
                    _sleep_backoff(attempt)
            else:
                _sleep_backoff(attempt)
            continue

        return response

    return None


def _sleep_backoff(attempt: int) -> None:
    base = min(2**attempt, 30)
    time.sleep(base + random.uniform(0, 0.5))
