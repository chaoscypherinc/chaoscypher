# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests for draining instances that still have parked waiters.

Before 2026-08-14 ``_drain_and_remove_instance`` polled only
``sem.active_count`` — requests queued on the semaphore were structurally
invisible — and once the poll budget ran out it popped
``_providers``/``_semaphores`` unconditionally. Two live failures followed:

1. The woken waiter evaluated ``self._providers[instance_id]`` and died with a
   bare ``KeyError`` in the middle of a chat turn / extraction chunk.
2. A URL-change reload (drain + re-add under the same id) installs a *new*
   semaphore; the waiter parked on the *old* one proceeded against the new
   provider without holding the new semaphore — a silent breach of the
   per-instance ``max_concurrent=1`` limit.

The tests drive real asyncio tasks against real ``PrioritySemaphore``s and use
bounded waits everywhere, so a regression fails rather than hangs.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import chaoscypher_core.adapters.llm.load_balancer as lb_mod
from chaoscypher_core.adapters.llm.limit import PrioritySemaphore
from chaoscypher_core.adapters.llm.load_balancer import OllamaLoadBalancer
from chaoscypher_core.exceptions import LLMError


# Every await in these tests is bounded by this many seconds so a regression
# surfaces as a failure instead of a hung suite.
_BOUND = 2.0


def _balancer(drain_max_wait: int = 2) -> OllamaLoadBalancer:
    """Assemble a balancer without running __init__ (no settings I/O)."""
    bal = OllamaLoadBalancer.__new__(OllamaLoadBalancer)
    bal._instances = {}
    bal._providers = {}
    bal._semaphores = {}
    bal._strategy = "round_robin"
    bal._round_robin_index = 0
    bal._lock = asyncio.Lock()
    bal._config_version = 0
    bal._global_config = {}
    bal._drain_max_wait = drain_max_wait
    bal._drain_check_interval = 0.0
    return bal


def _install_instance(
    bal: OllamaLoadBalancer, instance_id: str, provider: Any
) -> PrioritySemaphore:
    """Register ``provider`` under ``instance_id`` with a real one-slot semaphore.

    Mirrors ``_add_instance`` without constructing an OllamaProvider (which
    would drag in the Ollama SDK).
    """
    sem = PrioritySemaphore(max_concurrent=1, reserved_high_priority=0)
    bal._providers[instance_id] = provider
    bal._semaphores[instance_id] = sem
    bal._instances[instance_id] = {
        "healthy": True,
        "base_url": f"http://{instance_id}:11434",
    }
    return sem


async def _wait_until_parked(sem: PrioritySemaphore, count: int = 1) -> None:
    """Block until ``count`` requests sit queued on ``sem`` (bounded by _BOUND)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _BOUND
    while sem.high_priority_waiters.qsize() + sem.low_priority_waiters.qsize() < count:
        if loop.time() > deadline:
            raise AssertionError(f"no request parked on the semaphore within {_BOUND}s")
        await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# Drain accounting: parked waiters are not invisible
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_keeps_polling_while_only_waiters_remain() -> None:
    """A queued waiter alone keeps the drain polling; active_count==0 is not 'idle'."""
    bal = _balancer(drain_max_wait=3)

    sem = MagicMock()
    sem.active_count = 0  # no granted slot ...
    sem.high_priority_waiters = MagicMock()
    sem.high_priority_waiters.qsize.return_value = 0
    sem.low_priority_waiters = MagicMock()
    sem.low_priority_waiters.qsize.return_value = 1  # ... but one request is parked

    bal._semaphores = {"a": sem}
    bal._providers = {"a": MagicMock()}
    bal._instances = {"a": {"healthy": True}}

    sleeps: list[float] = []

    async def _record_sleep(seconds: float) -> None:
        """Stand-in for asyncio.sleep that records the drain's poll cycles."""
        sleeps.append(seconds)

    with patch.object(lb_mod.asyncio, "sleep", side_effect=_record_sleep):
        await bal._drain_and_remove_instance("a")

    assert len(sleeps) == 3, "drain broke out early despite a parked waiter"
    # The instance is still removed once the budget is spent — reload_config
    # must not be blocked forever by a waiter that never resolves.
    assert "a" not in bal._providers


