# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Generic retry helpers for operations that hit SQLITE_BUSY.

Cluster A's Unit-of-Work refactor moved every write into long-lived
``adapter.transaction()`` blocks. ``SafeSession.commit()`` has its own
retry layer, but it only fires on the final ``commit()`` call — not on
``session.execute()`` / ``flush()`` calls inside the transaction. Under
concurrent read load, intermediate DELETEs and INSERTs can hit
``database is locked`` and raise immediately.

This module provides sync and async retry wrappers that re-execute an
entire idempotent operation (commit, delete_source, etc.) when it
raises a lock error. The operation MUST be idempotent — retrying from
scratch runs cleanup + writes again. Cluster A and F already built
idempotency into the commit and delete paths.

Usage:
    # Async:
    result = await retry_on_db_lock_async(
        self._commit_impl,
        file_id=file_id,
        commit_data=commit_data,
        operation_name="commit",
    )

    # Sync:
    result = retry_on_db_lock_sync(
        self._delete_impl,
        source_id=source_id,
        operation_name="delete_source",
    )

    # Decorator variants also provided.
"""

from __future__ import annotations

import asyncio
import functools
import time
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from sqlalchemy.exc import OperationalError


logger = structlog.get_logger(__name__)

__all__ = [
    "DEFAULT_BASE_DELAY",
    "DEFAULT_MAX_DELAY",
    "DEFAULT_MAX_RETRIES",
    "DbLockRetryPolicy",
    "is_sqlite_lock_error",
    "retry_on_db_lock_async",
    "retry_on_db_lock_async_decorator",
    "retry_on_db_lock_sync",
    "retry_on_db_lock_sync_decorator",
]

# Retry window tuned for real-world contention from the dashboard's
# ~2s polling cycle. With the defaults below the worst-case wait
# before the final raise is ~45s:
#     0.5, 1.0, 2.0, 4.0, 8.0, 10.0, 10.0, 10.0  =  45.5s
# The 10s cap prevents runaway exponential growth on sustained
# contention. Under a healthy system, the first retry typically
# succeeds after one or two attempts.
DEFAULT_MAX_RETRIES = 8
DEFAULT_BASE_DELAY = 0.5
DEFAULT_MAX_DELAY = 10.0


def _backoff_delay(
    attempt: int,
    base_delay: float,
    max_delay: float,
    exponential_multiplier: float | None = None,
) -> float:
    """Exponential backoff with a cap.

    Args:
        attempt: Zero-indexed attempt number.
        base_delay: Base delay in seconds (delay for attempt 0).
        max_delay: Upper bound on any single sleep (seconds).
        exponential_multiplier: Per-attempt growth factor. ``None`` (default)
            resolves to ``BackoffSettings().exponential_multiplier`` (the class
            default) — this leaf helper has no object context at most call
            sites; callers holding engine settings inject the value.

    Returns:
        Seconds to sleep before the next attempt.
    """
    if exponential_multiplier is None:
        # Lazy import to avoid circular import (this module is reachable
        # via chaoscypher_core.utils.__init__ during app_config loading).
        from chaoscypher_core.settings import BackoffSettings

        exponential_multiplier = BackoffSettings().exponential_multiplier
    return float(min(base_delay * (exponential_multiplier**attempt), max_delay))


def is_sqlite_lock_error(exc: BaseException) -> bool:
    """Return True if this looks like SQLITE_BUSY / database-is-locked.

    Checks both ``OperationalError`` instances (SQLAlchemy) and plain
    exceptions with matching messages. Errors from WAL contention,
    busy_timeout expiry, and foreign-key lock conflicts all surface the
    same text in SQLite.

    Args:
        exc: Exception to inspect.

    Returns:
        True if the exception message indicates a SQLite lock error.

    """
    if not isinstance(exc, OperationalError):
        # Some drivers wrap or re-raise differently; still inspect the
        # message as a fallback.
        msg = str(exc).lower()
        return "database is locked" in msg or "sqlite_busy" in msg

    msg = str(exc).lower()
    return "database is locked" in msg or "sqlite_busy" in msg


async def retry_on_db_lock_async[T](
    fn: Callable[..., Awaitable[T]],
    *args: Any,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    operation_name: str = "transaction",
    **kwargs: Any,
) -> T:
    """Run an awaitable with exponential backoff retry on SQLITE_BUSY.

    The callee MUST be idempotent — retry re-executes the entire
    operation from scratch. Cluster A's commit and Cluster F's delete
    were built for this.

    Non-lock exceptions propagate immediately. Lock errors retry up to
    ``max_retries - 1`` times before the final raise.

    Args:
        fn: Async callable to invoke.
        *args: Positional arguments forwarded to ``fn``.
        max_retries: Maximum number of attempts (default 5).
        base_delay: Base backoff delay in seconds; doubles each attempt.
        max_delay: Upper bound on any single sleep (seconds).
        operation_name: Label used in log messages.
        **kwargs: Keyword arguments forwarded to ``fn``.

    Returns:
        Return value of ``fn`` on success.

    Raises:
        OperationalError: After ``max_retries`` failed lock attempts.
        Exception: Any non-lock exception from ``fn`` propagates immediately.

    """
    last_exc: BaseException | None = None
    for attempt in range(max_retries):
        try:
            return await fn(*args, **kwargs)
        except OperationalError as exc:
            last_exc = exc
            if not is_sqlite_lock_error(exc):
                raise
            if attempt >= max_retries - 1:
                logger.exception(
                    "db_lock_retry_exhausted",
                    operation=operation_name,
                    attempts=max_retries,
                )
                raise
            delay = _backoff_delay(attempt, base_delay, max_delay)
            logger.warning(
                "db_lock_retry",
                operation=operation_name,
                attempt=attempt + 1,
                max_retries=max_retries,
                delay_seconds=delay,
            )
            await asyncio.sleep(delay)

    # Defensive: loop always either returns or raises above
    assert last_exc is not None
    raise last_exc


def retry_on_db_lock_sync[T](
    fn: Callable[..., T],
    *args: Any,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    operation_name: str = "transaction",
    **kwargs: Any,
) -> T:
    """Sync counterpart of :func:`retry_on_db_lock_async`.

    Args:
        fn: Sync callable to invoke.
        *args: Positional arguments forwarded to ``fn``.
        max_retries: Maximum number of attempts (default 5).
        base_delay: Base backoff delay in seconds; doubles each attempt.
        max_delay: Upper bound on any single sleep (seconds).
        operation_name: Label used in log messages.
        **kwargs: Keyword arguments forwarded to ``fn``.

    Returns:
        Return value of ``fn`` on success.

    Raises:
        OperationalError: After ``max_retries`` failed lock attempts.
        Exception: Any non-lock exception from ``fn`` propagates immediately.

    """
    last_exc: BaseException | None = None
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except OperationalError as exc:
            last_exc = exc
            if not is_sqlite_lock_error(exc):
                raise
            if attempt >= max_retries - 1:
                logger.exception(
                    "db_lock_retry_exhausted",
                    operation=operation_name,
                    attempts=max_retries,
                )
                raise
            delay = _backoff_delay(attempt, base_delay, max_delay)
            logger.warning(
                "db_lock_retry",
                operation=operation_name,
                attempt=attempt + 1,
                max_retries=max_retries,
                delay_seconds=delay,
            )
            time.sleep(delay)

    assert last_exc is not None
    raise last_exc


def retry_on_db_lock_async_decorator[T](
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    operation_name: str | None = None,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Decorator variant for async methods.

    Args:
        max_retries: Maximum number of attempts (default 5).
        base_delay: Base backoff delay in seconds; doubles each attempt.
        operation_name: Label used in log messages; defaults to the
            decorated function's ``__qualname__``.

    Returns:
        Decorator that wraps the async function with retry logic.

    """

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        name = operation_name or fn.__qualname__

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            """Invoke the wrapped function under the db-lock retry policy."""
            return await retry_on_db_lock_async(
                fn,
                *args,
                max_retries=max_retries,
                base_delay=base_delay,
                operation_name=name,
                **kwargs,
            )

        return wrapper

    return decorator


