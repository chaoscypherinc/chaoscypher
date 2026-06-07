# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for chunk_attempts endpoints (list + detail)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chaoscypher_core.exceptions import NotFoundError
from chaoscypher_cortex.features.sources.chunk_attempts_api import (
    get_chunk_attempt_endpoint,
    list_chunk_attempts_endpoint,
)
from chaoscypher_cortex.features.sources.chunk_attempts_service import (
    ChunkAttemptsService,
)


def _summary_row(attempt_number: int = 1) -> dict:
    return {
        "id": f"a{attempt_number}",
        "chunk_task_id": "tsk-1",
        "attempt_number": attempt_number,
        "snapshotted_at": "2026-05-15T00:00:00Z",
        "started_at": None,
        "completed_at": None,
        "entity_count": 0,
        "relationship_count": 0,
        "invalid_relationship_count": 0,
        "finish_reason": "stop",
        "aborted_by_loop": None,
        "llm_duration_ms": 1000,
        "input_tokens": 10,
        "output_tokens": 20,
        "input_text_length": 5,
        "llm_response_length": 20,
        "error_message": None,
        "error_type": None,
    }


def _detail_row() -> dict:
    return {
        **_summary_row(),
        "input_text": "hello",
        "llm_response_json": "{}",
        "raw_entities": [],
        "raw_relationships": [],
        "filtering_log": None,
        "chunk_sentences": None,
    }


@pytest.fixture
def mock_adapter() -> MagicMock:
    adapter = MagicMock()
    adapter.get_source = MagicMock(
        return_value={
            "id": "src-1",
            "database_name": "test",
            "current_extraction_job_id": "job-1",
        }
    )
    adapter.get_chunk_task_by_job_and_index = MagicMock(return_value={"id": "tsk-1"})
    adapter.list_chunk_attempts = MagicMock(return_value=[_summary_row()])
    adapter.get_chunk_attempt = MagicMock(return_value=_detail_row())
    return adapter


@pytest.fixture
def service(mock_adapter: MagicMock) -> ChunkAttemptsService:
    return ChunkAttemptsService(adapter=mock_adapter, database_name="test")


@pytest.mark.asyncio
async def test_list_attempts_happy_path(service: ChunkAttemptsService) -> None:
    resp = await list_chunk_attempts_endpoint(
        source_id="src-1",
        chunk_index=0,
        _="user",
        service=service,
    )
    assert len(resp.data) == 1
    assert resp.data[0].attempt_number == 1
    assert resp.data[0].entity_count == 0


@pytest.mark.asyncio
async def test_list_attempts_404_missing_source(
    service: ChunkAttemptsService, mock_adapter: MagicMock
) -> None:
    mock_adapter.get_source.return_value = None
    with pytest.raises(NotFoundError):
        await list_chunk_attempts_endpoint(
            source_id="x",
            chunk_index=0,
            _="user",
            service=service,
        )


@pytest.mark.asyncio
async def test_list_attempts_404_missing_chunk(
    service: ChunkAttemptsService, mock_adapter: MagicMock
) -> None:
    mock_adapter.get_chunk_task_by_job_and_index.return_value = None
    with pytest.raises(NotFoundError):
        await list_chunk_attempts_endpoint(
            source_id="src-1",
            chunk_index=999,
            _="user",
            service=service,
        )


@pytest.mark.asyncio
async def test_get_attempt_returns_full_body(service: ChunkAttemptsService) -> None:
    resp = await get_chunk_attempt_endpoint(
        source_id="src-1",
        chunk_index=0,
        attempt_id="a1",
        _="user",
        service=service,
    )
    assert resp.input_text == "hello"
    assert resp.raw_entities == []


@pytest.mark.asyncio
async def test_get_attempt_404_missing(
    service: ChunkAttemptsService, mock_adapter: MagicMock
) -> None:
    mock_adapter.get_chunk_attempt.return_value = None
    with pytest.raises(NotFoundError):
        await get_chunk_attempt_endpoint(
            source_id="src-1",
            chunk_index=0,
            attempt_id="nope",
            _="user",
            service=service,
        )
