# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Retry-path coverage for the shared embedding HTTP retry helper.

Complements ``test_retry.py`` (which focuses on Retry-After bounds) by
exercising:

* ``httpx.ConnectError`` → retry then succeed.
* ``httpx.TimeoutException`` → retry then succeed.
* An ``LLMError`` raised by the request function passes straight through.
* A generic ``httpx.HTTPError`` is wrapped in ``LLMError``.
* Exhausting all retries raises ``LLMError`` chained from the last error.

``asyncio.sleep`` is patched throughout so the backoff waits don't slow tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from chaoscypher_core.adapters.embedding._retry import request_with_retry
from chaoscypher_core.exceptions import LLMError


def _ok_response() -> MagicMock:
    """A successful (status 200) httpx.Response stand-in."""
    resp = MagicMock()
    resp.status_code = 200
    resp.text = ""
    resp.headers = {}
    return resp


@pytest.mark.asyncio
async def test_connect_error_retries_then_succeeds() -> None:
    """A ConnectError on the first attempt is retried; the second attempt wins."""
    request_fn = AsyncMock(side_effect=[httpx.ConnectError("conn refused"), _ok_response()])

    with patch(
        "chaoscypher_core.adapters.embedding._retry.asyncio.sleep",
        new=AsyncMock(),
    ):
        result = await request_with_retry(request_fn=request_fn, provider="ollama")

    assert result.status_code == 200
    assert request_fn.await_count == 2


@pytest.mark.asyncio
async def test_timeout_exception_retries_then_succeeds() -> None:
    """A TimeoutException on the first attempt is retried; the second succeeds."""
    request_fn = AsyncMock(side_effect=[httpx.TimeoutException("read timeout"), _ok_response()])

    with patch(
        "chaoscypher_core.adapters.embedding._retry.asyncio.sleep",
        new=AsyncMock(),
    ):
        result = await request_with_retry(request_fn=request_fn, provider="openai")

    assert result.status_code == 200
    assert request_fn.await_count == 2


@pytest.mark.asyncio
async def test_llmerror_passes_through_unwrapped() -> None:
    """An LLMError raised inside request_fn propagates without re-wrapping."""
    sentinel = LLMError("explicit auth failure")
    request_fn = AsyncMock(side_effect=sentinel)

    with patch(
        "chaoscypher_core.adapters.embedding._retry.asyncio.sleep",
        new=AsyncMock(),
    ):
        with pytest.raises(LLMError) as exc_info:
            await request_with_retry(request_fn=request_fn, provider="openai")

    assert exc_info.value is sentinel


@pytest.mark.asyncio
async def test_generic_http_error_wrapped_in_llmerror() -> None:
    """A non-retryable httpx.HTTPError is wrapped into an LLMError."""
    request_fn = AsyncMock(side_effect=httpx.HTTPError("malformed url"))

    with patch(
        "chaoscypher_core.adapters.embedding._retry.asyncio.sleep",
        new=AsyncMock(),
    ):
        with pytest.raises(LLMError, match="HTTP error"):
            await request_with_retry(request_fn=request_fn, provider="gemini")


@pytest.mark.asyncio
async def test_retries_exhausted_raises_llmerror() -> None:
    """When every attempt raises a retryable error, the loop ends in LLMError."""
    request_fn = AsyncMock(side_effect=httpx.ConnectError("conn refused"))

    with patch(
        "chaoscypher_core.adapters.embedding._retry.asyncio.sleep",
        new=AsyncMock(),
    ):
        with pytest.raises(LLMError, match="request failed after"):
            await request_with_retry(
                request_fn=request_fn,
                provider="ollama",
                max_retries=3,
                initial_backoff=0.01,
            )

    assert request_fn.await_count == 3


@pytest.mark.asyncio
async def test_auth_error_code_raises_immediately_without_retry() -> None:
    """A status in auth_error_codes raises LLMError on the first response."""
    resp = MagicMock()
    resp.status_code = 401
    resp.text = "unauthorized"
    resp.headers = {}
    request_fn = AsyncMock(return_value=resp)

    with patch(
        "chaoscypher_core.adapters.embedding._retry.asyncio.sleep",
        new=AsyncMock(),
    ):
        with pytest.raises(LLMError, match="authentication failed"):
            await request_with_retry(
                request_fn=request_fn,
                provider="openai",
                auth_error_codes=(401, 403),
            )

    assert request_fn.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError("conn refused"), httpx.TimeoutException("read timeout")],
)
async def test_connect_and_timeout_backoff_is_jittered(error: httpx.HTTPError) -> None:
    """ConnectError / TimeoutException backoff applies 50-150% jitter, not a
    bare exponential — so a wave of simultaneous failures doesn't retry in
    lockstep. With ``random.random()`` pinned to 0.0 the factor is exactly 0.5,
    so the first sleep is ``initial_backoff * 0.5``, never the un-jittered value.
    """
    request_fn = AsyncMock(side_effect=[error, _ok_response()])
    sleep_mock = AsyncMock()

    with (
        patch("chaoscypher_core.adapters.embedding._retry.asyncio.sleep", new=sleep_mock),
        patch("chaoscypher_core.adapters.embedding._retry.random.random", return_value=0.0),
    ):
        result = await request_with_retry(
            request_fn=request_fn,
            provider="ollama",
            initial_backoff=4.0,
            exponential_multiplier=3.0,
            max_backoff=100.0,
        )

    assert result.status_code == 200
    # base = min(4.0 * 3**0, 100) = 4.0; jitter factor = 0.5 + 0.0 = 0.5.
    sleep_mock.assert_awaited_once_with(2.0)
