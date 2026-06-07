# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: WebScraper.extract_full_content respects max_bytes cap."""

from __future__ import annotations

import httpx
import pytest

from chaoscypher_core.adapters.web.search import WebScraper
from chaoscypher_core.exceptions import MaxBytesExceeded


def _stub_response(body: bytes, content_type: str = "text/html") -> httpx.Response:
    """Build an ``httpx.Response`` matching the new ``_capped`` contract."""
    return httpx.Response(
        200,
        request=httpx.Request("GET", "https://example.com/page"),
        headers={"content-type": content_type},
        content=body,
    )


@pytest.mark.asyncio
async def test_max_bytes_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    """When max_bytes is exceeded mid-stream, surface the error in FetchResult.error."""

    async def fake_capped(self: WebScraper, url: str, max_bytes: int | None) -> httpx.Response:
        raise MaxBytesExceeded(f"Content exceeded max_bytes={max_bytes}")

    monkeypatch.setattr(
        WebScraper,
        "_fetch_with_redirect_validation_capped",
        fake_capped,
    )

    scraper = WebScraper()
    result = await scraper.extract_full_content("https://example.com/big", max_bytes=100)

    assert result.error is not None
    err = result.error.lower()
    assert "max_bytes" in err or "too large" in err or "100" in err


@pytest.mark.asyncio
async def test_max_bytes_none_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """max_bytes=None passes None into the fetch helper (unlimited, legacy default)."""
    calls: list[int | None] = []

    async def recording_capped(self: WebScraper, url: str, max_bytes: int | None) -> httpx.Response:
        calls.append(max_bytes)
        # Return a minimal HTML page so trafilatura can extract something.
        return _stub_response(b"<html><body><p>Hello world, this is content.</p></body></html>")

    monkeypatch.setattr(
        WebScraper,
        "_fetch_with_redirect_validation_capped",
        recording_capped,
    )

    scraper = WebScraper()
    # No max_bytes argument — should default to None.
    await scraper.extract_full_content("https://example.com/page")

    assert len(calls) == 1
    assert calls[0] is None


@pytest.mark.asyncio
async def test_max_bytes_value_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    """The max_bytes value is forwarded verbatim to the fetch helper."""
    calls: list[int | None] = []

    async def recording_capped(self: WebScraper, url: str, max_bytes: int | None) -> httpx.Response:
        calls.append(max_bytes)
        return _stub_response(b"<html><body><p>Hello world, this is content.</p></body></html>")

    monkeypatch.setattr(
        WebScraper,
        "_fetch_with_redirect_validation_capped",
        recording_capped,
    )

    scraper = WebScraper()
    await scraper.extract_full_content("https://example.com/page", max_bytes=1024)

    assert len(calls) == 1
    assert calls[0] == 1024
