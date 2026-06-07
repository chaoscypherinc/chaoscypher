# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests that SourceRecovery respects both per-source and system pause.

The per-source `is_paused` short-circuit in `_recover_one` skips
paused sources. The system-wide pause extends the same method to
additionally skip every non-terminal source when the singleton
SystemState reports `processing_paused=True`.
"""

from unittest.mock import AsyncMock

import pytest

from chaoscypher_core.services.sources.recovery import SourceRecovery


def _seed_non_terminal(adapter, source_id: str, status: str = "pending") -> None:
    adapter.create_source(
        {
            "id": source_id,
            "database_name": adapter.database_name,
            "filename": f"{source_id}.pdf",
            "filepath": f"/tmp/{source_id}.pdf",
            "file_type": "pdf",
            "file_size": 100,
            "content_hash": f"hash-{source_id}",
            "status": status,
        }
    )


@pytest.mark.asyncio
async def test_reconciler_skips_source_paused(in_memory_adapter) -> None:
    """A per-source pause continues to be respected."""
    queue = AsyncMock()
    queue.enqueue = AsyncMock()
    queue.task_exists_for_source = AsyncMock(return_value=False)

    _seed_non_terminal(in_memory_adapter, "src-p1")
    in_memory_adapter.set_source_paused(
        source_id="src-p1",
        database_name="default",
        is_paused=True,
        reason="test",
    )

    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.skipped_paused == 1
    assert stats.recovered == 0
    queue.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconciler_skips_all_when_system_paused(in_memory_adapter) -> None:
    """A system-wide pause skips every non-terminal source."""
    queue = AsyncMock()
    queue.enqueue = AsyncMock()
    queue.task_exists_for_source = AsyncMock(return_value=False)

    _seed_non_terminal(in_memory_adapter, "src-s1", status="pending")
    _seed_non_terminal(in_memory_adapter, "src-s2", status="indexing")
    _seed_non_terminal(in_memory_adapter, "src-s3", status="extracting")

    in_memory_adapter.set_system_paused(is_paused=True, reason="maintenance")

    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.skipped_paused == 3
    assert stats.recovered == 0
    queue.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconciler_recovers_after_system_unpaused(in_memory_adapter) -> None:
    """Clearing system pause lets the next pass recover the same sources."""
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t-new"})
    queue.task_exists_for_source = AsyncMock(return_value=False)

    _seed_non_terminal(in_memory_adapter, "src-r1", status="pending")

    in_memory_adapter.set_system_paused(is_paused=True, reason="maintenance")
    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    first = await recovery.reconcile_database(database_name="default")
    assert first.skipped_paused == 1
    queue.enqueue.assert_not_awaited()

    in_memory_adapter.set_system_paused(is_paused=False)
    second = await recovery.reconcile_database(database_name="default")
    assert second.recovered == 1
    queue.enqueue.assert_awaited_once()


@pytest.mark.asyncio
async def test_recover_source_short_circuit_on_system_pause(in_memory_adapter) -> None:
    """The single-source recover_source entry point also respects system pause.

    This is important for the resume endpoint (task 10): if the global
    pause is on, resuming a single source must still be a no-op so the
    user gets consistent "system paused" semantics.
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock()
    queue.task_exists_for_source = AsyncMock(return_value=False)

    _seed_non_terminal(in_memory_adapter, "src-rc1", status="pending")
    in_memory_adapter.set_system_paused(is_paused=True, reason="deploy")

    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    fired = await recovery.recover_source(source_id="src-rc1", database_name="default")

    assert fired is False
    queue.enqueue.assert_not_awaited()
