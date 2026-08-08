# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""WebScraper reads web settings from constructor injection, not the app singleton.

Tier 2 config-unification (2026-06): ``adapters/web/search.py`` no longer
reads the ``get_settings()`` app singleton. Instead the scraper takes an
optional ``web_settings: WebSettings`` constructor argument (defaulting to
``WebSettings()`` class defaults). This suite pins:

* The HTTP client timeout comes from the injected
  ``web_settings.fetch_timeout_seconds``.
* The redirect helpers bound their hop count by the injected
  ``web_settings.max_redirects``.
* The module has no ``get_settings`` symbol to read from anymore.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import httpx
import pytest

import chaoscypher_core.adapters.web.search as _search_mod
from chaoscypher_core.adapters.web.search import WebScraper
from chaoscypher_core.settings import WebSettings


def test_search_module_has_no_get_settings_reference() -> None:
    """The app-config singleton import is fully removed from the module."""
    assert not hasattr(_search_mod, "get_settings"), (
        "adapters/web/search.py must not import get_settings any more"
    )


def test_get_client_uses_injected_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_get_client(timeout_seconds=...)`` builds the client with that timeout."""
    # Reset the module-level cache so the call constructs a fresh client.
    monkeypatch.setattr(_search_mod, "_client", None)
    captured: dict[str, Any] = {}

    real_init = httpx.AsyncClient.__init__

    def spy_init(self: httpx.AsyncClient, *args: Any, **kwargs: Any) -> None:
        captured["timeout"] = kwargs.get("timeout")
        real_init(self, *args, **kwargs)

    with patch.object(httpx.AsyncClient, "__init__", spy_init):
        client = _search_mod._get_client(timeout_seconds=1.5)

    assert isinstance(client, httpx.AsyncClient)
    assert captured["timeout"] == 1.5


@pytest.mark.asyncio
async def test_redirect_helper_uses_injected_max_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The redirect loop honors ``web_settings.max_redirects`` (not the singleton)."""
    # A perpetual-redirect server: every GET 302s to a new safe URL.
    hop_count = {"n": 0}

    class RedirectClient:
        async def get(self, url: httpx.URL | str, **_kwargs: Any) -> httpx.Response:
            hop_count["n"] += 1
            return httpx.Response(
                302,
                request=httpx.Request("GET", url),
                headers={"location": f"https://example.com/hop{hop_count['n']}"},
            )

    monkeypatch.setattr(_search_mod, "_get_client", lambda timeout_seconds: RedirectClient())
    # Pin deterministically — the redirect loop dials resolve_pinned_ip's IP.
    monkeypatch.setattr(_search_mod, "resolve_pinned_ip", lambda *a, **kw: "93.184.216.34")

    scraper = WebScraper(web_settings=WebSettings(max_redirects=3))
    result = await scraper._fetch_with_redirect_validation("https://example.com/start")

    assert result is None  # exhausted the redirect budget
    assert hop_count["n"] == 3  # exactly max_redirects hops attempted


