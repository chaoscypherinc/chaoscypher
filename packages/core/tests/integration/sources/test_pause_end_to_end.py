# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""End-to-end integration tests for pause/resume.

Uses a real file-backed SqliteAdapter (per-test via tmp_path) so
the full adapter → recovery roundtrip is exercised with real SQL,
real sessions, and real schema migrations. Queue is mocked
(AsyncMock) since the integration boundary is the adapter, not the
Valkey connection.

These tests live in core/ and must not import from cortex — the
PauseService/PauseRepository roundtrip is tested separately in
the cortex test suite (test_pause_service.py / test_pause_repository.py).
"""

from unittest.mock import AsyncMock

import pytest

from chaoscypher_core.services.sources.recovery import SourceRecovery


def _seed(adapter, source_id: str, status: str = "pending") -> None:
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
async def test_paused_source_not_redispatched(integration_adapter) -> None:
    """Paused source in a non-terminal state is not re-dispatched."""
    queue = AsyncMock()
    queue.enqueue = AsyncMock()
    queue.task_exists_for_source = AsyncMock(return_value=False)

    _seed(integration_adapter, "paused-src")
    integration_adapter.set_source_paused(
        source_id="paused-src",
        database_name="default",
        is_paused=True,
        reason="integration test",
    )

    recovery = SourceRecovery(adapter=integration_adapter, queue_client=queue)
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.skipped_paused == 1
    assert stats.recovered == 0
    queue.enqueue.assert_not_awaited()

    # Verify the source is genuinely paused in the DB
    source = integration_adapter.get_source(source_id="paused-src", database_name="default")
    assert source is not None
    assert source["is_paused"] is True
    assert source["paused_reason"] == "integration test"


@pytest.mark.asyncio
async def test_resume_clears_flag_and_recovery_dispatches(
    integration_adapter,
) -> None:
    """Clearing is_paused in the DB lets the next recovery pass dispatch work.

    Verifies the adapter roundtrip: set_source_paused(True) →
    recovery skips → set_source_paused(False) → recovery dispatches.
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t-new"})
    queue.task_exists_for_source = AsyncMock(return_value=False)

    _seed(integration_adapter, "resume-src")
    integration_adapter.set_source_paused(
        source_id="resume-src",
        database_name="default",
        is_paused=True,
        reason="test",
    )

    recovery = SourceRecovery(adapter=integration_adapter, queue_client=queue)

    # While paused, no dispatch
    stats = await recovery.reconcile_database(database_name="default")
    assert stats.skipped_paused == 1
    queue.enqueue.assert_not_awaited()

    # Clear pause
    integration_adapter.set_source_paused(
        source_id="resume-src",
        database_name="default",
        is_paused=False,
    )
    source = integration_adapter.get_source(source_id="resume-src", database_name="default")
    assert source is not None
    assert source["is_paused"] is False
    assert source["paused_at"] is None

    # Now recovery dispatches
    stats = await recovery.reconcile_database(database_name="default")
    assert stats.recovered == 1
    queue.enqueue.assert_awaited_once()


@pytest.mark.asyncio
async def test_system_pause_blocks_all_sources(integration_adapter) -> None:
    """System pause causes every non-terminal source to be skipped."""
    queue = AsyncMock()
    queue.enqueue = AsyncMock()
    queue.task_exists_for_source = AsyncMock(return_value=False)

    for i in range(3):
        _seed(integration_adapter, f"sys-src-{i}")

    integration_adapter.set_system_paused(is_paused=True, reason="test")

    recovery = SourceRecovery(adapter=integration_adapter, queue_client=queue)
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.skipped_paused == 3
    queue.enqueue.assert_not_awaited()

    # Verify system state persisted
    state = integration_adapter.get_system_state()
    assert state["processing_paused"] is True
    assert state["processing_paused_reason"] == "test"


@pytest.mark.asyncio
async def test_bulk_pause_and_resume_roundtrip(integration_adapter) -> None:
    """Bulk pause → verify → bulk resume → verify → recovery fires."""
    for i in range(3):
        _seed(integration_adapter, f"bulk-{i}")

    count = integration_adapter.bulk_set_sources_paused(
        source_ids=["bulk-0", "bulk-1", "bulk-2"],
        database_name="default",
        is_paused=True,
        reason="bulk test",
    )
    assert count == 3

    for i in range(3):
        source = integration_adapter.get_source(source_id=f"bulk-{i}", database_name="default")
        assert source is not None
        assert source["is_paused"] is True

    # Bulk resume
    count = integration_adapter.bulk_set_sources_paused(
        source_ids=["bulk-0", "bulk-1", "bulk-2"],
        database_name="default",
        is_paused=False,
    )
    assert count == 3

    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t-new"})
    queue.task_exists_for_source = AsyncMock(return_value=False)

    recovery = SourceRecovery(adapter=integration_adapter, queue_client=queue)
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 3
    assert queue.enqueue.await_count == 3
