# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for DB-level idempotency of the chunk extraction handler.

The chunk extraction handler (`_extract_chunk_handler`) is the work
horse of crash-resume — it's the one that can be
re-dispatched to the LLM queue after a crash, and it absolutely must
not re-execute LLM calls for tasks whose DB row is already in a
terminal state. These tests pin the short-circuit so it can't silently
regress later.
"""

from unittest.mock import MagicMock

import pytest


@pytest.mark.asyncio
async def test_handler_short_circuits_when_task_already_completed() -> None:
    """Re-dispatched task whose DB row is already completed returns a skipped result.

    It does not call the LLM.
    """
    from chaoscypher_core.operations.extraction.chunk_extraction_service import (
        ChunkExtractionOperationsService,
    )

    adapter = MagicMock()
    adapter.get_extraction_job = MagicMock(
        return_value={
            "id": "job-1",
            "status": "running",
            "source_id": "s-1",
        }
    )
    adapter.get_chunk_task = MagicMock(
        return_value={
            "id": "t-1",
            "job_id": "job-1",
            "status": "completed",
            "chunk_index": 0,
        }
    )

    service = ChunkExtractionOperationsService(source_repository=adapter)

    # Mirror the dispatcher's calling convention so signature drift surfaces
    # here too — see chaoscypher_core.queue.service._execute_handler.
    result = await service._extract_chunk_handler(
        data={
            "chunk_task_id": "t-1",
            "job_id": "job-1",
            "database_name": "default",
            "chunk_content": "already done",
            "chunk_index": 0,
        },
        metadata=None,
        task_id="test-task-already-completed",
    )

    assert result["skipped"] is True
    assert result["reason"] == "task_already_completed"
    # Adapter was consulted for job + task, but never for start_chunk_task_with_input
    adapter.get_extraction_job.assert_called()
    adapter.get_chunk_task.assert_called_once_with("t-1")
    # Never reached the LLM path
    adapter.start_chunk_task_with_input.assert_not_called()


@pytest.mark.asyncio
async def test_handler_short_circuits_when_job_already_finalized() -> None:
    """When the parent job is in a terminal state, chunk work is skipped.

    Covers the case where finalization has already run (maybe from a
    previous attempt) but a stale chunk task was re-queued by the queue
    reconciler.
    """
    from chaoscypher_core.operations.extraction.chunk_extraction_service import (
        ChunkExtractionOperationsService,
    )

    adapter = MagicMock()
    adapter.get_extraction_job = MagicMock(
        return_value={
            "id": "job-1",
            "status": "completed",
            "source_id": "s-1",
        }
    )
    adapter.get_chunk_task = MagicMock(
        return_value={
            "id": "t-1",
            "job_id": "job-1",
            "status": "pending",
            "chunk_index": 0,
        }
    )

    service = ChunkExtractionOperationsService(source_repository=adapter)

    # Dispatcher-style call.
    result = await service._extract_chunk_handler(
        data={
            "chunk_task_id": "t-1",
            "job_id": "job-1",
            "database_name": "default",
            "chunk_content": "stale",
            "chunk_index": 0,
        },
        metadata=None,
        task_id="test-job-already-finished",
    )

    assert result["skipped"] is True
    assert result["reason"] == "job_already_finished"
    adapter.start_chunk_task_with_input.assert_not_called()


@pytest.mark.asyncio
async def test_handler_short_circuits_when_task_row_missing() -> None:
    """A queued task id whose DB row was deleted is a skip, not an error.

    For example, an admin reset may have removed the row.
    """
    from chaoscypher_core.operations.extraction.chunk_extraction_service import (
        ChunkExtractionOperationsService,
    )

    adapter = MagicMock()
    adapter.get_extraction_job = MagicMock(
        return_value={
            "id": "job-1",
            "status": "running",
            "source_id": "s-1",
        }
    )
    adapter.get_chunk_task = MagicMock(return_value=None)

    service = ChunkExtractionOperationsService(source_repository=adapter)

    # Dispatcher-style call.
    result = await service._extract_chunk_handler(
        data={
            "chunk_task_id": "t-1",
            "job_id": "job-1",
            "database_name": "default",
            "chunk_content": "orphan",
            "chunk_index": 0,
        },
        metadata=None,
        task_id="test-task-not-found",
    )

    assert result["skipped"] is True
    assert result["reason"] == "task_not_found"
    adapter.start_chunk_task_with_input.assert_not_called()
