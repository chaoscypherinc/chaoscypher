# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Failed re-enqueues in retry paths are logged, not silently swallowed.

Workstream 8 (2026-05-07) Task 8.5 — four ``with suppress(Exception)``
blocks in retry paths used to hide queue-enqueue failures completely.
The recovery safety nets (SourceRecovery 60s loop, startup rehydrate)
still pick up the orphaned work; this change makes the moment of
failure queryable instead of invisible.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog.testing


@pytest.mark.asyncio
async def test_chunk_requeue_enqueue_failure_logs_warning() -> None:
    """``_handle_chunk_cancellation`` logs ``chunk_requeue_enqueue_failed`` on enqueue failure."""
    from chaoscypher_core.operations.extraction.chunk_extraction_service import (
        ChunkExtractionOperationsService,
    )

    service = ChunkExtractionOperationsService(source_repository=MagicMock())
    service.queue_extract_chunk = AsyncMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("queue down")
    )

    adapter = MagicMock()
    adapter.get_chunk_task = MagicMock(return_value={"retry_count": 0})
    adapter.update_chunk_task = MagicMock()

    settings = MagicMock()
    settings.retries = MagicMock(extraction_chunk_max=3)
    settings.priorities = MagicMock(background=10)

    with structlog.testing.capture_logs() as captured:
        result = await service._handle_chunk_cancellation(
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

    assert result["success"] is False
    events = [e["event"] for e in captured]
    assert "chunk_requeue_enqueue_failed" in events
    matched = next(e for e in captured if e["event"] == "chunk_requeue_enqueue_failed")
    assert matched["chunk_task_id"] == "t1"
    assert matched["job_id"] == "j1"


@pytest.mark.asyncio
async def test_chunk_retry_requeue_failure_logs_warning() -> None:
    """``_handle_chunk_failure`` logs ``chunk_retry_requeue_failed`` on enqueue failure."""
    from chaoscypher_core.operations.extraction.chunk_extraction_service import (
        ChunkExtractionOperationsService,
    )

    class _RetryableError(Exception):
        is_retryable = True

    service = ChunkExtractionOperationsService(source_repository=MagicMock())
    service.queue_extract_chunk = AsyncMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("queue down")
    )

    adapter = MagicMock()
    adapter.get_chunk_task = MagicMock(return_value={"retry_count": 0})
    adapter.update_chunk_task = MagicMock()
    adapter.fail_chunk_task = MagicMock()

    settings = MagicMock()
    settings.retries = MagicMock(extraction_chunk_max=3)
    settings.priorities = MagicMock(background=10)

    with structlog.testing.capture_logs() as captured:
        result = await service._handle_chunk_failure(
            adapter=adapter,
            exc=_RetryableError("transient LLM 503"),
            chunk_task_id="t1",
            job_id="j1",
            database_name="default",
            chunk_index=0,
            settings=settings,
            data={"small_chunk_ids": []},
        )

    assert result["success"] is False
    events = [e["event"] for e in captured]
    assert "chunk_retry_requeue_failed" in events


@pytest.mark.asyncio
async def test_finalize_requeue_failure_logs_warning() -> None:
    """``_handle_finalize_cancellation`` logs ``finalize_requeue_failed`` on enqueue failure."""
    from chaoscypher_core.operations.extraction import extraction_finalizer

    settings = MagicMock()
    settings.retries = MagicMock(extraction_finalize_max=3)
    settings.priorities = MagicMock(background=10)

    adapter = MagicMock()

    fake_qc = MagicMock()
    fake_qc.enqueue_task = AsyncMock(side_effect=RuntimeError("queue down"))

    with pytest.MonkeyPatch.context() as mp, structlog.testing.capture_logs() as captured:
        mp.setattr(extraction_finalizer, "queue_client", fake_qc)
        result = await extraction_finalizer._handle_finalize_cancellation(
            adapter=adapter,
            data={"finalize_retry_count": 0},
            job_id="j1",
            source_id="s1",
            database_name="default",
            settings=settings,
        )

    assert result["success"] is False
    events = [e["event"] for e in captured]
    assert "finalize_requeue_failed" in events


@pytest.mark.asyncio
async def test_next_waiting_extraction_dispatch_failure_logs_warning() -> None:
    """The cancellation-max-retries path logs when next-waiting dispatch fails."""
    from chaoscypher_core.operations.extraction import extraction_finalizer

    settings = MagicMock()
    settings.retries = MagicMock(extraction_finalize_max=0)  # force max-retries path
    settings.priorities = MagicMock(background=10)

    adapter = MagicMock()

    async def _boom(*_a: Any, **_kw: Any) -> None:
        raise RuntimeError("downstream dispatch broke")

    with pytest.MonkeyPatch.context() as mp, structlog.testing.capture_logs() as captured:
        mp.setattr(
            extraction_finalizer,
            "trigger_next_waiting_extraction",
            _boom,
        )
        result = await extraction_finalizer._handle_finalize_cancellation(
            adapter=adapter,
            data={"finalize_retry_count": 1},
            job_id="j1",
            source_id="s1",
            database_name="default",
            settings=settings,
        )

    assert result["success"] is False
    events = [e["event"] for e in captured]
    assert "next_waiting_extraction_dispatch_failed" in events
