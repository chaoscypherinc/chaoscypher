# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Cortex health-evaluator loop tests.

Mirrors the neuron sibling's ``TestHealthMonitorLoop``
(packages/neuron/tests/unit/test_worker_setup_helpers.py): the loop must
enter a fresh ``adapter.session_scope()`` around every tick so the
evaluator's ``asyncio.to_thread`` adapter calls never drive the singleton
``_fallback_session`` from a worker thread while request handlers emit
system events through the same adapter on the event loop.
"""

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_cortex.lifespan import _health_evaluator_loop


def _make_scoped_adapter() -> tuple[MagicMock, list[str]]:
    """Fake adapter whose session_scope records enter/exit events."""
    events: list[str] = []
    adapter = MagicMock()

    @contextlib.asynccontextmanager
    async def _scope():
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    adapter.session_scope = _scope
    return adapter, events


async def _cancel(task: asyncio.Task[None]) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def _never_shutdown() -> bool:
    return False


class TestHealthEvaluatorLoop:
    @pytest.mark.asyncio
    async def test_tick_called_repeatedly(self) -> None:
        evaluator = MagicMock()
        evaluator.tick = AsyncMock()
        adapter, _events = _make_scoped_adapter()

        task = asyncio.create_task(
            _health_evaluator_loop(
                evaluator=evaluator,
                adapter=adapter,
                interval_seconds=0.02,
                should_shutdown=_never_shutdown,
            )
        )
        await asyncio.sleep(0.08)
        await _cancel(task)

        assert evaluator.tick.await_count >= 2

    @pytest.mark.asyncio
    async def test_each_tick_runs_inside_session_scope(self) -> None:
        """Every pass enters (and exits) adapter.session_scope around tick()."""
        adapter, events = _make_scoped_adapter()
        scope_depth_at_tick: list[int] = []
        two_ticks = asyncio.Event()

        async def _tick() -> None:
            scope_depth_at_tick.append(events.count("enter") - events.count("exit"))
            if len(scope_depth_at_tick) >= 2:
                two_ticks.set()

        evaluator = MagicMock()
        evaluator.tick = AsyncMock(side_effect=_tick)

        task = asyncio.create_task(
            _health_evaluator_loop(
                evaluator=evaluator,
                adapter=adapter,
                interval_seconds=0.01,
                should_shutdown=_never_shutdown,
            )
        )
        try:
            await asyncio.wait_for(two_ticks.wait(), timeout=5.0)
        finally:
            await _cancel(task)

        # Each tick observed exactly one open scope, and one scope was
        # entered (and closed) per tick — not one shared scope for the loop.
        assert all(depth == 1 for depth in scope_depth_at_tick)
        assert events.count("enter") >= 2
        assert events.count("enter") == events.count("exit")

    @pytest.mark.asyncio
    async def test_tick_exception_does_not_kill_loop(self) -> None:
        adapter, events = _make_scoped_adapter()
        recovered = asyncio.Event()
        call_count = 0

        async def flaky_tick() -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("tick boom")
            recovered.set()

        evaluator = MagicMock()
        evaluator.tick = AsyncMock(side_effect=flaky_tick)

        task = asyncio.create_task(
            _health_evaluator_loop(
                evaluator=evaluator,
                adapter=adapter,
                interval_seconds=0.01,
                should_shutdown=_never_shutdown,
            )
        )
        try:
            await asyncio.wait_for(recovered.wait(), timeout=5.0)
        finally:
            await _cancel(task)

        assert call_count >= 2
        # The failing tick's scope still closed.
        assert events.count("enter") == events.count("exit")
