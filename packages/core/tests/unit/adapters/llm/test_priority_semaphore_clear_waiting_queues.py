# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests for ``PrioritySemaphore.clear_waiting_queues()``.

Pre-fix, the method awaited ``_try_grant_slots()`` while still inside its own
``async with self.lock`` block. ``asyncio.Lock`` is not reentrant, so the call
never returned and left every subsequent ``acquire()`` / ``_release_slot()``
blocked on the held lock — one admin call to ``DELETE /api/v1/llm/semaphore``
wedged all LLM traffic for the process lifetime.

Secondary accounting defect, also covered here: drained waiters were woken via
``event.set()`` with no slot grant and no cancellation signal, so a live waiter
proceeded into its LLM call slotless and its ``finally`` drove ``active_count``
negative.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from chaoscypher_core.adapters.llm.limit import PrioritySemaphore


async def _wait_until(predicate: Callable[[], bool], timeout: float = 1.0) -> None:
    """Spin the event loop until ``predicate()`` is true (bounded)."""
    async with asyncio.timeout(timeout):
        # Polling semaphore-internal waiter counts; there is no Event to await.
        while not predicate():  # noqa: ASYNC110
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_clear_with_empty_queues_returns_and_releases_lock() -> None:
    """The no-op clear must return promptly and leave the lock free."""
    sem = PrioritySemaphore(max_concurrent=1, reserved_high_priority=0)

    result = await asyncio.wait_for(sem.clear_waiting_queues(), timeout=1.0)

    assert result == {
        "high_priority_cleared": 0,
        "low_priority_cleared": 0,
        "total_cleared": 0,
    }
    assert not sem.lock.locked()


@pytest.mark.asyncio
async def test_semaphore_still_usable_after_clear() -> None:
    """acquire() must not hang on a lock left held by clear_waiting_queues()."""
    sem = PrioritySemaphore(max_concurrent=1, reserved_high_priority=0)

    await asyncio.wait_for(sem.clear_waiting_queues(), timeout=1.0)

    async def fresh() -> bool:
        async with sem.acquire():
            return True

    assert await asyncio.wait_for(fresh(), timeout=1.0) is True
    assert sem.active_count == 0


@pytest.mark.asyncio
async def test_cleared_live_waiter_is_cancelled_not_run_slotless() -> None:
    """A live waiter drained by clear must raise, and counts must stay >= 0."""
    sem = PrioritySemaphore(max_concurrent=1, reserved_high_priority=0)

    holder_acquired = asyncio.Event()
    holder_release = asyncio.Event()

    async def holder() -> None:
        async with sem.acquire():
            holder_acquired.set()
            await holder_release.wait()

    holder_task = asyncio.create_task(holder())
    await holder_acquired.wait()
    assert sem.active_count == 1

    waiter_entered_llm_call = False

    async def waiter() -> None:
        nonlocal waiter_entered_llm_call
        async with sem.acquire():
            waiter_entered_llm_call = True

    waiter_task = asyncio.create_task(waiter())
    await _wait_until(lambda: sem.get_stats()["waiting_low_priority"] == 1)

    result = await asyncio.wait_for(sem.clear_waiting_queues(), timeout=1.0)
    assert result["total_cleared"] == 1

    # The woken waiter must abort, not run without a slot.
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(waiter_task, timeout=1.0)
    assert waiter_entered_llm_call is False

    holder_release.set()
    await holder_task

    # No phantom release: the cleared waiter never held a slot, so the count
    # returns to exactly zero (pre-fix it went negative).
    assert sem.active_count == 0
    assert sem.get_stats()["waiting_low_priority"] == 0

    # And the slot is genuinely reusable.
    async def fresh() -> bool:
        async with sem.acquire():
            return True

    assert await asyncio.wait_for(fresh(), timeout=1.0) is True
    assert sem.active_count == 0
