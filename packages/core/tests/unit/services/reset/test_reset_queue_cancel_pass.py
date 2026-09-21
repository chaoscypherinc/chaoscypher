# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``reset_queue_stats`` must cancel non-terminal tasks before clearing keys.

Regression pin. ``POST /settings/reset/queue`` documents "All active/queued
jobs (cancelled)", but the implementation only unlinked the keyspace. No
cancel flag was set and ``cancel_all_tasks`` — which exists and does exactly
this — was never called, so a running handler finished unaware and its
terminal HSET *recreated* the just-deleted task hash with only status fields.
That hash is non-empty, so the worker's ``task_hash_missing`` guard does not
fire; the retry path then reads an absent ``operation`` and dead-letters the
task as "No handler registered for operations:" with its payload gone.

Ordering is the whole point, so it is asserted directly: cancelling after the
unlink would be indistinguishable from not cancelling at all.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.services.reset import operations as ops_mod
from chaoscypher_core.services.reset.operations import ResetOperations


def _make_reset_ops() -> ResetOperations:
    with (
        patch.object(ops_mod, "WorkflowSystemResetService", MagicMock()),
        patch.object(ops_mod, "DataResetService", MagicMock()),
        patch.object(ops_mod, "DatabaseResetService", MagicMock()),
        patch.object(ops_mod, "GraphCleanupService", MagicMock()),
    ):
        return ResetOperations(MagicMock(), MagicMock())


def _make_client(trace: list[str]) -> MagicMock:
    """A fake Redis client that records the order of the calls that matter."""
    client = MagicMock(name="redis-client")

    async def _keys(pattern: str) -> list[str]:
        trace.append(f"keys:{pattern}")
        return [f"{pattern.replace('*', 'x')}"]

    pipeline = MagicMock(name="pipeline")
    pipeline.unlink = MagicMock(side_effect=lambda key: trace.append(f"unlink:{key}"))
    pipeline.execute = AsyncMock(side_effect=lambda: trace.append("execute"))

    client.keys = AsyncMock(side_effect=_keys)
    client.pipeline = MagicMock(return_value=pipeline)
    return client


@pytest.mark.asyncio
async def test_cancel_runs_before_any_key_is_unlinked() -> None:
    """The cancel pass is the first queue call the reset makes."""
    ops = _make_reset_ops()
    trace: list[str] = []
    client = _make_client(trace)

    async def _cancel() -> int:
        trace.append("cancel_all_tasks")
        return 3

    from chaoscypher_core.queue import queue_client

    with (
        patch.object(queue_client, "client", client),
        patch.object(queue_client, "monitor", AsyncMock()),
        patch.object(queue_client, "cancel_all_tasks", _cancel),
    ):
        result = await ops.reset_queue_stats()

    assert result["status"] == "success"
    assert trace[0] == "cancel_all_tasks", (
        f"cancel must precede every key operation; call order was {trace}"
    )
    assert "unlink:queue:task:x" in trace


@pytest.mark.asyncio
async def test_cancelled_count_is_reported() -> None:
    """The count reaches the response, which documents 'cancelled ... counts'."""
    ops = _make_reset_ops()
    client = _make_client([])

    from chaoscypher_core.queue import queue_client

    with (
        patch.object(queue_client, "client", client),
        patch.object(queue_client, "monitor", AsyncMock()),
        patch.object(queue_client, "cancel_all_tasks", AsyncMock(return_value=7)),
    ):
        result = await ops.reset_queue_stats()

    assert result["tasks_cancelled"] == 7


@pytest.mark.asyncio
async def test_cancel_failure_does_not_block_the_reset() -> None:
    """A queue too unhealthy to cancel through must still be clearable.

    This endpoint is the operator's "my queue is stuck" action, so a failed
    cancel pass degrades to the old bulk-delete behavior rather than raising.
    """
    ops = _make_reset_ops()
    trace: list[str] = []
    client = _make_client(trace)

    async def _boom() -> Any:
        raise RuntimeError("valkey unreachable")

    from chaoscypher_core.queue import queue_client

    with (
        patch.object(queue_client, "client", client),
        patch.object(queue_client, "monitor", AsyncMock()),
        patch.object(queue_client, "cancel_all_tasks", _boom),
    ):
        result = await ops.reset_queue_stats()

    assert result["status"] == "success"
    assert result["tasks_cancelled"] == 0
    assert "execute" in trace, "keyspace must still be cleared after a cancel failure"
