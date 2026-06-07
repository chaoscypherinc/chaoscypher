# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the retry-on-db-lock helper."""

import asyncio

import pytest
from sqlalchemy.exc import OperationalError

from chaoscypher_core.utils.retry import (
    is_sqlite_lock_error,
    retry_on_db_lock_async,
    retry_on_db_lock_sync,
)


# --- is_sqlite_lock_error ---


def test_detects_database_is_locked() -> None:
    """OperationalError with 'database is locked' message is detected."""
    exc = OperationalError("", {}, Exception("database is locked"))
    assert is_sqlite_lock_error(exc)


def test_detects_sqlite_busy() -> None:
    """OperationalError with 'SQLITE_BUSY' in message is detected."""
    exc = OperationalError("", {}, Exception("SQLITE_BUSY something"))
    assert is_sqlite_lock_error(exc)


def test_non_lock_operational_error_not_detected() -> None:
    """OperationalError unrelated to locking is not detected as a lock error."""
    exc = OperationalError("", {}, Exception("table not found"))
    assert not is_sqlite_lock_error(exc)


def test_non_operational_error_message_check() -> None:
    """Non-SQLAlchemy exceptions with lock text are detected via fallback."""
    exc = RuntimeError("database is locked")
    assert is_sqlite_lock_error(exc)  # fallback for wrapped exceptions


def test_unrelated_exception_not_detected() -> None:
    """Unrelated exceptions are not detected as lock errors."""
    assert not is_sqlite_lock_error(ValueError("oops"))


# --- retry_on_db_lock_async ---


@pytest.mark.asyncio
async def test_async_returns_immediately_on_success() -> None:
    """No retries occur when the callable succeeds on first attempt."""
    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    result = await retry_on_db_lock_async(fn, max_retries=5, base_delay=0.001)
    assert result == "ok"
    assert calls == 1


@pytest.mark.asyncio
async def test_async_retries_then_succeeds() -> None:
    """Callable succeeds after two lock errors; result is returned."""
    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise OperationalError("", {}, Exception("database is locked"))
        return "finally"

    result = await retry_on_db_lock_async(fn, max_retries=5, base_delay=0.001)
    assert result == "finally"
    assert calls == 3


@pytest.mark.asyncio
async def test_async_exhausts_retries_and_raises() -> None:
    """OperationalError is raised after max_retries consecutive lock errors."""
    calls = 0

    async def fn() -> None:
        nonlocal calls
        calls += 1
        raise OperationalError("", {}, Exception("database is locked"))

    with pytest.raises(OperationalError):
        await retry_on_db_lock_async(fn, max_retries=3, base_delay=0.001)
    assert calls == 3


@pytest.mark.asyncio
async def test_async_non_lock_error_not_retried() -> None:
    """Non-lock OperationalError propagates immediately without retry."""
    calls = 0

    async def fn() -> None:
        nonlocal calls
        calls += 1
        raise OperationalError("", {}, Exception("table not found"))

    with pytest.raises(OperationalError):
        await retry_on_db_lock_async(fn, max_retries=5, base_delay=0.001)
    assert calls == 1  # no retry


@pytest.mark.asyncio
async def test_async_forwards_args_and_kwargs() -> None:
    """Positional and keyword arguments are forwarded to the callable."""

    async def fn(a: int, b: int, *, c: int) -> tuple[int, int, int]:
        return (a, b, c)

    result = await retry_on_db_lock_async(fn, 1, 2, c=3, base_delay=0.001)
    assert result == (1, 2, 3)


@pytest.mark.asyncio
async def test_async_backoff_grows_exponentially() -> None:
    """Sleep delays double on each failed attempt (base * 2^attempt)."""
    calls = 0
    delays: list[float] = []
    orig_sleep = asyncio.sleep

    async def record_sleep(delay: float) -> None:
        delays.append(delay)
        await orig_sleep(0)  # yield but don't actually wait

    async def fn() -> str:
        nonlocal calls
        calls += 1
        if calls < 4:
            raise OperationalError("", {}, Exception("database is locked"))
        return "done"

    # Patch asyncio.sleep on the canonical module (shim re-exports from there)
    import chaoscypher_core.utils.retry as retry_mod

    monkey = retry_mod.asyncio
    retry_mod.asyncio = type("m", (), {"sleep": record_sleep})  # type: ignore[assignment]
    try:
        result = await retry_on_db_lock_async(fn, max_retries=5, base_delay=0.1)
    finally:
        retry_mod.asyncio = monkey

    assert result == "done"
    # attempt 0 failed -> sleep 0.1 (base * 2^0)
    # attempt 1 failed -> sleep 0.2
    # attempt 2 failed -> sleep 0.4
    # attempt 3 succeeded -> no more sleep
    assert delays == [0.1, 0.2, 0.4]


# --- retry_on_db_lock_sync ---


def test_sync_returns_immediately_on_success() -> None:
    """No retries occur when the callable succeeds on first attempt."""
    calls = 0

    def fn() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    assert retry_on_db_lock_sync(fn, base_delay=0.001) == "ok"
    assert calls == 1


def test_sync_retries_then_succeeds() -> None:
    """Callable succeeds after one lock error; result is returned."""
    calls = 0

    def fn() -> str:
        nonlocal calls
        calls += 1
        if calls < 2:
            raise OperationalError("", {}, Exception("database is locked"))
        return "finally"

    assert retry_on_db_lock_sync(fn, max_retries=5, base_delay=0.001) == "finally"
    assert calls == 2


def test_sync_exhausts_and_raises() -> None:
    """OperationalError is raised after max_retries consecutive lock errors."""

    def fn() -> None:
        raise OperationalError("", {}, Exception("database is locked"))

    with pytest.raises(OperationalError):
        retry_on_db_lock_sync(fn, max_retries=2, base_delay=0.001)


def test_sync_non_lock_not_retried() -> None:
    """Non-lock OperationalError propagates immediately without retry."""
    calls = 0

    def fn() -> None:
        nonlocal calls
        calls += 1
        raise OperationalError("", {}, Exception("constraint failed"))

    with pytest.raises(OperationalError):
        retry_on_db_lock_sync(fn, max_retries=5, base_delay=0.001)
    assert calls == 1
