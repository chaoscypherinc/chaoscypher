# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Retryable LLM failures requeue the chunk task; non-retryable do not.

Mirrors the cancellation-retry contract of _handle_chunk_cancellation
for the general-exception path. A transient LLMRateLimitError must
not lose the chunk's data on first hiccup; a fatal LLMModelError
must continue to fail fast.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.constants import OP_EXTRACT_CHUNK, OP_FINALIZE_EXTRACTION
from chaoscypher_core.exceptions import (
    LLMAuthenticationError,
    LLMRateLimitError,
    LLMServiceError,
)
from chaoscypher_core.operations.extraction.chunk_extraction_service import (
    ChunkExtractionOperationsService,
)


def _make_settings(max_retries: int = 2):
    return SimpleNamespace(
        retries=SimpleNamespace(extraction_chunk_max=max_retries),
        priorities=SimpleNamespace(background=10),
    )


def _make_adapter(retry_count: int = 0):
    adapter = MagicMock()
    adapter.get_chunk_task.return_value = {"retry_count": retry_count}
    adapter.get_extraction_job.return_value = {"source_id": "src1", "status": "running"}
    return adapter


def test_extract_chunk_owns_its_transient_retry_budget():
    """extract_chunk requeues itself; the queue must not multiply retries."""
    service = ChunkExtractionOperationsService()

    chunk_spec = service.operation_handlers[OP_EXTRACT_CHUNK]

    assert chunk_spec.retry_on_crash is True
    assert chunk_spec.retry_on_transient is False


def test_finalize_extraction_relies_on_queue_transient_retries():
    """finalize_extraction has no domain transient-retry counter for inline errors.

    The only domain retry counter (``finalize_retry_count``) lives inside
    ``_handle_finalize_cancellation`` and only fires on ``asyncio.CancelledError``.
    A plain transient error raised during aggregation/embedding/dedup/commit
    has no domain retry budget, so the queue-level transient retry path
    must stay enabled (default) — otherwise a single network blip fails the
    finalize permanently and recovery falls to the slow SourceRecovery loop.
    """
    service = ChunkExtractionOperationsService()

    finalize_spec = service.operation_handlers[OP_FINALIZE_EXTRACTION]

    assert finalize_spec.retry_on_crash is True
    assert finalize_spec.retry_on_transient is True


@pytest.mark.asyncio
async def test_retryable_llm_error_below_max_requeues_chunk():
    service = ChunkExtractionOperationsService()
    service.queue_extract_chunk = AsyncMock(return_value="task_2")
    adapter = _make_adapter(retry_count=0)
    settings = _make_settings(max_retries=2)
    exc = LLMRateLimitError(provider="openai", retry_after=1)

    result = await service._handle_chunk_failure(
        adapter=adapter,
        exc=exc,
        chunk_task_id="ct1",
        job_id="job1",
        database_name="db",
        chunk_index=0,
        settings=settings,
    )

    adapter.update_chunk_task.assert_called_once()
    update_args = adapter.update_chunk_task.call_args
    assert update_args[0][0] == "ct1"
    assert update_args[0][1]["status"] == "queued"
    assert update_args[0][1]["retry_count"] == 1
    service.queue_extract_chunk.assert_awaited_once()
    adapter.fail_chunk_task.assert_not_called()
    assert result["success"] is False
    assert result["retry_count"] == 1


@pytest.mark.asyncio
async def test_retryable_llm_error_at_max_fails_permanently():
    service = ChunkExtractionOperationsService()
    service.queue_extract_chunk = AsyncMock()
    service._update_chunk_progress = AsyncMock()
    adapter = _make_adapter(retry_count=2)
    settings = _make_settings(max_retries=2)
    exc = LLMServiceError(provider="openai", reason="503")

    result = await service._handle_chunk_failure(
        adapter=adapter,
        exc=exc,
        chunk_task_id="ct1",
        job_id="job1",
        database_name="db",
        chunk_index=0,
        settings=settings,
    )

    adapter.fail_chunk_task.assert_called_once()
    service.queue_extract_chunk.assert_not_awaited()
    service._update_chunk_progress.assert_awaited_once()
    assert result["success"] is False
    assert result.get("max_retries_exceeded") is True


@pytest.mark.asyncio
async def test_non_retryable_llm_error_fails_immediately():
    service = ChunkExtractionOperationsService()
    service.queue_extract_chunk = AsyncMock()
    service._update_chunk_progress = AsyncMock()
    adapter = _make_adapter(retry_count=0)
    settings = _make_settings(max_retries=2)
    exc = LLMAuthenticationError(provider="openai")

    result = await service._handle_chunk_failure(
        adapter=adapter,
        exc=exc,
        chunk_task_id="ct1",
        job_id="job1",
        database_name="db",
        chunk_index=0,
        settings=settings,
    )

    adapter.fail_chunk_task.assert_called_once()
    adapter.update_chunk_task.assert_not_called()
    service.queue_extract_chunk.assert_not_awaited()
    assert result["success"] is False


@pytest.mark.asyncio
async def test_generic_exception_fails_immediately_without_retry():
    """Plain Exception is not retryable — preserves current behavior."""
    service = ChunkExtractionOperationsService()
    service.queue_extract_chunk = AsyncMock()
    service._update_chunk_progress = AsyncMock()
    adapter = _make_adapter(retry_count=0)
    settings = _make_settings(max_retries=2)
    exc = RuntimeError("boom")

    result = await service._handle_chunk_failure(
        adapter=adapter,
        exc=exc,
        chunk_task_id="ct1",
        job_id="job1",
        database_name="db",
        chunk_index=0,
        settings=settings,
    )

    adapter.fail_chunk_task.assert_called_once()
    adapter.update_chunk_task.assert_not_called()
    service.queue_extract_chunk.assert_not_awaited()
    assert result["success"] is False


@pytest.mark.asyncio
async def test_retryable_llm_error_propagates_storage_failure_during_requeue():
    """If adapter.update_chunk_task raises during requeue, the storage
    error propagates so the calling task is marked failed by the
    queue infrastructure (matches the cancellation handler's
    contract).
    """
    service = ChunkExtractionOperationsService()
    service.queue_extract_chunk = AsyncMock()
    adapter = _make_adapter(retry_count=0)
    adapter.update_chunk_task.side_effect = RuntimeError("sqlite locked")
    settings = _make_settings(max_retries=2)
    exc = LLMRateLimitError(provider="openai", retry_after=1)

    with pytest.raises(RuntimeError, match="sqlite locked"):
        await service._handle_chunk_failure(
            adapter=adapter,
            exc=exc,
            chunk_task_id="ct1",
            job_id="job1",
            database_name="db",
            chunk_index=0,
            settings=settings,
        )

    # The requeue did NOT happen because update failed first.
    service.queue_extract_chunk.assert_not_awaited()
