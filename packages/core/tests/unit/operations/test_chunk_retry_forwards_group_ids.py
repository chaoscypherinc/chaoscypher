# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Regression: in-handler retry forwards hierarchical_group_id + small_chunk_ids.

Audit fix #H/core (chunk retry drops ids). The cancellation-retry
path forwards both kwargs (lines 752-760); the retryable-error path
at 919-926 used to drop them, causing the retried handler to skip
with reason='no_small_chunk_ids'.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.exceptions import LLMServiceError
from chaoscypher_core.operations.extraction.chunk_extraction_service import (
    ChunkExtractionOperationsService,
)


def _make_settings(max_retries: int = 3):
    return SimpleNamespace(
        retries=SimpleNamespace(extraction_chunk_max=max_retries),
        priorities=SimpleNamespace(background=5),
    )


def _make_adapter(retry_count: int = 0):
    adapter = MagicMock()
    adapter.get_chunk_task.return_value = {
        "id": "ct1",
        "retry_count": retry_count,
        "max_retries": 3,
    }
    adapter.get_extraction_job.return_value = {"source_id": "src1", "status": "running"}
    adapter.update_chunk_task = MagicMock()
    return adapter


@pytest.mark.asyncio
async def test_retryable_error_retry_forwards_group_ids() -> None:
    """queue_extract_chunk receives hierarchical_group_id + small_chunk_ids on retry."""
    service = ChunkExtractionOperationsService()
    queue_extract_chunk = AsyncMock(return_value="task_2")
    service.queue_extract_chunk = queue_extract_chunk

    adapter = _make_adapter(retry_count=0)
    settings = _make_settings(max_retries=3)
    exc = LLMServiceError(provider="ollama", model="qwen3", reason="transient")

    data = {
        "chunk_task_id": "ct1",
        "job_id": "j1",
        "database_name": "default",
        "chunk_index": 7,
        "hierarchical_group_id": "hg-abc",
        "small_chunk_ids": ["sc-1", "sc-2", "sc-3"],
    }

    await service._handle_chunk_failure(
        adapter=adapter,
        exc=exc,
        chunk_task_id="ct1",
        job_id="j1",
        database_name="default",
        chunk_index=7,
        settings=settings,
        data=data,
    )

    queue_extract_chunk.assert_awaited_once()
    kwargs = queue_extract_chunk.await_args.kwargs
    assert kwargs["chunk_task_id"] == "ct1"
    assert kwargs["job_id"] == "j1"
    assert kwargs["database_name"] == "default"
    assert kwargs["chunk_index"] == 7
    # The two formerly-dropped kwargs:
    assert kwargs["hierarchical_group_id"] == "hg-abc"
    assert kwargs["small_chunk_ids"] == ["sc-1", "sc-2", "sc-3"]


@pytest.mark.asyncio
async def test_retryable_error_retry_forwards_none_group_ids_when_absent() -> None:
    """queue_extract_chunk still runs when data has no group/chunk id keys."""
    service = ChunkExtractionOperationsService()
    queue_extract_chunk = AsyncMock(return_value="task_2")
    service.queue_extract_chunk = queue_extract_chunk

    adapter = _make_adapter(retry_count=0)
    settings = _make_settings(max_retries=3)
    exc = LLMServiceError(provider="ollama", model="qwen3", reason="transient")

    await service._handle_chunk_failure(
        adapter=adapter,
        exc=exc,
        chunk_task_id="ct1",
        job_id="j1",
        database_name="default",
        chunk_index=7,
        settings=settings,
        data={},
    )

    queue_extract_chunk.assert_awaited_once()
    kwargs = queue_extract_chunk.await_args.kwargs
    assert "hierarchical_group_id" in kwargs
    assert "small_chunk_ids" in kwargs
    assert kwargs["hierarchical_group_id"] is None
    assert kwargs["small_chunk_ids"] is None


def _assert_repointed_to(adapter: MagicMock, new_queue_task_id: str) -> None:
    """The retry clears the stale pointer, then repoints it at the new task."""
    calls = adapter.update_chunk_task.call_args_list
    assert calls[0].args[0] == "ct1"
    assert calls[0].args[1]["status"] == "queued"
    assert calls[0].args[1]["queue_task_id"] is None
    assert calls[-1].args[0] == "ct1"
    repoint = calls[-1].args[1]
    assert repoint["queue_task_id"] == new_queue_task_id
    assert repoint["queued_at"] is not None
    # Pointer-only write: must not flip a row the new delivery already claimed.
    assert "status" not in repoint


@pytest.mark.asyncio
async def test_retryable_error_retry_repoints_queue_task_id() -> None:
    """The retryable-error requeue stamps the NEW queue task id on the row."""
    service = ChunkExtractionOperationsService()
    service.queue_extract_chunk = AsyncMock(return_value="task_2")
    adapter = _make_adapter(retry_count=0)

    await service._handle_chunk_failure(
        adapter=adapter,
        exc=LLMServiceError(provider="ollama", model="qwen3", reason="transient"),
        chunk_task_id="ct1",
        job_id="j1",
        database_name="default",
        chunk_index=7,
        settings=_make_settings(max_retries=3),
        data={},
    )

    _assert_repointed_to(adapter, "task_2")


@pytest.mark.asyncio
async def test_cancellation_retry_repoints_queue_task_id() -> None:
    """The cancellation requeue stamps the NEW queue task id on the row."""
    service = ChunkExtractionOperationsService()
    service.queue_extract_chunk = AsyncMock(return_value="task_3")
    adapter = _make_adapter(retry_count=0)

    await service._handle_chunk_cancellation(
        adapter=adapter,
        data={},
        chunk_task_id="ct1",
        job_id="j1",
        database_name="default",
        chunk_index=7,
        settings=_make_settings(max_retries=3),
    )

    _assert_repointed_to(adapter, "task_3")