def test_pending_requests_counts_active_plus_waiters() -> None:
    """_pending_requests sums granted slots and both waiter queues."""
    sem = MagicMock()
    sem.active_count = 1
    sem.high_priority_waiters = MagicMock()
    sem.high_priority_waiters.qsize.return_value = 2
    sem.low_priority_waiters = MagicMock()
    sem.low_priority_waiters.qsize.return_value = 3

    assert OllamaLoadBalancer._pending_requests(sem) == 6


# ---------------------------------------------------------------------------
# Variant 1: woken waiter must not KeyError on a removed instance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_waiter_woken_after_drain_raises_llm_error_not_keyerror() -> None:
    """Draining the pool out from under a parked waiter yields a classifiable error.

    Live repro: holder inside ``acquire_instance``, a second acquire queued on
    the same one-slot semaphore, ``_drain_and_remove_instance`` spends its
    budget and pops the instance, then the holder releases and the waiter is
    woken onto an instance that no longer exists.
    """
    bal = _balancer()
    sem = _install_instance(bal, "a", MagicMock(name="provider-a"))

    holder_acquired = asyncio.Event()
    release_holder = asyncio.Event()

    async def _holder() -> None:
        """Occupy the instance's only slot until told to let go."""
        async with bal.acquire_instance() as (_instance_id, _provider):
            holder_acquired.set()
            await release_holder.wait()

    async def _waiter() -> str:
        """Queue behind the holder for the same instance."""
        async with bal.acquire_instance() as (instance_id, _provider):
            return instance_id

    holder = asyncio.create_task(_holder())
    await asyncio.wait_for(holder_acquired.wait(), timeout=_BOUND)

    waiter = asyncio.create_task(_waiter())
    await _wait_until_parked(sem)

    await asyncio.wait_for(bal._drain_and_remove_instance("a"), timeout=_BOUND)
    assert "a" not in bal._providers

    release_holder.set()
    await asyncio.wait_for(holder, timeout=_BOUND)

    with pytest.raises(LLMError):
        await asyncio.wait_for(waiter, timeout=_BOUND)


@pytest.mark.asyncio
async def test_waiter_woken_after_drain_reselects_surviving_instance() -> None:
    """With another instance still in the pool, the woken waiter re-selects onto it."""
    bal = _balancer()
    drained_sem = _install_instance(bal, "a", MagicMock(name="provider-a"))
    survivor = MagicMock(name="provider-b")
    survivor_sem = _install_instance(bal, "b", survivor)

    holder_acquired = asyncio.Event()
    release_holder = asyncio.Event()

    async def _holder() -> None:
        """Hold instance 'a' — the one that is about to be drained."""
        async with bal._semaphores["a"].acquire(high_priority=False):
            holder_acquired.set()
            await release_holder.wait()

    async def _waiter() -> tuple[str, Any]:
        """Park behind the holder on 'a', then survive its removal."""
        async with bal.acquire_instance() as (instance_id, provider):
            return instance_id, provider

    holder = asyncio.create_task(_holder())
    await asyncio.wait_for(holder_acquired.wait(), timeout=_BOUND)

    # Pin selection to "a" so the waiter definitely queues on the doomed instance.
    with patch.object(bal, "_select_instance", side_effect=["a", "b"]):
        waiter = asyncio.create_task(_waiter())
        await _wait_until_parked(drained_sem)

        await asyncio.wait_for(bal._drain_and_remove_instance("a"), timeout=_BOUND)
        release_holder.set()
        await asyncio.wait_for(holder, timeout=_BOUND)

        instance_id, provider = await asyncio.wait_for(waiter, timeout=_BOUND)

    assert instance_id == "b"
    assert provider is survivor
    # The re-selected request ran under the surviving instance's own semaphore,
    # and gave the slot back on completion.
    assert survivor_sem.active_count == 0
    assert survivor_sem.total_low_priority == 1


