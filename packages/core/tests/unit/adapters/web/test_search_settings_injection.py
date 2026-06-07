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
        async def get(self, url: str) -> httpx.Response:
            hop_count["n"] += 1
            return httpx.Response(
                302,
                request=httpx.Request("GET", url),
                headers={"location": f"https://example.com/hop{hop_count['n']}"},
            )

    monkeypatch.setattr(_search_mod, "_get_client", lambda timeout_seconds: RedirectClient())
    monkeypatch.setattr(_search_mod, "validate_url_safety", lambda *a, **kw: True)

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
        async def get(self, url: str) -> httpx.Response:
            hop_count["n"] += 1
            return httpx.Response(
                302,
                request=httpx.Request("GET", url),
                headers={"location": f"https://example.com/hop{hop_count['n']}"},
            )

    monkeypatch.setattr(_search_mod, "_get_client", lambda timeout_seconds: RedirectClient())
    monkeypatch.setattr(_search_mod, "validate_url_safety", lambda *a, **kw: True)

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
        async def get(self, url: str) -> httpx.Response:
            return httpx.Response(200, request=httpx.Request("GET", url), text="ok")

    def fake_get_client(timeout_seconds: float) -> FakeClient:
        seen["timeout"] = timeout_seconds
        return FakeClient()

    monkeypatch.setattr(_search_mod, "_get_client", fake_get_client)
    monkeypatch.setattr(_search_mod, "validate_url_safety", lambda *a, **kw: True)

    scraper = WebScraper(web_settings=WebSettings(fetch_timeout_seconds=7.0))
    await scraper._fetch_with_redirect_validation("https://example.com/")

    assert seen["timeout"] == 7.0
