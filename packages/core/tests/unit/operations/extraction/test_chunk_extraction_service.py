# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for ChunkExtractionOperationsService cancellation behaviour.

Task 4 (2026-05-08): Fix cancellation-path chunk leak during shutdown.

When ``_handle_chunk_cancellation`` requeues a chunk and the requeue call
itself receives ``asyncio.CancelledError`` (graceful worker shutdown),
the handler must re-raise — not swallow — so the worker's shutdown path
sees the cancel correctly and the chunk is not left in a half-cancelled /
unrequeued state with the worker thinking the task completed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog.testing


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def cancellation_setup() -> Generator[tuple[object, MagicMock, MagicMock]]:
    """Yield ``(service, adapter, settings)`` wired for the requeue-cancel
    scenario: ``queue_extract_chunk`` always raises ``CancelledError``,
    ``get_chunk_task`` returns ``retry_count=0``, and ``extraction_chunk_max``
    is 3 (so the retry branch is taken).
    """
    from chaoscypher_core.operations.extraction.chunk_extraction_service import (
        ChunkExtractionOperationsService,
    )

    service = ChunkExtractionOperationsService(source_repository=MagicMock())
    service.queue_extract_chunk = AsyncMock(  # type: ignore[method-assign]
        side_effect=asyncio.CancelledError()
    )

    adapter = MagicMock()
    adapter.get_chunk_task = MagicMock(return_value={"retry_count": 0})
    adapter.update_chunk_task = MagicMock()

    settings = MagicMock()
    settings.retries = MagicMock(extraction_chunk_max=3)
    settings.priorities = MagicMock(background=10)

    return service, adapter, settings


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancellation_during_requeue_reraises_cancel(
    cancellation_setup: tuple[object, MagicMock, MagicMock],
) -> None:
    """If the requeue itself is cancelled (graceful shutdown), the handler
    must re-raise CancelledError after logging — silently returning would
    leave the chunk in a half-cancelled state with the worker thinking
    the task completed.
    """
    service, adapter, settings = cancellation_setup

    with pytest.raises(asyncio.CancelledError):
        await service._handle_chunk_cancellation(
            adapter=adapter,
            data={
                "chunk_task_id": "t1",
                "job_id": "j1",
                "small_chunk_ids": [],
            },
            chunk_task_id="t1",
            job_id="j1",
            database_name="default",
            chunk_index=0,
            settings=settings,
        )


@pytest.mark.asyncio
async def test_cancellation_during_requeue_logs_warning(
    cancellation_setup: tuple[object, MagicMock, MagicMock],
) -> None:
    """A ``chunk_requeue_cancelled_during_shutdown`` warning is emitted
    before the CancelledError is re-raised, making the event queryable.
    The warning must carry all five operator-context fields: ``chunk_task_id``,
    ``job_id``, ``chunk_index``, ``retry_count``, and ``database_name``.
    """
    service, adapter, settings = cancellation_setup

    with structlog.testing.capture_logs() as captured, pytest.raises(asyncio.CancelledError):
        await service._handle_chunk_cancellation(
            adapter=adapter,
            data={
                "chunk_task_id": "t1",
                "job_id": "j1",
                "small_chunk_ids": [],
            },
            chunk_task_id="t1",
            job_id="j1",
            database_name="default",
            chunk_index=0,
            settings=settings,
        )

    events = [e["event"] for e in captured]
    assert "chunk_requeue_cancelled_during_shutdown" in events
    matched = next(e for e in captured if e["event"] == "chunk_requeue_cancelled_during_shutdown")
    # adapter.get_chunk_task returns retry_count=0, so current_retries+1 == 1
    assert matched["chunk_task_id"] == "t1"
    assert matched["job_id"] == "j1"
    assert matched["chunk_index"] == 0
    assert matched["retry_count"] == 1
    assert matched["database_name"] == "default"


# ---------------------------------------------------------------------------
# Task 5: LLM_CHUNKS_TIMED_OUT + LLM_CHUNKS_FAILED_PERMANENT counters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chunk_cancellation_at_max_retries_increments_timed_out() -> None:
    """When retry budget is exhausted on a CancelledError, LLM_CHUNKS_TIMED_OUT
    must be incremented exactly once on the source row.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from chaoscypher_core.operations.extraction.chunk_extraction_service import (
        ChunkExtractionOperationsService,
    )

    service = ChunkExtractionOperationsService(source_repository=MagicMock())

    adapter = MagicMock()
    # retry_count already at max so the final-failure branch is taken
    adapter.get_chunk_task = MagicMock(return_value={"retry_count": 3})
    adapter.fail_chunk_task = MagicMock()
    adapter.get_extraction_job = MagicMock(return_value={"source_id": "src-1"})

    settings = MagicMock()
    settings.retries = MagicMock(extraction_chunk_max=3)
    settings.priorities = MagicMock(background=10)

    increment_mock = AsyncMock()

    with patch(
        "chaoscypher_core.services.quality.counters.increment_quality_counter",
        new=increment_mock,
    ):
        # Also patch _update_chunk_progress to avoid needing full adapter wiring
        service._update_chunk_progress = AsyncMock()  # type: ignore[method-assign]

        result = await service._handle_chunk_cancellation(
            adapter=adapter,
            data={"chunk_task_id": "t-cancel", "job_id": "j-cancel", "small_chunk_ids": []},
            chunk_task_id="t-cancel",
            job_id="j-cancel",
            database_name="default",
            chunk_index=0,
            settings=settings,
        )

    assert result["max_retries_exceeded"] is True

    from chaoscypher_core.services.quality.counters import QualityCounter

    increment_mock.assert_awaited_once_with(
        adapter=adapter,
        source_id="src-1",
        database_name="default",
        counter=QualityCounter.LLM_CHUNKS_TIMED_OUT,
    )


@pytest.mark.asyncio
async def test_chunk_failure_at_max_retries_increments_failed_permanent() -> None:
    """When a retryable exception exhausts its retry budget, LLM_CHUNKS_FAILED_PERMANENT
    must be incremented exactly once on the source row.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from chaoscypher_core.operations.extraction.chunk_extraction_service import (
        ChunkExtractionOperationsService,
    )

    # Build a retryable exception: is_retryable=True, not a fatal LLM error
    retryable_exc = RuntimeError("transient")
    retryable_exc.is_retryable = True  # type: ignore[attr-defined]

    service = ChunkExtractionOperationsService(source_repository=MagicMock())

    adapter = MagicMock()
    adapter.get_chunk_task = MagicMock(return_value={"retry_count": 3})
    adapter.fail_chunk_task = MagicMock()
    adapter.get_extraction_job = MagicMock(return_value={"source_id": "src-2"})

    settings = MagicMock()
    settings.retries = MagicMock(extraction_chunk_max=3)
    settings.priorities = MagicMock(background=10)

    increment_mock = AsyncMock()

    with patch(
        "chaoscypher_core.services.quality.counters.increment_quality_counter",
        new=increment_mock,
    ):
        service._update_chunk_progress = AsyncMock()  # type: ignore[method-assign]

        result = await service._handle_chunk_failure(
            adapter=adapter,
            exc=retryable_exc,
            chunk_task_id="t-fail",
            job_id="j-fail",
            database_name="default",
            chunk_index=0,
            settings=settings,
            data={},
        )

    assert result["max_retries_exceeded"] is True

    from chaoscypher_core.services.quality.counters import QualityCounter

    increment_mock.assert_awaited_once_with(
        adapter=adapter,
        source_id="src-2",
        database_name="default",
        counter=QualityCounter.LLM_CHUNKS_FAILED_PERMANENT,
    )
