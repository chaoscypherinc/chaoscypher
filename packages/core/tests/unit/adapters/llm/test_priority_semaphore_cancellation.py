# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests for PrioritySemaphore slot accounting under cancellation.

A waiter cancelled while parked in ``acquire()`` (before its ``try/finally``
ever runs) must not leak a slot — neither by leaving a phantom event in the
waiter queue that a later grant counts, nor by holding an already-granted slot
it will never use. With the leak, a single cancelled-while-waiting request
permanently consumes a slot and (at max_concurrent=1) deadlocks all LLM calls.
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
async def test_cancelled_queued_waiter_does_not_leak_slot() -> None:
    sem = PrioritySemaphore(max_concurrent=1, reserved_high_priority=0)

    holder_acquired = asyncio.Event()
    holder_release = asyncio.Event()

    async def holder() -> None:
        async with sem.acquire():
            holder_acquired.set()
            await holder_release.wait()

    holder_task = asyncio.create_task(holder())
    await holder_acquired.wait()  # the single slot is now held
    assert sem.active_count == 1

    # A second request queues for the slot. It cannot be granted while the
    # holder occupies the only slot, so it parks in `await my_event.wait()`.
    async def waiter() -> None:
        async with sem.acquire():
            pass

    waiter_task = asyncio.create_task(waiter())
    await _wait_until(lambda: sem.get_stats()["waiting_low_priority"] == 1)

    # Cancel the queued waiter mid-wait.
    waiter_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter_task

    # Release the holder's slot.
    holder_release.set()
    await holder_task

    # The slot must be free again: a fresh request acquires without hanging and
    # the active count returns to zero. With the leak, the freed slot would have
    # been handed to the cancelled waiter's phantom event, wedging the semaphore.
    async def fresh() -> bool:
        async with sem.acquire():
            return True

    assert await asyncio.wait_for(fresh(), timeout=1.0) is True
    assert sem.active_count == 0
    assert sem.get_stats()["waiting_low_priority"] == 0


@pytest.mark.asyncio
async def test_repeated_cancellations_do_not_exhaust_slots() -> None:
    """N waiters cancelled while queued must not permanently consume the slots."""
    sem = PrioritySemaphore(max_concurrent=2, reserved_high_priority=0)

    holder_release = asyncio.Event()

    async def holder() -> None:
        async with sem.acquire():
            await holder_release.wait()

    h1 = asyncio.create_task(holder())
    h2 = asyncio.create_task(holder())
    await _wait_until(lambda: sem.active_count == 2)

    async def waiter() -> None:
        async with sem.acquire():
            pass

    waiters = [asyncio.create_task(waiter()) for _ in range(5)]
    await _wait_until(lambda: sem.get_stats()["waiting_low_priority"] == 5)
    for w in waiters:
        w.cancel()
    for w in waiters:
        with pytest.raises(asyncio.CancelledError):
            await w

    holder_release.set()
    await asyncio.gather(h1, h2)

    async def fresh() -> bool:
        async with sem.acquire():
            return True

    assert await asyncio.wait_for(fresh(), timeout=1.0) is True
    assert sem.active_count == 0
