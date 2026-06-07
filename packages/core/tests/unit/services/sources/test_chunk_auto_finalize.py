# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for atomic chunk counter + auto-finalize trigger.

The chunk extraction handler calls ``_update_chunk_progress`` after
each chunk lands. That method delegates to an adapter-level atomic
counter increment (``increment_job_completed_and_check``) and then,
if the returned state is terminal, enqueues OP_FINALIZE_EXTRACTION.
These tests pin both halves of the contract so a future change can't
quietly drop either the atomicity call or the finalize enqueue.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_last_chunk_triggers_finalization() -> None:
    """When the atomic increment reports ``is_terminal``, queue finalize."""
    from chaoscypher_core.operations.extraction.chunk_extraction_service import (
        ChunkExtractionOperationsService,
    )

    adapter = MagicMock()
    adapter.increment_job_completed_and_check = MagicMock(
        return_value={
            "completed": 10,
            "failed": 0,
            "total": 10,
            "is_terminal": True,
        }
    )
    adapter.get_extraction_job = MagicMock(
        return_value={
            "id": "job-1",
            "source_id": "src-1",
            "generate_embeddings": True,
        }
    )
    adapter.update_step_progress = MagicMock()

    settings = MagicMock()
    settings.priorities.background = 50

    service = ChunkExtractionOperationsService(source_repository=adapter)

    with patch.object(
        service,
        "queue_finalize_extraction",
        new=AsyncMock(return_value="finalize-t"),
    ) as mock_finalize:
        await service._update_chunk_progress(
            adapter=adapter,
            job_id="job-1",
            source_id="src-1",
            database_name="default",
            chunk_task_id="t-1",
            chunk_index=0,
            task_outcome="completed",
            settings=settings,
        )

    adapter.increment_job_completed_and_check.assert_called_once_with(
        job_id="job-1", database_name="default", outcome="completed"
    )
    mock_finalize.assert_awaited_once()
    call_kwargs = mock_finalize.await_args.kwargs
    assert call_kwargs["job_id"] == "job-1"
    assert call_kwargs["source_id"] == "src-1"
    assert call_kwargs["database_name"] == "default"


@pytest.mark.asyncio
async def test_non_terminal_chunk_does_not_trigger_finalization() -> None:
    """Mid-job chunks update the counter but do NOT enqueue finalize."""
    from chaoscypher_core.operations.extraction.chunk_extraction_service import (
        ChunkExtractionOperationsService,
    )

    adapter = MagicMock()
    adapter.increment_job_completed_and_check = MagicMock(
        return_value={
            "completed": 5,
            "failed": 0,
            "total": 10,
            "is_terminal": False,
        }
    )
    adapter.update_step_progress = MagicMock()

    settings = MagicMock()
    settings.priorities.background = 50

    service = ChunkExtractionOperationsService(source_repository=adapter)

    with patch.object(
        service,
        "queue_finalize_extraction",
        new=AsyncMock(),
    ) as mock_finalize:
        await service._update_chunk_progress(
            adapter=adapter,
            job_id="job-1",
            source_id="src-1",
            database_name="default",
            chunk_task_id="t-5",
            chunk_index=5,
            task_outcome="completed",
            settings=settings,
        )

    mock_finalize.assert_not_awaited()


@pytest.mark.asyncio
async def test_last_failed_chunk_also_triggers_finalization() -> None:
    """A terminal-by-failure job is still finalized (not left hanging)."""
    from chaoscypher_core.operations.extraction.chunk_extraction_service import (
        ChunkExtractionOperationsService,
    )

    adapter = MagicMock()
    adapter.increment_job_completed_and_check = MagicMock(
        return_value={
            "completed": 9,
            "failed": 1,
            "total": 10,
            "is_terminal": True,
        }
    )
    adapter.get_extraction_job = MagicMock(
        return_value={
            "id": "job-1",
            "source_id": "src-1",
            "generate_embeddings": True,
        }
    )
    adapter.update_step_progress = MagicMock()

    settings = MagicMock()
    settings.priorities.background = 50

    service = ChunkExtractionOperationsService(source_repository=adapter)

    with patch.object(
        service,
        "queue_finalize_extraction",
        new=AsyncMock(return_value="finalize-t"),
    ) as mock_finalize:
        await service._update_chunk_progress(
            adapter=adapter,
            job_id="job-1",
            source_id="src-1",
            database_name="default",
            chunk_task_id="t-10",
            chunk_index=9,
            task_outcome="failed",
            settings=settings,
        )

    adapter.increment_job_completed_and_check.assert_called_once_with(
        job_id="job-1", database_name="default", outcome="failed"
    )
    mock_finalize.assert_awaited_once()
