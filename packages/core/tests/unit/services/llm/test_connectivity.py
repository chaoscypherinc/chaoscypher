# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit coverage for the async cloud-LLM connectivity probes.

Added by the 2026-07-23 llm section-audit: connectivity.py previously had
no core tests (cortex mocks ``verify_cloud_key`` at its boundary), so the
status-code mapping and the Gemini key-in-header fix were unpinned.
"""

from __future__ import annotations

import httpx
import pytest

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.services.llm import connectivity


_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _client_factory(handler):
    """Build an AsyncClient factory that routes through a MockTransport.

    Uses the captured real class — monkeypatching ``connectivity.httpx``
    patches the global module, so calling ``httpx.AsyncClient`` inside the
    factory would recurse.
    """

    def factory(**kwargs):
        kwargs.pop("transport", None)
        return _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kwargs)

    return factory


@pytest.mark.asyncio
async def test_gemini_key_sent_in_header_not_query_string(monkeypatch) -> None:
    """The Gemini key must travel in x-goog-api-key, never the URL.

    Regression: the key used to ride the query string, where httpx error
    stringification (and proxy access logs) could leak it.
    """
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key_header"] = request.headers.get("x-goog-api-key", "")
        return httpx.Response(200)

    monkeypatch.setattr(connectivity.httpx, "AsyncClient", _client_factory(handler))

    ok, message = await connectivity.verify_gemini_key("sk-test-secret")

    assert ok is True
    assert message == "API key valid"
    assert seen["key_header"] == "sk-test-secret"
    assert "sk-test-secret" not in seen["url"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_ok", "expected_message"),
    [
        (200, True, "API key valid"),
        (400, False, "Invalid API key"),
        (401, False, "Invalid API key"),
        (403, False, "Invalid API key"),
        (500, False, "Unexpected status: 500"),
    ],
)
async def test_gemini_status_mapping(
    monkeypatch, status: int, expected_ok: bool, expected_message: str
) -> None:
    monkeypatch.setattr(
        connectivity.httpx,
        "AsyncClient",
        _client_factory(lambda request: httpx.Response(status)),
    )

    ok, message = await connectivity.verify_gemini_key("k")
    assert (ok, message) == (expected_ok, expected_message)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_ok", "expected_message"),
    [
        (200, True, "API key valid"),
        (401, False, "Invalid API key"),
        (429, False, "Unexpected status: 429"),
    ],
)
async def test_openai_status_mapping(
    monkeypatch, status: int, expected_ok: bool, expected_message: str
) -> None:
    monkeypatch.setattr(
        connectivity.httpx,
        "AsyncClient",
        _client_factory(lambda request: httpx.Response(status)),
    )

    ok, message = await connectivity.verify_openai_key("k")
    assert (ok, message) == (expected_ok, expected_message)


@pytest.mark.asyncio
async def test_timeout_maps_to_friendly_message(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("boom", request=request)

    monkeypatch.setattr(connectivity.httpx, "AsyncClient", _client_factory(handler))

    ok, message = await connectivity.verify_anthropic_key("k")
    assert ok is False
    assert message == "Connection timed out"


@pytest.mark.asyncio
async def test_dispatch_unknown_provider_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        await connectivity.verify_cloud_key("nope", "k")


@pytest.mark.asyncio
async def test_dispatch_routes_to_gemini(monkeypatch) -> None:
    monkeypatch.setattr(
        connectivity.httpx,
        "AsyncClient",
        _client_factory(lambda request: httpx.Response(200)),
    )

    ok, _ = await connectivity.verify_cloud_key("gemini", "k")
    assert ok is True
