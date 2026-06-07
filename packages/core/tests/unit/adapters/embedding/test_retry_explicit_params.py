# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Explicit-parameter / class-default behavior for ``_retry.py``.

Task C6 drops the direct ``app_config.get_settings()`` read from the leaf
embedding-retry helper. Resolution order is now:

1. An explicit keyword argument (when not ``None``), else
2. the matching ``RetrySettings()`` / ``BackoffSettings()`` class default.

These tests pin both halves: an injected value is honored verbatim, and the
``None`` fallback equals the class default (NOT whatever the app singleton
happens to hold).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.adapters.embedding._retry import request_with_retry
from chaoscypher_core.settings import BackoffSettings, RetrySettings


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


class TestExplicitParamsHonored:
    """Caller-supplied retry/backoff values must be used verbatim."""

    @pytest.mark.asyncio
    async def test_explicit_multiplier_drives_exponential_growth(self) -> None:
        """A custom ``exponential_multiplier`` produces that growth factor."""
        # Two 5xx responses then success → two sleeps, exponents 0 and 1.
        request_fn = AsyncMock(side_effect=[_response(500), _response(500), _response(200)])
        slept: list[float] = []

        async def _record_sleep(seconds: float) -> None:
            slept.append(seconds)

        with patch(
            "chaoscypher_core.adapters.embedding._retry.asyncio.sleep",
            side_effect=_record_sleep,
        ):
            result = await request_with_retry(
                request_fn=request_fn,
                provider="openai",
                max_retries=5,
                initial_backoff=1.0,
                exponential_multiplier=3.0,
                max_backoff=1000.0,
            )

        assert result.status_code == 200
        # 1.0 * 3**0, 1.0 * 3**1
        assert slept == [1.0, 3.0]

    @pytest.mark.asyncio
    async def test_explicit_max_backoff_clamps_retry_after(self) -> None:
        """A huge ``Retry-After`` is clamped to the explicit ``max_backoff``."""
        request_fn = AsyncMock(side_effect=[_response(429, retry_after="86400"), _response(200)])
        slept: list[float] = []

        async def _record_sleep(seconds: float) -> None:
            slept.append(seconds)

        with patch(
            "chaoscypher_core.adapters.embedding._retry.asyncio.sleep",
            side_effect=_record_sleep,
        ):
            result = await request_with_retry(
                request_fn=request_fn,
                provider="openai",
                max_backoff=7.0,
            )

        assert result.status_code == 200
        assert slept == [7.0]


class TestClassDefaultFallback:
    """``None`` resolves to the class default, not the app singleton."""

    @pytest.mark.asyncio
    async def test_none_uses_class_defaults_not_singleton(self) -> None:
        """Even when the singleton is overridden, ``None`` reads class defaults."""
        request_fn = AsyncMock(side_effect=[_response(500), _response(200)])
        slept: list[float] = []

        async def _record_sleep(seconds: float) -> None:
            slept.append(seconds)

        # A poisoned singleton must NOT influence the leaf helper anymore.
        poisoned = MagicMock()
        poisoned.retries.embedding_max_retries = 99
        poisoned.retries.embedding_initial_backoff_seconds = 123.0
        poisoned.backoff.exponential_multiplier = 9.0
        poisoned.backoff.max_seconds = 9999

        with (
            patch(
                "chaoscypher_core.adapters.embedding._retry.asyncio.sleep",
                side_effect=_record_sleep,
            ),
            patch(
                "chaoscypher_core.app_config.get_settings",
                return_value=poisoned,
            ),
        ):
            result = await request_with_retry(request_fn=request_fn, provider="openai")

        assert result.status_code == 200
        defaults_retry = RetrySettings()
        defaults_backoff = BackoffSettings()
        # First retry sleep = initial_backoff * multiplier**0 = class default.
        assert slept == [defaults_retry.embedding_initial_backoff_seconds]
        # Sanity: class defaults differ from the poisoned values we injected.
        assert defaults_retry.embedding_initial_backoff_seconds != 123.0
        assert defaults_backoff.exponential_multiplier != 9.0

    @pytest.mark.asyncio
    async def test_max_retries_default_exhaustion(self) -> None:
        """``max_retries=None`` falls back to ``RetrySettings().embedding_max_retries``."""
        default_retries = RetrySettings().embedding_max_retries
        request_fn = AsyncMock(side_effect=[_response(500)] * (default_retries + 5))

        async def _noop_sleep(_seconds: float) -> None:
            return None

        from chaoscypher_core.exceptions import LLMError

        with patch(
            "chaoscypher_core.adapters.embedding._retry.asyncio.sleep",
            side_effect=_noop_sleep,
        ):
            with pytest.raises(LLMError):
                await request_with_retry(request_fn=request_fn, provider="openai")

        # Exactly ``default_retries`` attempts were made.
        assert request_fn.call_count == default_retries
