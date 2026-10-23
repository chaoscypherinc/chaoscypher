# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for chunk_attempts endpoints (list + detail)."""

from __future__ import annotations

from typing import TYPE_CHECKING
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


if TYPE_CHECKING:
    from pathlib import Path


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
    # Model a COMMITTED source: current_extraction_job_id is cleared at
    # extraction-complete time, so it is None post-commit. The attempts-history
    # view must still resolve the chunk task (the whole point of the fix), so
    # the resolver goes through the (source_id, chunk_index) join, not the
    # now-null active-job pointer.
    adapter.get_source = MagicMock(
        return_value={
            "id": "src-1",
            "database_name": "test",
            "current_extraction_job_id": None,
        }
    )
    adapter.get_chunk_task_by_source_and_index = MagicMock(return_value={"id": "tsk-1"})
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
    mock_adapter.get_chunk_task_by_source_and_index.return_value = None
    with pytest.raises(NotFoundError):
        await list_chunk_attempts_endpoint(
            source_id="src-1",
            chunk_index=999,
            _="user",
            service=service,
        )


@pytest.mark.asyncio
async def test_list_attempts_resolves_on_committed_source_without_job_pointer(
    service: ChunkAttemptsService, mock_adapter: MagicMock
) -> None:
    """Attempts resolve on a committed source (current_extraction_job_id=None).

    Regression: the resolver used to look the chunk task up via the source's
    active-job pointer, which is cleared at extraction-complete time — so
    every committed source 404'd, exactly the case (reviewing rerun history
    after commit) this view exists to serve. The resolver must not touch the
    job pointer.
    """
    # Fixture already sets current_extraction_job_id=None; assert the resolver
    # never consulted the (removed) job-pointer lookup and drove the source
    # join instead.
    resp = await list_chunk_attempts_endpoint(
        source_id="src-1",
        chunk_index=0,
        _="user",
        service=service,
    )
    assert len(resp.data) == 1
    mock_adapter.get_chunk_task_by_source_and_index.assert_called_once_with(
        source_id="src-1", chunk_index=0, database_name="test"
    )
    assert not mock_adapter.get_chunk_task_by_job_and_index.called


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


# --- The response models take every column the real adapter returns (audit wave 6) ---


def test_every_attempt_column_is_a_field_of_the_detail_model() -> None:
    """``ChunkAttemptDetail`` forbids extra keys: a column added to the attempt row must be declared.

    The hand-written rows above could not notice: 0011 added
    ``decode_fallback`` to the row and the detail endpoint would have
    answered 500 for every attempt.
    """
    from chaoscypher_core.adapters.sqlite.models import ChunkExtractionAttempt
    from chaoscypher_cortex.features.sources.models import ChunkAttemptDetail

    missing = set(ChunkExtractionAttempt.model_fields) - set(ChunkAttemptDetail.model_fields)
    assert not missing, sorted(missing)


def test_real_adapter_rows_validate_with_their_decode_fallback(tmp_path: Path) -> None:
    """A rerun on a real adapter: the list and detail rows validate and carry the fallback."""
    from sqlmodel import SQLModel

    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
    from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
    from chaoscypher_core.services.sources.engine.extraction.chunk_failures import (
        decode_fallback,
    )
    from chaoscypher_cortex.features.sources.models import (
        ChunkAttemptDetail,
        ChunkAttemptSummary,
    )

    db_path = tmp_path / "app.db"
    SQLModel.metadata.create_all(get_engine(str(db_path)), checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    fallback = decode_fallback(2, seed=42, temperature=0.0)
    assert fallback is not None
    try:
        adapter.upload_source(
            source_id="src-1",
            database_name="default",
            filename="t.txt",
            file_content=b"hello world",
            staging_dir=str(tmp_path),
        )
        adapter.update_source_columns(
            source_id="src-1", database_name="default", updates={"status": "committed"}
        )
        adapter.create_extraction_job(job_id="job-1", source_id="src-1", database_name="default")
        adapter.create_chunk_task(
            task_id="tsk-1", job_id="job-1", database_name="default", chunk_index=0
        )
        adapter.update_chunk_task(
            "tsk-1", {"status": "completed", "decode_fallback": fallback.record()}
        )
        adapter.reset_chunk_task_for_rerun(task_id="tsk-1", source_id="src-1")
        rows = adapter.list_chunk_attempts(chunk_task_id="tsk-1")
        detail = adapter.get_chunk_attempt(rows[0]["id"])
    finally:
        adapter.disconnect()
        evict_engine(db_path)

    assert detail is not None
    assert [ChunkAttemptSummary(**r).decode_fallback for r in rows] == [fallback.record()]
    assert ChunkAttemptDetail(**detail).decode_fallback == fallback.record()
