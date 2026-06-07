# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source recovery events audit trail.

When SourceRecovery dispatches a real recovery action it must record an
event row so operators can see *what* fired, *when*, *which stage*,
*what action was dispatched*, and *what the classifier saw*. Without
this audit trail, the "auto-recovered N times" warning in the UI is
opaque — users have to grep logs to figure out whether the recoveries
were spurious or real.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from chaoscypher_core.services.sources.recovery import SourceRecovery


def _seed_extracting_source(adapter, source_id: str) -> None:
    adapter.create_source(
        {
            "id": source_id,
            "database_name": adapter.database_name,
            "filename": f"{source_id}.pdf",
            "filepath": f"/tmp/{source_id}.pdf",
            "file_type": "pdf",
            "file_size": 100,
            "content_hash": f"hash-{source_id}",
            "status": "extracting",
            "auto_analyze": True,
        }
    )


def test_record_recovery_event_persists_row(in_memory_adapter) -> None:
    """The new adapter method writes an event row that list_ can read back."""
    in_memory_adapter.create_source(
        {
            "id": "src-1",
            "database_name": in_memory_adapter.database_name,
            "filename": "x.pdf",
            "filepath": "/tmp/x.pdf",
            "file_type": "pdf",
            "file_size": 100,
            "content_hash": "h1",
            "status": "extracting",
        }
    )

    in_memory_adapter.record_recovery_event(
        source_id="src-1",
        database_name="default",
        from_status="extracting",
        action_taken="extract_chunk",
        reason="stalled",
        enqueued_count=2,
    )

    events = in_memory_adapter.list_recovery_events(
        source_id="src-1", database_name="default", limit=50
    )
    assert len(events) == 1
    e = events[0]
    assert e["source_id"] == "src-1"
    assert e["database_name"] == "default"
    assert e["from_status"] == "extracting"
    assert e["action_taken"] == "extract_chunk"
    assert e["reason"] == "stalled"
    assert e["enqueued_count"] == 2
    assert "attempt_at" in e
    assert "id" in e


def test_list_recovery_events_returns_newest_first(in_memory_adapter) -> None:
    """Recent events appear first so the UI panel shows the latest at the top."""
    in_memory_adapter.create_source(
        {
            "id": "src-2",
            "database_name": in_memory_adapter.database_name,
            "filename": "y.pdf",
            "filepath": "/tmp/y.pdf",
            "file_type": "pdf",
            "file_size": 100,
            "content_hash": "h2",
            "status": "extracting",
        }
    )

    in_memory_adapter.record_recovery_event(
        source_id="src-2",
        database_name="default",
        from_status="indexing",
        action_taken="index_document",
        reason="stalled",
        enqueued_count=1,
    )
    in_memory_adapter.record_recovery_event(
        source_id="src-2",
        database_name="default",
        from_status="extracting",
        action_taken="extract_chunk",
        reason="compound",
        enqueued_count=3,
    )

    events = in_memory_adapter.list_recovery_events(
        source_id="src-2", database_name="default", limit=50
    )
    assert len(events) == 2
    # Newest first.
    assert events[0]["from_status"] == "extracting"
    assert events[1]["from_status"] == "indexing"


def test_list_recovery_events_limit_is_honored(in_memory_adapter) -> None:
    in_memory_adapter.create_source(
        {
            "id": "src-3",
            "database_name": in_memory_adapter.database_name,
            "filename": "z.pdf",
            "filepath": "/tmp/z.pdf",
            "file_type": "pdf",
            "file_size": 100,
            "content_hash": "h3",
            "status": "extracting",
        }
    )
    for i in range(5):
        in_memory_adapter.record_recovery_event(
            source_id="src-3",
            database_name="default",
            from_status="extracting",
            action_taken="extract_chunk",
            reason="stalled",
            enqueued_count=i,
        )

    events = in_memory_adapter.list_recovery_events(
        source_id="src-3", database_name="default", limit=2
    )
    assert len(events) == 2