def retry_on_db_lock_sync_decorator[T](
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    operation_name: str | None = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator variant for sync functions.

    Args:
        max_retries: Maximum number of attempts (default 5).
        base_delay: Base backoff delay in seconds; doubles each attempt.
        operation_name: Label used in log messages; defaults to the
            decorated function's ``__qualname__``.

    Returns:
        Decorator that wraps the sync function with retry logic.

    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        name = operation_name or fn.__qualname__

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            return retry_on_db_lock_sync(
                fn,
                *args,
                max_retries=max_retries,
                base_delay=base_delay,
                operation_name=name,
                **kwargs,
            )

        return wrapper

    return decorator


class DbLockRetryPolicy:
    """Default retry policy: retries on SQLite "database is locked" errors.

    Wraps the existing ``retry_on_db_lock_sync`` / ``retry_on_db_lock_async``
    function helpers. Services accept this type (or any ``RetryPolicyPort``)
    via DI instead of importing the helpers directly.

    Satisfies ``RetryPolicyPort`` structurally — no explicit inheritance
    required.
    """

    def __init__(
        self,
        max_retries: int = DEFAULT_MAX_RETRIES,
        base_delay: float = DEFAULT_BASE_DELAY,
        max_delay: float = DEFAULT_MAX_DELAY,
    ) -> None:
        """Initialize the retry policy.

        Args:
            max_retries: Maximum retry attempts (default from module constant).
            base_delay: Initial backoff delay in seconds.
            max_delay: Upper bound for backoff delay.

        """
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay

    def run_sync(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        """Run ``fn(*args, **kwargs)`` under SQLite-lock retry.

        Args:
            fn: Sync callable to invoke.
            *args: Positional arguments forwarded to ``fn``.
            **kwargs: Keyword arguments forwarded to ``fn``.

        Returns:
            Return value of ``fn`` on success.

        """
        return retry_on_db_lock_sync(
            fn,
            *args,
            max_retries=self.max_retries,
            base_delay=self.base_delay,
            max_delay=self.max_delay,
            **kwargs,
        )

    async def run_async(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        """Await ``fn(*args, **kwargs)`` under SQLite-lock retry.

        Args:
            fn: Async callable to invoke.
            *args: Positional arguments forwarded to ``fn``.
            **kwargs: Keyword arguments forwarded to ``fn``.

        Returns:
            Return value of ``fn`` on success.

        """
        return await retry_on_db_lock_async(
            fn,
            *args,
            max_retries=self.max_retries,
            base_delay=self.base_delay,
            max_delay=self.max_delay,
            **kwargs,
        )
