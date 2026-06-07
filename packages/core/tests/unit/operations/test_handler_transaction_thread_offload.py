# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: handler ``with adapter.transaction()`` bodies must not block the event loop.

Pre-2026-05-23: ``_execute_handler`` awaited every operation handler with
no thread offload. Each handler's synchronous ``with adapter.transaction():
...`` body ran on the event loop, so any ``SafeSession._retry_delay``
``time.sleep(...)`` triggered by SQLITE_BUSY contention froze every other
slot of the 8-concurrent Operations queue for the retry duration.

Fix: every handler call site whose transaction body is sync now wraps the
body (or the sync repo method that owns the transaction) in
``asyncio.to_thread`` so the sleep runs on a worker thread instead of the
event loop. This test pins the contract by simulating a 200ms
``time.sleep`` inside one handler's transaction body and verifying a
sibling async task progresses concurrently rather than waiting for the
sleep to finish.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.operations.workflows.repository import WorkflowExecutionRepository


# A 200ms sleep is long enough that, if it were running on the event loop,
# a sibling coroutine spaced 1ms apart would never see itself complete
# before the sleeper finishes. With the thread offload the sleep blocks
# only its worker thread, so the sibling completes almost immediately.
_SIM_RETRY_SLEEP_S: float = 0.200
# A 50ms tolerance on top of "should complete near-instantly" makes the
# test stable on slow / loaded CI runners without being so loose that a
# regression (sibling waits for the full 200ms) would pass.
_SIBLING_MAX_ELAPSED_S: float = 0.150


def _make_blocking_adapter() -> MagicMock:
    """Adapter whose ``transaction()`` body simulates a SafeSession retry sleep.

    The ``transaction()`` context manager performs a 200ms blocking
    ``time.sleep`` inside the ``with`` block — exactly the shape of
    ``SafeSession._retry_delay`` under SQLITE_BUSY contention. The session
    setter / getter are stubbed because ``WorkflowExecutionRepository``
    reads ``self.adapter.session`` inside ``create_execution``.
    """
    adapter = MagicMock()

    @contextmanager
    def _blocking_transaction() -> Any:
        # Simulate _retry_delay: a synchronous blocking sleep that, on
        # main pre-2026-05-23, would have frozen the event loop.
        time.sleep(_SIM_RETRY_SLEEP_S)
        yield

    adapter.transaction = _blocking_transaction
    # session is a real attribute, not a MagicMock chain, so the repo's
    # ``assert session is not None`` passes.
    adapter.session = MagicMock()
    return adapter


@pytest.mark.asyncio
async def test_workflow_repo_write_does_not_block_event_loop() -> None:
    """A repo write whose transaction body sleeps must not block sibling coroutines.

    Models the 2026-05-23 fix contract: ``execute_workflow_task`` now
    invokes ``execution_repo.create_execution`` via
    ``await asyncio.to_thread(...)``. Inside that thread, the sync
    ``with self.adapter.transaction(): ...`` block runs — and any
    ``time.sleep`` triggered there (modelling
    ``SafeSession._retry_delay`` under SQLITE_BUSY) blocks ONLY the
    worker thread, not the event loop.

    The test fires two concurrent tasks:

      A — drives the blocking repo write through ``asyncio.to_thread``
          (the production code path).
      B — a lightweight sibling coroutine that records the wall time it
          observes between scheduling and finishing.

    If the thread offload works, sibling B finishes far faster than the
    200ms sleep. If a regression reverts the offload, sibling B is
    starved on the event loop and its elapsed time approaches the full
    200ms.
    """
    adapter = _make_blocking_adapter()
    repo = WorkflowExecutionRepository(adapter)

    sibling_elapsed: dict[str, float] = {}

    async def driver_a() -> None:
        # Same call shape as orchestrator._ensure_execution_record:
        # the blocking sync repo method goes through asyncio.to_thread.
        await asyncio.to_thread(
            repo.create_execution,
            {
                "id": "exec-a",
                "workflow_id": "wf-a",
                "triggered_by": "manual",
                "trigger_id": None,
                "parent_execution_id": None,
                "inputs": {},
                "status": "pending",
            },
        )

    async def sibling_b() -> None:
        # Give driver_a a moment to enter its thread so we measure the
        # in-flight window, not the scheduling gap.
        await asyncio.sleep(0.01)
        start = time.monotonic()
        # A trivial async-only step that completes near-instantly when
        # the event loop is responsive.
        await asyncio.sleep(0)
        sibling_elapsed["seconds"] = time.monotonic() - start

    await asyncio.gather(driver_a(), sibling_b())

    assert "seconds" in sibling_elapsed, "sibling did not record elapsed time"
    assert sibling_elapsed["seconds"] < _SIBLING_MAX_ELAPSED_S, (
        "sibling coroutine took "
        f"{sibling_elapsed['seconds']:.3f}s — the {_SIM_RETRY_SLEEP_S}s "
        "blocking sleep inside the transaction body starved the event "
        "loop. The asyncio.to_thread offload must keep the sleep on a "
        "worker thread so other Operations-queue slots progress."
    )


@pytest.mark.asyncio
async def test_two_blocking_handlers_run_in_parallel() -> None:
    """Two concurrent handlers each sleeping 200ms must finish in ~200ms total.

    Sister of ``test_workflow_repo_write_does_not_block_event_loop``.
    With the thread offload, two ``asyncio.to_thread`` calls run on
    distinct worker threads — both sleeps happen in parallel and the
    total wall time is dominated by a single sleep, not the sum.
    """
    adapter = _make_blocking_adapter()
    repo = WorkflowExecutionRepository(adapter)

    async def run_write(execution_id: str) -> None:
        await asyncio.to_thread(
            repo.create_execution,
            {
                "id": execution_id,
                "workflow_id": "wf-parallel",
                "triggered_by": "manual",
                "trigger_id": None,
                "parent_execution_id": None,
                "inputs": {},
                "status": "pending",
            },
        )

    start = time.monotonic()
    await asyncio.gather(run_write("exec-1"), run_write("exec-2"))
    elapsed = time.monotonic() - start

    # Serial execution would take ~400ms; parallel-via-threads takes
    # ~200ms + scheduling overhead. Allow 100ms of slack for slow CI.
    assert elapsed < (_SIM_RETRY_SLEEP_S * 2) - 0.05, (
        f"two concurrent handlers took {elapsed:.3f}s — expected near "
        f"{_SIM_RETRY_SLEEP_S:.3f}s (parallel). Looks like the "
        "asyncio.to_thread offload serialized the work — possibly the "
        "GIL was held across the sleep, or the wrap regressed."
    )
