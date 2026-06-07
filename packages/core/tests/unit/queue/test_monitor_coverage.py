# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Behavioral coverage tests for ``queue/monitor.py`` (``QueueMonitor``).

Each public method is exercised in both the ``client is None`` short-circuit
and the live-client path. The live path uses a fake async Valkey whose command
methods are AsyncMocks returning awaitables (the production code awaits results
that aren't already plain int/dict), plus an async-generator ``scan_iter`` for
the auto-detect / clear-all flows:

- ``get_queue_stats`` — empty (no client), live counts, and the health-key
  fallback that fills ``running`` from the published health hash.
- ``get_all_stats`` — explicit queue set vs. auto-detect from pending + health
  keys via ``scan_iter``.
- ``track_tokens`` — input/output token + cost-cents HINCRBY (and no-client
  no-op).
- ``get_token_stats`` — stored-cost path, custom per-million override path, and
  the empty no-client default.
- ``clear_token_stats`` — single-queue delete vs. all-registered-queues delete.
- ``clear_all_stats`` — recent + scan_iter sweep of ``*:recent`` / ``*:stats``.

The fake Valkey backend mirrors the recording-AsyncMock pattern used by the
sibling queue tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.queue.monitor import QueueMonitor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_valkey() -> MagicMock:
    """Build a recording fake async Valkey client for the monitor."""
    valkey = MagicMock()
    valkey.zcard = AsyncMock(return_value=0)
    valkey.scard = AsyncMock(return_value=0)
    valkey.hgetall = AsyncMock(return_value={})
    valkey.hincrby = AsyncMock(return_value=1)
    valkey.hget = AsyncMock(return_value=None)
    valkey.delete = AsyncMock(return_value=1)
    return valkey


def _scan_iter_factory(keys: list[bytes]) -> Any:
    """Return a callable producing an async iterator over ``keys`` per call."""

    def _scan_iter(match: str | None = None) -> AsyncIterator[bytes]:
        async def _gen() -> AsyncIterator[bytes]:
            for key in keys:
                # Crude match emulation: only yield keys matching the suffix.
                if match is None or _matches(key, match):
                    yield key

        return _gen()

    return _scan_iter


def _matches(key: bytes, match: str) -> bool:
    """Emulate Valkey glob matching for ``queue:*:suffix`` patterns."""
    key_str = key.decode()
    prefix, _, suffix = match.partition("*")
    return key_str.startswith(prefix) and key_str.endswith(suffix)


# ---------------------------------------------------------------------------
# get_queue_stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_queue_stats_no_client_returns_zeros() -> None:
    """With no client, get_queue_stats returns a zeroed stub dict."""
    monitor = QueueMonitor(client=None)
    stats = await monitor.get_queue_stats("llm")
    assert stats == {
        "queue": "llm",
        "queued": 0,
        "running": 0,
        "completed_recent": 0,
        "failed_recent": 0,
    }


@pytest.mark.asyncio
async def test_get_queue_stats_live_counts() -> None:
    """Live client path reads queued (zcard) and running (scard) counts."""
    valkey = _make_valkey()
    valkey.zcard = AsyncMock(return_value=7)
    valkey.scard = AsyncMock(return_value=2)
    valkey.hgetall = AsyncMock(return_value={})  # no health key

    monitor = QueueMonitor(client=valkey)
    stats = await monitor.get_queue_stats("llm")

    assert stats["queued"] == 7
    assert stats["running"] == 2
    assert stats["workers"] == 0  # no health hash -> no worker detected


@pytest.mark.asyncio
async def test_get_queue_stats_health_fallback_fills_running() -> None:
    """When running set is empty but health hash reports running, prefer health."""
    valkey = _make_valkey()
    valkey.zcard = AsyncMock(return_value=0)
    valkey.scard = AsyncMock(return_value=0)
    valkey.hgetall = AsyncMock(return_value={b"running": b"4"})

    monitor = QueueMonitor(client=valkey)
    stats = await monitor.get_queue_stats("llm")

    assert stats["workers"] == 1
    assert stats["running"] == 4  # filled from health hash


@pytest.mark.asyncio
async def test_get_queue_stats_health_present_but_running_nonzero_keeps_scard() -> None:
    """A live running count takes precedence over the health-hash value."""
    valkey = _make_valkey()
    valkey.zcard = AsyncMock(return_value=0)
    valkey.scard = AsyncMock(return_value=3)
    valkey.hgetall = AsyncMock(return_value={b"running": b"99"})

    monitor = QueueMonitor(client=valkey)
    stats = await monitor.get_queue_stats("llm")

    assert stats["workers"] == 1
    assert stats["running"] == 3  # scard wins because it's non-zero


# ---------------------------------------------------------------------------
# get_all_stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_all_stats_uses_explicit_queue_set() -> None:
    """A pre-registered queue set is used directly (no auto-detect scan)."""
    valkey = _make_valkey()
    valkey.zcard = AsyncMock(return_value=1)
    valkey.scard = AsyncMock(return_value=0)
    valkey.scan_iter = _scan_iter_factory([])

    monitor = QueueMonitor(client=valkey, queues={"llm"})
    all_stats = await monitor.get_all_stats()

    assert len(all_stats) == 1
    assert all_stats[0]["queue"] == "llm"


@pytest.mark.asyncio
async def test_get_all_stats_auto_detects_from_keys() -> None:
    """With no registered queues, queues are auto-detected from pending+health keys."""
    valkey = _make_valkey()
    valkey.zcard = AsyncMock(return_value=0)
    valkey.scard = AsyncMock(return_value=0)
    valkey.hgetall = AsyncMock(return_value={})
    valkey.scan_iter = _scan_iter_factory([b"queue:llm:pending", b"queue:operations:health"])

    monitor = QueueMonitor(client=valkey, queues=set())
    all_stats = await monitor.get_all_stats()

    detected = {s["queue"] for s in all_stats}
    assert detected == {"llm", "operations"}


# ---------------------------------------------------------------------------
# track_tokens
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_track_tokens_no_client_noop() -> None:
    """track_tokens with no client returns without error."""
    monitor = QueueMonitor(client=None)
    await monitor.track_tokens("llm", 10, 20, cost_usd=1.0)


@pytest.mark.asyncio
async def test_track_tokens_increments_stats_hash() -> None:
    """track_tokens HINCRBY's input/output tokens and cost-cents into the stats hash."""
    valkey = _make_valkey()
    monitor = QueueMonitor(client=valkey)

    await monitor.track_tokens("llm", input_tokens=100, output_tokens=50, cost_usd=2.5)

    stats_key = "queue:llm:stats"
    valkey.hincrby.assert_any_await(stats_key, "total_input_tokens", 100)
    valkey.hincrby.assert_any_await(stats_key, "total_output_tokens", 50)
    # 2.5 USD -> 250 cents.
    valkey.hincrby.assert_any_await(stats_key, "total_cost_cents", 250)


# ---------------------------------------------------------------------------
# get_token_stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_token_stats_no_client_returns_zeros() -> None:
    """get_token_stats with no client returns a zeroed default."""
    monitor = QueueMonitor(client=None)
    stats = await monitor.get_token_stats("llm")
    assert stats == {
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_tokens": 0,
        "total_cost_usd": 0.0,
    }


@pytest.mark.asyncio
async def test_get_token_stats_stored_cost_path() -> None:
    """Without custom costs, total_cost_usd derives from stored cost cents."""
    valkey = _make_valkey()

    def _hget(_key: str, field: str) -> Any:
        async def _coro() -> bytes:
            return {
                "total_input_tokens": b"1000",
                "total_output_tokens": b"500",
                "total_cost_cents": b"150",
            }[field]

        return _coro()

    valkey.hget = MagicMock(side_effect=_hget)
    monitor = QueueMonitor(client=valkey)

    stats = await monitor.get_token_stats("llm")

    assert stats["total_input_tokens"] == 1000
    assert stats["total_output_tokens"] == 500
    assert stats["total_tokens"] == 1500
    assert stats["total_cost_usd"] == 1.5  # 150 cents


@pytest.mark.asyncio
async def test_get_token_stats_custom_cost_override() -> None:
    """Custom per-million costs override the stored cost-cents value."""
    valkey = _make_valkey()

    def _hget(_key: str, field: str) -> Any:
        async def _coro() -> bytes:
            return {
                "total_input_tokens": b"1000000",
                "total_output_tokens": b"2000000",
            }.get(field, b"0")

        return _coro()

    valkey.hget = MagicMock(side_effect=_hget)
    monitor = QueueMonitor(client=valkey)

    stats = await monitor.get_token_stats("llm", custom_input_cost=3.0, custom_output_cost=6.0)

    # 1M input * $3/M + 2M output * $6/M = 3 + 12 = 15.
    assert stats["total_cost_usd"] == pytest.approx(15.0)


# ---------------------------------------------------------------------------
# clear_token_stats / clear_all_stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_token_stats_no_client_noop() -> None:
    """clear_token_stats with no client is a no-op."""
    monitor = QueueMonitor(client=None)
    await monitor.clear_token_stats("llm")


@pytest.mark.asyncio
async def test_clear_token_stats_single_queue() -> None:
    """A named queue deletes only that queue's stats hash."""
    valkey = _make_valkey()
    monitor = QueueMonitor(client=valkey)
    await monitor.clear_token_stats("llm")
    valkey.delete.assert_awaited_once_with("queue:llm:stats")


@pytest.mark.asyncio
async def test_clear_token_stats_all_registered_queues() -> None:
    """With no queue arg, every registered queue's stats hash is deleted."""
    valkey = _make_valkey()
    monitor = QueueMonitor(client=valkey, queues={"llm", "operations"})
    await monitor.clear_token_stats(None)

    deleted = {c.args[0] for c in valkey.delete.await_args_list}
    assert deleted == {"queue:llm:stats", "queue:operations:stats"}


@pytest.mark.asyncio
async def test_clear_all_stats_no_client_noop() -> None:
    """clear_all_stats with no client is a no-op."""
    monitor = QueueMonitor(client=None)
    await monitor.clear_all_stats()


@pytest.mark.asyncio
async def test_clear_all_stats_sweeps_recent_and_stats_keys() -> None:
    """clear_all_stats deletes queue:recent plus every *:recent and *:stats key."""
    valkey = _make_valkey()
    valkey.scan_iter = _scan_iter_factory(
        [b"queue:llm:recent", b"queue:llm:stats", b"queue:operations:stats"]
    )
    monitor = QueueMonitor(client=valkey)

    await monitor.clear_all_stats()

    deleted = {c.args[0] for c in valkey.delete.await_args_list}
    assert "queue:recent" in deleted
    assert b"queue:llm:recent" in deleted
    assert b"queue:llm:stats" in deleted
    assert b"queue:operations:stats" in deleted
