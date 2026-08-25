import httpx

from recto.net import RateLimiter, request


def test_rate_limiter_enforces_min_interval():
    import time

    interval = 0.05
    limiter = RateLimiter(min_interval_seconds=interval)

    start = time.monotonic()
    limiter.wait()
    limiter.wait()
    elapsed = time.monotonic() - start

    # time.sleep() can return a few ms early relative to monotonic() — the
    # Windows timer granularity is ~15ms, and a 50ms sleep there measures
    # ~47ms. Assert the limiter waited, not that the OS clock is exact; the
    # real intervals this guards (arXiv's 3s) have all the slack they need.
    assert elapsed >= interval * 0.9


def test_request_retries_on_429_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def fake_request(self, method, url, **kwargs):
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(429, request=httpx.Request(method, url))
        return httpx.Response(200, json={"ok": True}, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    monkeypatch.setattr("recto.net._sleep_backoff", lambda attempt: None)

    resp = request("GET", "https://example.com/x", max_retries=3)
    assert resp is not None
    assert resp.status_code == 200
    assert calls["n"] == 2


def test_request_gives_up_and_returns_none_on_connection_error(monkeypatch):
    def fake_request(self, method, url, **kwargs):
        raise httpx.ConnectError("boom", request=httpx.Request(method, url))

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    monkeypatch.setattr("recto.net._sleep_backoff", lambda attempt: None)

    resp = request("GET", "https://example.com/x", max_retries=1)
    assert resp is None
