# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the shared embedding HTTP retry helper (``_retry.py``).

Focuses on the ``Retry-After`` header handling: a hostile or misbehaving
endpoint must not be able to pin the concurrency-1 embedding slot asleep for
an unbounded duration, and a non-numeric (HTTP-date) header must not crash the
retry loop.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.adapters.embedding._retry import request_with_retry
from chaoscypher_core.app_config import get_settings


def _response(status_code: int, *, retry_after: str | None = None) -> MagicMock:
    """Build a minimal httpx.Response stand-in for the retry helper."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = ""
    headers: dict[str, str] = {}
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    resp.headers = headers
    return resp


class TestRetryAfterBounds:
    """The ``Retry-After`` header must be bounded and crash-proof."""

    @pytest.mark.asyncio
    async def test_retry_after_header_clamped_to_max_backoff(self) -> None:
        """A huge ``Retry-After`` sleeps at most ``backoff.max_seconds``."""
        request_fn = AsyncMock(side_effect=[_response(429, retry_after="86400"), _response(200)])
        slept: list[float] = []

        async def _record_sleep(seconds: float) -> None:
            slept.append(seconds)

        with patch(
            "chaoscypher_core.adapters.embedding._retry.asyncio.sleep",
            side_effect=_record_sleep,
        ):
            result = await request_with_retry(request_fn=request_fn, provider="openai")

        assert result.status_code == 200
        max_seconds = float(get_settings().backoff.max_seconds)
        assert slept == [max_seconds]

    @pytest.mark.asyncio
    async def test_non_numeric_retry_after_falls_back_to_exponential(self) -> None:
        """An HTTP-date ``Retry-After`` must not crash; falls back to backoff."""
        request_fn = AsyncMock(
            side_effect=[
                _response(429, retry_after="Wed, 21 Oct 2025 07:28:00 GMT"),
                _response(200),
            ]
        )
        slept: list[float] = []

        async def _record_sleep(seconds: float) -> None:
            slept.append(seconds)

        with patch(
            "chaoscypher_core.adapters.embedding._retry.asyncio.sleep",
            side_effect=_record_sleep,
        ):
            result = await request_with_retry(request_fn=request_fn, provider="openai")

        assert result.status_code == 200
        settings = get_settings()
        expected = settings.retries.embedding_initial_backoff_seconds * (
            settings.backoff.exponential_multiplier**0
        )
        assert slept == [expected]
