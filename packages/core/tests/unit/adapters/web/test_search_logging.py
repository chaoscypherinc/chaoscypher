# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: 4xx/5xx URL fetches must log the status code (audit fix M2)."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import structlog

import chaoscypher_core.adapters.web.search as _search_mod
from chaoscypher_core.adapters.web.search import WebScraper


def _make_fake_client(response: httpx.Response) -> type:
    """Return a FakeClient class whose .get() always yields *response*."""

    class FakeClient:
        def __init__(self, *a: object, **kw: object) -> None:
            pass

        async def get(self, url: str) -> httpx.Response:
            return response

        async def stream(self, method: str, url: str) -> None:  # pragma: no cover
            pass

    return FakeClient


@pytest.mark.asyncio
async def test_http_4xx_logged_with_status_code() -> None:
    response = httpx.Response(404, request=httpx.Request("GET", "https://example.com/"))
    fake_instance = _make_fake_client(response)()

    # Reset the module-level cached client so _get_client() creates a fresh one
    # via the patched httpx.AsyncClient, rather than reusing a stale instance.
    with patch.object(_search_mod, "_client", fake_instance):
        scraper = WebScraper()
        with structlog.testing.capture_logs() as logs:
            result = await scraper._fetch_with_redirect_validation("https://example.com/")

    assert result is None
    assert any(
        rec.get("event") == "url_fetch_http_error" and rec.get("status_code") == 404 for rec in logs
    ), f"expected url_fetch_http_error with status_code=404, got: {logs}"


@pytest.mark.asyncio
async def test_http_5xx_logged_with_status_code() -> None:
    response = httpx.Response(503, request=httpx.Request("GET", "https://example.com/"))
    fake_instance = _make_fake_client(response)()

    # Reset the module-level cached client so _get_client() creates a fresh one
    # via the patched httpx.AsyncClient, rather than reusing a stale instance.
    with patch.object(_search_mod, "_client", fake_instance):
        scraper = WebScraper()
        with structlog.testing.capture_logs() as logs:
            result = await scraper._fetch_with_redirect_validation("https://example.com/")

    assert result is None
    assert any(
        rec.get("event") == "url_fetch_http_error" and rec.get("status_code") == 503 for rec in logs
    ), f"expected url_fetch_http_error with status_code=503, got: {logs}"
