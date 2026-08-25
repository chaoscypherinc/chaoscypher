# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ChunkRerunService.

The service validates source + chunk state, delegates the atomic reset
to the adapter mixin, and enqueues OP_EXTRACT_CHUNK. Quality counter
increment is best-effort.
"""

from __future__ import annotations

from itertools import count
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.exceptions import ConflictError, NotFoundError
from chaoscypher_core.queue.client import QueueClient
from chaoscypher_cortex.features.sources.chunk_rerun_service import ChunkRerunService


@pytest.fixture
def mock_adapter() -> MagicMock:
    adapter = MagicMock()
    adapter.get_source = MagicMock(
        return_value={
            "id": "src-1",
            "database_name": "test",
            "status": "committed",
            "current_extraction_job_id": "job-1",
        }
    )
    adapter.get_chunk_task_by_source_and_index = MagicMock(
        return_value={
            "id": "tsk-1",
            "job_id": "job-1",
            "chunk_index": 0,
            "status": "completed",
            "small_chunk_ids": ["sc-1"],
        }
    )
    adapter.reset_chunk_task_for_rerun = MagicMock(return_value=1)
    adapter.increment_source_counter = MagicMock()
    return adapter


@pytest.fixture
def mock_queue_client() -> MagicMock:
    q = MagicMock()
    q.enqueue_task = AsyncMock(return_value="qt-1")
    return q


@pytest.fixture
def service(mock_adapter: MagicMock, mock_queue_client: MagicMock) -> ChunkRerunService:
    return ChunkRerunService(
        adapter=mock_adapter,
        queue_client=mock_queue_client,
        database_name="test",
    )


@pytest.mark.asyncio
async def test_rerun_chunk_happy_path(
    service: ChunkRerunService,
    mock_adapter: MagicMock,
    mock_queue_client: MagicMock,
) -> None:
    result = await service.rerun_chunk(source_id="src-1", chunk_index=0)

    assert result["chunk_task_id"] == "tsk-1"
    assert result["queue_task_id"] == "qt-1"
    assert result["attempt_number"] == 1
    assert result["source_status"] == "extracting"

    mock_adapter.reset_chunk_task_for_rerun.assert_called_once_with(
        task_id="tsk-1",
        source_id="src-1",
    )
    mock_queue_client.enqueue_task.assert_called_once()
    call_kwargs = mock_queue_client.enqueue_task.call_args.kwargs
    assert call_kwargs["operation"] == "extract_chunk"
    assert call_kwargs["data"]["chunk_task_id"] == "tsk-1"
    assert call_kwargs["data"]["chunk_index"] == 0


@pytest.mark.asyncio
async def test_rerun_chunk_404_missing_source(
    service: ChunkRerunService, mock_adapter: MagicMock
) -> None:
    mock_adapter.get_source.return_value = None
    with pytest.raises(NotFoundError):
        await service.rerun_chunk(source_id="nope", chunk_index=0)


@pytest.mark.asyncio
async def test_rerun_chunk_404_missing_chunk_task(
    service: ChunkRerunService, mock_adapter: MagicMock
) -> None:
    mock_adapter.get_chunk_task_by_source_and_index.return_value = None
    with pytest.raises(NotFoundError):
        await service.rerun_chunk(source_id="src-1", chunk_index=999)


@pytest.mark.asyncio
async def test_rerun_chunk_works_on_committed_source_with_null_current_job_id(
    service: ChunkRerunService, mock_adapter: MagicMock
) -> None:
    """Regression: a fully-committed source has ``current_extraction_job_id=None``.

    Prior behaviour 404'd because the service required the active-job pointer
    to find the chunk task. The lookup now goes via the job→source join, so
    the rerun succeeds as long as the chunk_task row still exists (which it
    does post-commit — those rows are only deleted when the source itself is
    deleted).
    """
    mock_adapter.get_source.return_value = {
        "id": "src-1",
        "database_name": "test",
        "status": "committed",
        "current_extraction_job_id": None,
    }
    result = await service.rerun_chunk(source_id="src-1", chunk_index=0)
    assert result["chunk_task_id"] == "tsk-1"
    assert result["source_status"] == "extracting"


@pytest.mark.asyncio
async def test_rerun_chunk_409_source_committing(
    service: ChunkRerunService, mock_adapter: MagicMock
) -> None:
    mock_adapter.get_source.return_value = {
        "id": "src-1",
        "database_name": "test",
        "status": "committing",
        "current_extraction_job_id": "job-1",
    }
    with pytest.raises(ConflictError) as ei:
        await service.rerun_chunk(source_id="src-1", chunk_index=0)
    assert "commit" in str(ei.value).lower()


@pytest.mark.asyncio
async def test_rerun_chunk_409_task_running(
    service: ChunkRerunService, mock_adapter: MagicMock
) -> None:
    mock_adapter.get_chunk_task_by_source_and_index.return_value = {
        "id": "tsk-1",
        "job_id": "job-1",
        "chunk_index": 0,
        "status": "running",
        "small_chunk_ids": ["sc-1"],
    }
    with pytest.raises(ConflictError):
        await service.rerun_chunk(source_id="src-1", chunk_index=0)


@pytest.mark.asyncio
async def test_rerun_chunk_409_when_reset_loses_race(
    service: ChunkRerunService, mock_adapter: MagicMock
) -> None:
    mock_adapter.reset_chunk_task_for_rerun.side_effect = ConflictError("lost race")
    with pytest.raises(ConflictError):
        await service.rerun_chunk(source_id="src-1", chunk_index=0)


@pytest.mark.asyncio
async def test_rerun_chunk_enqueue_failure_does_not_rollback_reset(
    service: ChunkRerunService,
    mock_adapter: MagicMock,
    mock_queue_client: MagicMock,
) -> None:
    """If enqueue fails AFTER the DB reset committed, we don't roll back.

    The reconciler will catch the orphan pending task within 60s.
    """
    mock_queue_client.enqueue_task.side_effect = RuntimeError("queue down")
    with pytest.raises(RuntimeError):
        await service.rerun_chunk(source_id="src-1", chunk_index=0)
    # Reset DID happen
    mock_adapter.reset_chunk_task_for_rerun.assert_called_once()


@pytest.mark.asyncio
async def test_rerun_chunk_increments_quality_counter(
    service: ChunkRerunService, mock_adapter: MagicMock
) -> None:
    await service.rerun_chunk(source_id="src-1", chunk_index=0)
    mock_adapter.increment_source_counter.assert_called_once()
    args = mock_adapter.increment_source_counter.call_args.kwargs
    assert args["column"] == "chunks_rerun_total"
    assert args["n"] == 1


# ---------------------------------------------------------------------------
# Producer -> consumer coupling (entry 764)
#
# QueueClient.in_flight_chunk_task_ids filters on metadata.source_id AND
# metadata.database_name. rerun_chunk already wrote source_id but was
# missing database_name, so this producer's tasks were invisible to the
# in-flight guard too. This test drives the REAL rerun_chunk against a
# REAL (store-backed, not mocked) QueueClient and calls the REAL consumer
# afterward -- unlike the mock-based tests above, it can't pass by
# asserting on call_args alone.
# ---------------------------------------------------------------------------


def _make_store_backed_queue_client() -> QueueClient:
    """Build a minimal QueueClient whose enqueue path writes into an in-memory store."""
    store: dict[str, dict[str, Any]] = {}

    client = QueueClient.__new__(QueueClient)
    client._connected = True
    client._max_pending_queue_depth = 10000
    client._operations_result_ttl = 7200
    client._llm_result_ttl = 3600

    pipeline = MagicMock()

    def _hset(key: str, mapping: dict[str, Any]) -> MagicMock:
        task_id = key.removeprefix("queue:task:")
        store[task_id] = dict(mapping)
        return pipeline

    pipeline.hset.side_effect = _hset
    pipeline.zadd.return_value = pipeline
    pipeline.lpush.return_value = pipeline
    pipeline.ltrim.return_value = pipeline
    pipeline.execute = AsyncMock(return_value=[])

    valkey = MagicMock()
    valkey.zcard = AsyncMock(return_value=0)
    valkey.pipeline = MagicMock(return_value=pipeline)
    valkey.exists = AsyncMock(return_value=0)
    # Pending-ZSET seq counter (queue FIFO tiebreaker, 2026-08-15).
    valkey.incr = AsyncMock(side_effect=lambda _key, _c=count(1): next(_c))
    valkey.incrby = AsyncMock(side_effect=lambda _key, amount: amount)

    def _scan_iter_factory(*_args: Any, **_kwargs: Any) -> Any:
        async def _gen() -> Any:
            for task_id in list(store):
                yield f"queue:task:{task_id}".encode()

        return _gen()

    valkey.scan_iter = _scan_iter_factory

    async def _hgetall(key: Any) -> dict[str, Any]:
        raw = key.decode() if isinstance(key, bytes) else key
        task_id = raw.removeprefix("queue:task:")
        return store.get(task_id, {})

    valkey.hgetall = AsyncMock(side_effect=_hgetall)

    client.client = valkey
    return client


@pytest.mark.asyncio
async def test_rerun_chunk_metadata_satisfies_in_flight_guard(
    mock_adapter: MagicMock,
) -> None:
    """rerun_chunk's metadata matches QueueClient.in_flight_chunk_task_ids' predicate.

    Regression for the missing ``database_name`` key: with only
    ``source_id`` stamped, the consumer's second filter
    (``meta.get("database_name") != database_name``) always missed and the
    task stayed invisible to the in-flight guard.
    """
    real_queue_client = _make_store_backed_queue_client()
    service = ChunkRerunService(
        adapter=mock_adapter,
        queue_client=real_queue_client,
        database_name="test",
    )

    result = await service.rerun_chunk(source_id="src-1", chunk_index=0)

    ids = await real_queue_client.in_flight_chunk_task_ids(source_id="src-1", database_name="test")
    assert ids == {"tsk-1"}
    assert result["chunk_task_id"] == "tsk-1"