@pytest.mark.asyncio
async def test_capped_redirect_helper_uses_injected_max_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The capped redirect loop also honors the injected ``max_redirects``."""
    hop_count = {"n": 0}

    class RedirectClient:
        async def get(self, url: httpx.URL | str, **_kwargs: Any) -> httpx.Response:
            hop_count["n"] += 1
            return httpx.Response(
                302,
                request=httpx.Request("GET", url),
                headers={"location": f"https://example.com/hop{hop_count['n']}"},
            )

    monkeypatch.setattr(_search_mod, "_get_client", lambda timeout_seconds: RedirectClient())
    # Pin deterministically — the redirect loop dials resolve_pinned_ip's IP.
    monkeypatch.setattr(_search_mod, "resolve_pinned_ip", lambda *a, **kw: "93.184.216.34")

    scraper = WebScraper(web_settings=WebSettings(max_redirects=2))
    # max_bytes=None → legacy single-GET-per-hop path.
    result = await scraper._fetch_with_redirect_validation_capped("https://example.com/start", None)

    assert result is None
    assert hop_count["n"] == 2


def test_default_web_settings_when_none() -> None:
    """Omitting ``web_settings`` falls back to ``WebSettings()`` class defaults."""
    scraper = WebScraper()
    assert scraper._web_settings.fetch_timeout_seconds == WebSettings().fetch_timeout_seconds
    assert scraper._web_settings.max_redirects == WebSettings().max_redirects


@pytest.mark.asyncio
async def test_injected_timeout_threaded_into_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scraper passes its injected fetch timeout down to ``_get_client``."""
    seen: dict[str, float] = {}

    class FakeClient:
        async def get(self, url: httpx.URL | str, **_kwargs: Any) -> httpx.Response:
            return httpx.Response(200, request=httpx.Request("GET", url), text="ok")

    def fake_get_client(timeout_seconds: float) -> FakeClient:
        seen["timeout"] = timeout_seconds
        return FakeClient()

    monkeypatch.setattr(_search_mod, "_get_client", fake_get_client)
    # Pin deterministically — the redirect loop dials resolve_pinned_ip's IP.
    monkeypatch.setattr(_search_mod, "resolve_pinned_ip", lambda *a, **kw: "93.184.216.34")

    scraper = WebScraper(web_settings=WebSettings(fetch_timeout_seconds=7.0))
    await scraper._fetch_with_redirect_validation("https://example.com/")

    assert seen["timeout"] == 7.0


@pytest.mark.asyncio
async def test_fetch_dials_pinned_ip_with_host_header_and_sni(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fetch loop dials the vetted IP, not the hostname (DNS-rebinding fix).

    ``validate_url_safety`` alone left a TOCTOU: the vetted hostname was handed
    to httpx, which re-resolves DNS at connect time. The loop must dial the IP
    ``resolve_pinned_ip`` returned, carrying the original authority as the Host
    header and the hostname as TLS SNI (the http_request_plugin pattern).
    """
    seen: dict[str, Any] = {}

    class FakeClient:
        async def get(self, url: httpx.URL | str, **kwargs: Any) -> httpx.Response:
            seen["url"] = httpx.URL(url)
            seen["headers"] = kwargs.get("headers")
            seen["extensions"] = kwargs.get("extensions")
            return httpx.Response(200, request=httpx.Request("GET", url), text="ok")

    monkeypatch.setattr(_search_mod, "_get_client", lambda timeout_seconds: FakeClient())
    monkeypatch.setattr(_search_mod, "resolve_pinned_ip", lambda *a, **kw: "93.184.216.34")

    scraper = WebScraper()
    result = await scraper._fetch_with_redirect_validation("https://example.com/page")

    assert result == "ok"
    assert seen["url"].host == "93.184.216.34"
    assert seen["headers"]["Host"] == "example.com"
    assert seen["extensions"] == {"sni_hostname": "example.com"}


@pytest.mark.asyncio
async def test_fetch_blocked_when_pin_unresolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    """``resolve_pinned_ip`` returning None blocks the fetch before any dial."""
    dialed = {"n": 0}

    class FakeClient:
        async def get(self, url: httpx.URL | str, **_kwargs: Any) -> httpx.Response:
            dialed["n"] += 1
            return httpx.Response(200, request=httpx.Request("GET", url), text="ok")

    monkeypatch.setattr(_search_mod, "_get_client", lambda timeout_seconds: FakeClient())
    monkeypatch.setattr(_search_mod, "resolve_pinned_ip", lambda *a, **kw: None)

    scraper = WebScraper()
    assert await scraper._fetch_with_redirect_validation("https://blocked.internal/") is None
    assert (
        await scraper._fetch_with_redirect_validation_capped("https://blocked.internal/", 10)
        is None
    )
    assert dialed["n"] == 0
