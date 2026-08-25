# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests for the low-priority admission gate in ``_try_grant_slots``.

Two related defects, both in the elif-branch that grants low-priority
waiters (packages/core/src/chaoscypher_core/adapters/llm/limit.py):

1. The gate compared *total* ``active_count`` against ``available_for_low``
   instead of ``active_low_priority`` (the counter that actually tracks how
   many of the low tier's allowance are in use). Any active high-priority
   work — even work that never touches the reserved slots — counted against
   the low tier's budget, so free slots could sit idle while a low-priority
   waiter starved in ``await my_event.wait()`` (untimed, so the starvation
   is permanent).

2. ``reserved_high_priority`` was clamped only to ``max_concurrent`` (not
   ``max_concurrent - 1``), so a ``settings.yaml`` config with
   ``llm_reserved_interactive == llm_max_concurrent`` drove
   ``available_for_low`` to 0 — no low-priority request could ever be
   granted, regardless of how many slots were actually idle.
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
async def test_low_priority_admitted_while_high_priority_occupies_non_reserved_slots() -> None:
    """3 active high-priority holders + max=4/reserved=1 must still admit a low waiter.

    available_for_low = max_concurrent(4) - reserved_high_priority(1) = 3.
    active_low_priority is 0, so the low waiter's own tier has 3 of 3 slots
    free and must be granted promptly. Gating on total ``active_count``
    (also 3 here) makes ``3 < 3`` false and blocks the waiter forever even
    though its tier is entirely unused — the exact starvation this test
    guards against.
    """
    sem = PrioritySemaphore(max_concurrent=4, reserved_high_priority=1)

    holder_release = asyncio.Event()

    async def high_holder() -> None:
        """Hold a high-priority slot open until ``holder_release`` is set."""
        async with sem.acquire(high_priority=True):
            await holder_release.wait()

    holders = [asyncio.create_task(high_holder()) for _ in range(3)]
    await _wait_until(lambda: sem.active_high_priority == 3)
    assert sem.active_count == 3

    async def low_waiter() -> bool:
        """Acquire a low-priority slot and report whether it was granted."""
        async with sem.acquire(high_priority=False):
            return True

    # Bounded wait: with the bug this waiter is never granted a slot, so an
    # unbounded await would hang the test suite instead of failing it.
    granted = await asyncio.wait_for(low_waiter(), timeout=1.0)
    assert granted is True

    holder_release.set()
    await asyncio.gather(*holders)


@pytest.mark.asyncio
async def test_reserved_slots_cannot_consume_the_entire_pool() -> None:
    """Requesting ``reserved_high_priority == max_concurrent`` must not zero out the low tier.

    Pre-fix, ``reserved_high_priority`` was clamped only to
    ``max_concurrent`` (permitting equality), which drives
    ``available_for_low`` to 0 and starves every low-priority request
    forever, independent of the counter defect covered above.
    """
    sem = PrioritySemaphore(max_concurrent=3, reserved_high_priority=3)

    # The clamp must leave at least one slot outside the reserved pool.
    assert sem.reserved_high_priority < sem.max_concurrent
    assert sem.max_concurrent - sem.reserved_high_priority >= 1

    async def low_waiter() -> bool:
        """Acquire a low-priority slot and report whether it was granted."""
        async with sem.acquire(high_priority=False):
            return True

    granted = await asyncio.wait_for(low_waiter(), timeout=1.0)
    assert granted is True


@pytest.mark.asyncio
async def test_update_config_reserved_equal_to_max_is_also_clamped() -> None:
    """The runtime reconfiguration path (``update_config``) must apply the same clamp.

    ``update_config`` is reachable live from a ``settings.yaml`` reload via
    the Ollama load balancer, so it must not reopen the starvation window
    the constructor clamp closes.
    """
    sem = PrioritySemaphore(max_concurrent=2, reserved_high_priority=0)
    await sem.update_config(max_concurrent=2, reserved_high_priority=2)

    assert sem.reserved_high_priority < sem.max_concurrent


@pytest.mark.asyncio
async def test_update_config_shrinking_max_concurrent_reclamps_existing_reserved() -> None:
    """Shrinking ``max_concurrent`` alone (reserved left untouched) must not strand an invalid config.

    A caller updating only ``max_concurrent`` (leaving a previously valid
    ``reserved_high_priority`` untouched) must not end up with
    ``reserved_high_priority >= max_concurrent`` — that reopens the
    starvation window via a path that never explicitly re-requests the
    reserved count.
    """
    sem = PrioritySemaphore(max_concurrent=4, reserved_high_priority=3)
    assert sem.reserved_high_priority == 3  # valid: 3 < 4

    await sem.update_config(max_concurrent=2)  # reserved_high_priority left as None

    assert sem.reserved_high_priority < sem.max_concurrent