# ---------------------------------------------------------------------------
# Variant 2: URL change swaps the semaphore under a parked waiter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_waiter_parked_across_url_change_holds_the_new_semaphore() -> None:
    """A drain+re-add under the same id must not let the old waiter run slotless.

    ``reload_config`` recreates an instance whose ``base_url`` changed by
    draining it and immediately re-adding it — installing a *new* provider and
    a *new* semaphore under the same id. A request parked on the old semaphore
    must not proceed against the new provider while holding only the retired
    semaphore, or the instance runs two concurrent requests against a
    ``max_concurrent=1`` backend.
    """
    bal = _balancer()
    old_sem = _install_instance(bal, "a", MagicMock(name="provider-old"))

    holder_acquired = asyncio.Event()
    release_holder = asyncio.Event()
    waiter_running = asyncio.Event()
    release_waiter = asyncio.Event()
    observed: dict[str, Any] = {}

    async def _holder() -> None:
        """Occupy the pre-reload instance's only slot."""
        async with bal.acquire_instance() as (_instance_id, _provider):
            holder_acquired.set()
            await release_holder.wait()

    async def _waiter() -> None:
        """Park on the old semaphore and record what it holds once woken."""
        async with bal.acquire_instance() as (instance_id, provider):
            observed["instance_id"] = instance_id
            observed["provider"] = provider
            observed["new_sem_active"] = bal._semaphores["a"].active_count
            observed["old_sem_active"] = old_sem.active_count
            waiter_running.set()
            await release_waiter.wait()

    holder = asyncio.create_task(_holder())
    await asyncio.wait_for(holder_acquired.wait(), timeout=_BOUND)

    waiter = asyncio.create_task(_waiter())
    await _wait_until_parked(old_sem)

    # URL change: drain then re-add under the same id (reload_config's path).
    new_provider = MagicMock(name="provider-new")
    await asyncio.wait_for(bal._drain_and_remove_instance("a"), timeout=_BOUND)
    new_sem = _install_instance(bal, "a", new_provider)
    assert new_sem is not old_sem

    release_holder.set()
    await asyncio.wait_for(holder, timeout=_BOUND)
    await asyncio.wait_for(waiter_running.wait(), timeout=_BOUND)

    assert observed["provider"] is new_provider
    assert observed["new_sem_active"] == 1, "waiter ran without holding the new semaphore"
    assert observed["old_sem_active"] == 0, "waiter still held the retired semaphore"

    release_waiter.set()
    await asyncio.wait_for(waiter, timeout=_BOUND)
    assert new_sem.active_count == 0


@pytest.mark.asyncio
async def test_repeated_replacement_gives_up_with_llm_error() -> None:
    """Endless instance churn ends in a bounded LLMError, never an infinite retry."""
    bal = _balancer()
    _install_instance(bal, "a", MagicMock(name="provider-a"))
    ghost_sems: list[PrioritySemaphore] = []

    async def _select_ghost() -> str:
        """Selection stub always landing on an id whose provider is already gone."""
        sem = PrioritySemaphore(max_concurrent=1, reserved_high_priority=0)
        ghost_sems.append(sem)
        bal._semaphores["ghost"] = sem
        bal._providers.pop("ghost", None)
        return "ghost"

    with (
        patch.object(bal, "_select_instance", side_effect=_select_ghost) as select,
        pytest.raises(LLMError) as excinfo,
    ):
        async with bal.acquire_instance() as (_instance_id, _provider):
            pytest.fail("acquire_instance yielded a retired instance")

    assert select.await_count == lb_mod._ACQUIRE_MAX_ATTEMPTS
    assert excinfo.value.is_retryable is True
    # Every slot taken on the way out was handed back — no leaked capacity.
    assert [sem.active_count for sem in ghost_sems] == [0] * lb_mod._ACQUIRE_MAX_ATTEMPTS
