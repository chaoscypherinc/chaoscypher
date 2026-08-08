# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: ``orchestrator._ensure_execution_record`` must offload its write.

Pre-2026-05-23: ``execute_workflow_task`` called
``execution_repo.create_execution`` directly on the event loop, so the sync
``with adapter.transaction(): ...`` body — and any ``SafeSession._retry_delay``
``time.sleep`` triggered by SQLITE_BUSY contention — froze every other slot of
the 8-concurrent Operations queue for the retry duration. The fix wraps the
call in ``asyncio.to_thread``.

The previous version of this file never imported the orchestrator: both tests
performed the ``asyncio.to_thread`` call themselves and measured wall-clock
elapsed time, so reverting the production offload left them green (hunt queue
2026-08-01; the wall-clock-threshold flake risk was separately parked
2026-07-15). These tests drive the real ``_ensure_execution_record`` and
assert thread placement and loop liveness directly — no timing thresholds.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.operations.workflows.orchestrator import _ensure_execution_record


@pytest.mark.asyncio
async def test_create_execution_runs_on_a_worker_thread() -> None:
    """The repo write must execute off the event-loop thread.

    If the ``asyncio.to_thread`` wrapper inside ``_ensure_execution_record``
    is replaced with a direct call, ``create_execution`` runs on the event
    loop's own thread and the thread-identity assertion fails.
    """
    observed: dict[str, Any] = {}

    def create_execution(payload: dict[str, Any]) -> None:
        observed["thread_id"] = threading.get_ident()
        observed["payload"] = payload

    repo = MagicMock()
    repo.create_execution = create_execution

    execution_id = await _ensure_execution_record(
        execution_repo=repo,
        execution_id=None,
        workflow_id="wf-offload",
        triggered_by="manual",
        trigger_id=None,
        parent_execution_id=None,
        inputs={"a": 1},
    )

    assert observed["payload"]["id"] == execution_id
    assert observed["payload"]["workflow_id"] == "wf-offload"
    assert observed["payload"]["inputs"] == {"a": 1}
    assert observed["thread_id"] != threading.get_ident(), (
        "create_execution ran on the event-loop thread — the asyncio.to_thread "
        "offload in _ensure_execution_record has regressed"
    )


@pytest.mark.asyncio
async def test_event_loop_stays_live_while_create_execution_blocks() -> None:
    """The loop must keep running while the offloaded write blocks its thread.

    The fake ``create_execution`` blocks until a coroutine running on the
    event loop releases it. With the offload intact, that release is
    scheduled while the worker thread waits. If the write runs on the loop
    (regression), the releasing coroutine can never run: the ``wait`` below
    times out and the test fails — a deadline, not a timing threshold.
    """
    release = threading.Event()
    entered: asyncio.Event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def create_execution(payload: dict[str, Any]) -> None:
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(timeout=5), (
            "event loop never released the blocked write — create_execution "
            "is starving the loop it was supposed to be offloaded from"
        )

    repo = MagicMock()
    repo.create_execution = create_execution

    async with asyncio.timeout(10):
        task = asyncio.create_task(
            _ensure_execution_record(
                execution_repo=repo,
                execution_id=None,
                workflow_id="wf-liveness",
                triggered_by="manual",
                trigger_id=None,
                parent_execution_id=None,
                inputs={},
            )
        )
        await entered.wait()  # the write has started on its worker thread
        release.set()  # only reachable while the event loop is responsive
        await task