def test_list_recovery_events_scoped_per_source(in_memory_adapter) -> None:
    in_memory_adapter.create_source(
        {
            "id": "src-A",
            "database_name": in_memory_adapter.database_name,
            "filename": "a.pdf",
            "filepath": "/tmp/a.pdf",
            "file_type": "pdf",
            "file_size": 100,
            "content_hash": "ha",
            "status": "extracting",
        }
    )
    in_memory_adapter.create_source(
        {
            "id": "src-B",
            "database_name": in_memory_adapter.database_name,
            "filename": "b.pdf",
            "filepath": "/tmp/b.pdf",
            "file_type": "pdf",
            "file_size": 100,
            "content_hash": "hb",
            "status": "extracting",
        }
    )

    in_memory_adapter.record_recovery_event(
        source_id="src-A",
        database_name="default",
        from_status="extracting",
        action_taken="extract_chunk",
        reason="stalled",
        enqueued_count=1,
    )
    in_memory_adapter.record_recovery_event(
        source_id="src-B",
        database_name="default",
        from_status="extracting",
        action_taken="extract_chunk",
        reason="stalled",
        enqueued_count=2,
    )

    a_events = in_memory_adapter.list_recovery_events(
        source_id="src-A", database_name="default", limit=10
    )
    assert len(a_events) == 1
    assert a_events[0]["enqueued_count"] == 1


@pytest.mark.asyncio
async def test_recovery_service_records_event_on_real_dispatch(
    in_memory_adapter,
) -> None:
    """SourceRecovery._recover_one writes a recovery event after a real dispatch."""
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t"})
    queue.task_exists_for_source = AsyncMock(return_value=False)
    queue.in_flight_chunk_task_ids = AsyncMock(return_value=set())

    _seed_extracting_source(in_memory_adapter, source_id="src-event")
    job_id = "job-event"
    in_memory_adapter.create_extraction_job(
        job_id=job_id,
        source_id="src-event",
        database_name="default",
    )
    in_memory_adapter.update_extraction_job(job_id, {"status": "running", "total_chunks": 1})
    in_memory_adapter.create_chunk_task(
        task_id="task-1", job_id=job_id, database_name="default", chunk_index=0
    )

    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    await recovery.reconcile_database(database_name="default")

    events = in_memory_adapter.list_recovery_events(
        source_id="src-event", database_name="default", limit=10
    )
    assert len(events) == 1
    assert events[0]["from_status"] == "extracting"
    assert events[0]["enqueued_count"] >= 1


@pytest.mark.asyncio
async def test_recovery_service_skips_event_on_no_op(in_memory_adapter) -> None:
    """No event row when recovery is debounced (in-flight chunks)."""
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t"})
    queue.task_exists_for_source = AsyncMock(return_value=False)
    # Every chunk in flight on Valkey → classifier returns None → no dispatch.
    queue.in_flight_chunk_task_ids = AsyncMock(return_value={"task-1"})

    _seed_extracting_source(in_memory_adapter, source_id="src-noop-event")
    job_id = "job-noop-event"
    in_memory_adapter.create_extraction_job(
        job_id=job_id,
        source_id="src-noop-event",
        database_name="default",
    )
    in_memory_adapter.update_extraction_job(job_id, {"status": "running", "total_chunks": 1})
    in_memory_adapter.create_chunk_task(
        task_id="task-1", job_id=job_id, database_name="default", chunk_index=0
    )

    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    await recovery.reconcile_database(database_name="default")

    events = in_memory_adapter.list_recovery_events(
        source_id="src-noop-event", database_name="default", limit=10
    )
    assert events == [], "no-op recovery must not record an event"


def test_source_recovery_ports_protocol_includes_record_recovery_event() -> None:
    """The composite port surfaces both record_ and list_ methods."""
    from chaoscypher_core.ports.source_recovery import SourceRecoveryPorts

    assert hasattr(SourceRecoveryPorts, "record_recovery_event")
