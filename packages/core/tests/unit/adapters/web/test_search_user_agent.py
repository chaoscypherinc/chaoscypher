# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The WebScraper sends the configured User-Agent on every request.

``WebSettings.user_agent`` (site import design, decision 2) rides on each
request's headers rather than on the module-level cached client, so the cache
stays one client per process and the pinned-IP request keeps its Host header
and SNI extension. Every redirect hop carries it too.
"""

from __future__ import annotations

import httpx
import pytest

import chaoscypher_core.adapters.web.search as _search_mod
from chaoscypher_core.adapters.web.search import WebScraper
from chaoscypher_core.settings import WebSettings


_PINNED_IP = "93.184.216.34"


def _install_transport(
    monkeypatch: pytest.MonkeyPatch, handler: httpx.MockTransport
) -> httpx.AsyncClient:
    """Put a client on a fake transport into the module-level cache."""
    client = httpx.AsyncClient(transport=handler, follow_redirects=False)
    monkeypatch.setattr(_search_mod, "_client", client)
    monkeypatch.setattr(_search_mod, "resolve_pinned_ip", lambda *a, **kw: _PINNED_IP)
    return client


@pytest.mark.asyncio
async def test_every_hop_carries_the_user_agent_and_pinned_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A redirected text fetch sends the User-Agent on both hops, dialing the pinned IP."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/final"})
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="hello world")

    client = _install_transport(monkeypatch, httpx.MockTransport(handler))
    scraper = WebScraper(web_settings=WebSettings(user_agent="TestBot/1.0 (+https://t.example)"))

    result = await scraper.extract_full_content("https://example.com/start", max_bytes=10_000)

    assert result.error is None
    assert [r.url.path for r in seen] == ["/start", "/final"]
    for request in seen:
        assert request.headers["user-agent"] == "TestBot/1.0 (+https://t.example)"
        assert request.headers["host"] == "example.com"
        assert request.url.host == _PINNED_IP
    # The cached client is reused, not rebuilt per user agent.
    assert _search_mod._get_client(1.0) is client


@pytest.mark.asyncio
async def test_default_user_agent_sent_on_uncapped_get(monkeypatch: pytest.MonkeyPatch) -> None:
    """The plain-GET path (``max_bytes=None``) sends the default User-Agent too."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="body text")

    _install_transport(monkeypatch, httpx.MockTransport(handler))

    await WebScraper().extract_full_content("https://example.com/page")
    await WebScraper()._fetch_with_redirect_validation("https://example.com/page")

    assert len(seen) == 2
    assert all(r.headers["user-agent"] == WebSettings().user_agent for r in seen)


@pytest.mark.asyncio
async def test_final_url_follows_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    """``FetchResult.final_url`` is the logical URL after redirects, never the pinned IP."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "https://example.com/new?x=1"})
        return httpx.Response(200, headers={"content-type": "text/markdown"}, text="# Page")

    _install_transport(monkeypatch, httpx.MockTransport(handler))

    result = await WebScraper().extract_full_content("https://example.com/old", max_bytes=1000)

    assert result.url == "https://example.com/old"
    assert result.final_url == "https://example.com/new?x=1"


@pytest.mark.asyncio
async def test_fetch_raw_reports_status_type_body_and_final_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``fetch_raw`` returns the response as-is (any status) for discovery to read."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(302, headers={"location": "/robots-real.txt"})
        if request.url.path == "/robots-real.txt":
            return httpx.Response(
                200,
                headers={"content-type": "text/plain; charset=utf-8"},
                text="User-agent: *\nDisallow: /private\n",
            )
        return httpx.Response(404, headers={"content-type": "text/html"}, text="missing")

    _install_transport(monkeypatch, httpx.MockTransport(handler))
    scraper = WebScraper()

    found = await scraper.fetch_raw("https://example.com/robots.txt", max_bytes=1000)
    missing = await scraper.fetch_raw("https://example.com/nope", max_bytes=1000)

    assert found is not None
    assert found.status_code == 200
    assert found.content_type == "text/plain"
    assert found.final_url == "https://example.com/robots-real.txt"
    assert found.body.startswith(b"User-agent: *")
    assert missing is not None
    assert missing.status_code == 404
    assert missing.body == b""


@pytest.mark.asyncio
async def test_fetch_raw_blocked_and_oversized_return_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unsafe address and an over-cap body both come back as ``None``."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"x" * 500)

    _install_transport(monkeypatch, httpx.MockTransport(handler))
    scraper = WebScraper()

    assert await scraper.fetch_raw("https://example.com/big", max_bytes=100) is None
    monkeypatch.setattr(_search_mod, "resolve_pinned_ip", lambda *a, **kw: None)
    assert await scraper.fetch_raw("https://example.com/big", max_bytes=1000) is None
