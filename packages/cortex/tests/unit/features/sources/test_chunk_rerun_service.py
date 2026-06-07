# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ChunkRerunService.

The service validates source + chunk state, delegates the atomic reset
to the adapter mixin, and enqueues OP_EXTRACT_CHUNK. Quality counter
increment is best-effort.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.exceptions import ConflictError, NotFoundError
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
