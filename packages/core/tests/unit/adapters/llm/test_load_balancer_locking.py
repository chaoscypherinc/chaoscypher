# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests for OllamaLoadBalancer instance-state locking.

Before 2026-04-18, _select_instance mutated self._round_robin_index and
mark_instance_unhealthy wrote self._instances[id]['healthy']=False
without holding self._lock. Concurrent reload_config() could KeyError
or skew round-robin when instances were added/removed mid-flight.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from chaoscypher_core.adapters.llm.load_balancer import OllamaLoadBalancer


def test_select_instance_is_coroutine() -> None:
    """_select_instance must be async so it can hold the asyncio lock."""
    assert inspect.iscoroutinefunction(OllamaLoadBalancer._select_instance)


def test_mark_instance_unhealthy_is_coroutine() -> None:
    """mark_instance_unhealthy must be async so it can hold the asyncio lock."""
    assert inspect.iscoroutinefunction(OllamaLoadBalancer.mark_instance_unhealthy)


@pytest.mark.asyncio
async def test_select_instance_blocks_while_lock_is_held() -> None:
    """Round-robin mutation is serialized under self._lock."""
    balancer = OllamaLoadBalancer.__new__(OllamaLoadBalancer)
    balancer._lock = asyncio.Lock()
    balancer._providers = {"a": object(), "b": object()}
    balancer._instances = {
        "a": {"healthy": True},
        "b": {"healthy": True},
    }
    balancer._round_robin_index = 0
    balancer._strategy = "round_robin"

    async with balancer._lock:
        select_task = asyncio.create_task(balancer._select_instance())
        await asyncio.sleep(0.05)
        assert not select_task.done(), "_select_instance returned without acquiring the lock"

    result = await asyncio.wait_for(select_task, timeout=1.0)
    assert result in {"a", "b"}


@pytest.mark.asyncio
async def test_mark_instance_unhealthy_blocks_while_lock_is_held() -> None:
    balancer = OllamaLoadBalancer.__new__(OllamaLoadBalancer)
    balancer._lock = asyncio.Lock()
    balancer._instances = {"a": {"healthy": True}}

    async with balancer._lock:
        mark_task = asyncio.create_task(balancer.mark_instance_unhealthy("a", "connection refused"))
        await asyncio.sleep(0.05)
        assert not mark_task.done(), "mark_instance_unhealthy returned without acquiring the lock"

    await asyncio.wait_for(mark_task, timeout=1.0)
    assert balancer._instances["a"]["healthy"] is False
