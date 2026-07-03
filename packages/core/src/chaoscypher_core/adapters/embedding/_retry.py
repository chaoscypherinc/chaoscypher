# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared HTTP retry helper for embedding providers.

Centralizes the exponential-backoff retry loop used by OpenAI, Ollama, and
Gemini embedding providers. Handles 5xx, 429 rate limits (with optional
``Retry-After`` header), connection errors, and timeouts.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable

import httpx
import structlog

from chaoscypher_core.exceptions import LLMError


logger = structlog.get_logger(__name__)


def _get_retry_defaults() -> tuple[int, float]:
    """Resolve embedding retry defaults from the ``RetrySettings`` class.

    Returns ``(max_retries, initial_backoff_seconds)``. This leaf helper has
    no object context at most call sites, so it reads the class defaults
    rather than the app singleton. Callers that hold engine settings inject
    explicit values through ``request_with_retry``.
    """
    from chaoscypher_core.settings import RetrySettings

    s = RetrySettings()
    return (s.embedding_max_retries, s.embedding_initial_backoff_seconds)


def _get_backoff_multiplier() -> float:
    """Resolve the exponential backoff multiplier from ``BackoffSettings``."""
    from chaoscypher_core.settings import BackoffSettings

    return BackoffSettings().exponential_multiplier


def _get_max_backoff() -> float:
    """Resolve the maximum backoff cap (seconds) from ``BackoffSettings``."""
    from chaoscypher_core.settings import BackoffSettings

    return float(BackoffSettings().max_seconds)


def _jittered_backoff(
    attempt: int, initial_backoff: float, multiplier: float, max_backoff: float
) -> float:
    """Exponential backoff for ``attempt`` (0-based), clamped to ``max_backoff``.

    Applies 50-150% random jitter so multiple embedding requests that fail at the
    same time (e.g. a wave of parallel batches in ``services/search/engine/index``
    all hitting a provider outage) don't retry in lockstep and re-stampede the
    provider. Mirrors the queue worker's retry jitter (``queue/worker.py``).
    """
    base = min(initial_backoff * (multiplier**attempt), max_backoff)
    return base * (0.5 + random.random())  # noqa: S311 — non-crypto jitter


def _resolve_retry_wait(retry_after: str | None, fallback: float, max_backoff: float) -> float:
    """Resolve sleep seconds from a ``Retry-After`` header, bounded by ``max_backoff``.

    A numeric (delta-seconds) header is clamped to ``max_backoff`` so a hostile or
    misbehaving endpoint returning e.g. ``Retry-After: 86400`` can't pin the
    concurrency-1 embedding slot asleep for a day. A missing or non-numeric
    (HTTP-date form) header falls back to the exponential backoff value.
    """
    if not retry_after:
        return fallback
    try:
        seconds = float(retry_after)
    except ValueError:
        # Retry-After may be an HTTP-date rather than delta-seconds; don't crash.
        return fallback
    return min(seconds, max_backoff)


async def request_with_retry(
    *,
    request_fn: Callable[[], Awaitable[httpx.Response]],
    provider: str,
    auth_error_codes: tuple[int, ...] = (),
    max_retries: int | None = None,
    initial_backoff: float | None = None,
    exponential_multiplier: float | None = None,
    max_backoff: float | None = None,
) -> httpx.Response:
    """Execute an HTTP request with exponential-backoff retry.

    Retries on 429, 5xx, ``httpx.ConnectError``, and ``httpx.TimeoutException``.
    Honors ``Retry-After`` header when present on 429/5xx responses.

    Args:
        request_fn: Zero-arg async callable that performs the HTTP request and
            returns an ``httpx.Response``. Called fresh on each retry.
        provider: Provider name for structured logging (e.g. "openai").
        auth_error_codes: HTTP status codes that should raise ``LLMError``
            immediately without retry (e.g. ``(401, 403)``).
        max_retries: Maximum retry attempts. ``None`` (default) resolves to
            ``RetrySettings().embedding_max_retries`` (the class default).
        initial_backoff: Initial backoff in seconds; multiplied by
            ``exponential_multiplier ** attempt`` each attempt. ``None``
            (default) resolves to
            ``RetrySettings().embedding_initial_backoff_seconds``.
        exponential_multiplier: Per-attempt growth factor. ``None`` (default)
            resolves to ``BackoffSettings().exponential_multiplier``.
        max_backoff: Upper bound (seconds) on any single sleep, including a
            ``Retry-After`` header value. ``None`` (default) resolves to
            ``BackoffSettings().max_seconds``.

    Returns:
        The successful HTTP response (status not in retryable set).

    Raises:
        LLMError: If retries are exhausted, an auth error is hit, or a
            non-retryable HTTP error is raised by httpx.
    """
    if max_retries is None or initial_backoff is None:
        default_retries, default_backoff = _get_retry_defaults()
        if max_retries is None:
            max_retries = default_retries
        if initial_backoff is None:
            initial_backoff = default_backoff
    if exponential_multiplier is None:
        exponential_multiplier = _get_backoff_multiplier()
    if max_backoff is None:
        max_backoff = _get_max_backoff()
    backoff_multiplier = exponential_multiplier
    last_error: Exception | None = None

    for attempt in range(max_retries):
        try:
            response = await request_fn()

            if response.status_code in auth_error_codes:
                msg = f"{provider} authentication failed ({response.status_code}): {response.text}"
                raise LLMError(msg)

            if response.status_code == 429 or response.status_code >= 500:
                last_error = LLMError(
                    f"{provider} server error {response.status_code}: {response.text}"
                )
                logger.warning(
                    "embedding_provider_retryable_error",
                    provider=provider,
                    status=response.status_code,
                    attempt=attempt + 1,
                )
                wait = _resolve_retry_wait(
                    response.headers.get("Retry-After"),
                    fallback=initial_backoff * (backoff_multiplier**attempt),
                    max_backoff=max_backoff,
                )
                await asyncio.sleep(wait)
                continue

            return response

        except httpx.ConnectError as e:
            last_error = e
            logger.warning(
                "embedding_provider_connect_error",
                provider=provider,
                attempt=attempt + 1,
                error=str(e),
            )
            await asyncio.sleep(
                _jittered_backoff(attempt, initial_backoff, backoff_multiplier, max_backoff)
            )

        except httpx.TimeoutException as e:
            last_error = e
            logger.warning(
                "embedding_provider_timeout",
                provider=provider,
                attempt=attempt + 1,
                error=str(e),
            )
            await asyncio.sleep(
                _jittered_backoff(attempt, initial_backoff, backoff_multiplier, max_backoff)
            )

        except LLMError:
            raise

        except httpx.HTTPError as e:
            msg = f"{provider} HTTP error: {e}"
            raise LLMError(msg) from e

    msg = f"{provider} request failed after {max_retries} retries: {last_error}"
    raise LLMError(msg) from last_error
