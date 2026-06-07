# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Truncated and loop-aborted chunks surface on the chunk task and source counters.

Workstream 8 (2026-05-07) plumbs ``finish_reason`` and ``aborted_by_loop``
from each LLM call through ``extract_single_chunk`` and into the
``chunk_extraction_tasks`` row. The chunk handler also bumps the
source-row counters ``llm_chunks_truncated`` and
``llm_chunks_aborted_by_loop`` so the data-quality UI can flag sources
that lost content to provider truncation or stream-loop aborts.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine


@pytest.fixture
def sqlite_adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """File-backed adapter (CC040 forbids ``:memory:``)."""
    db_dir = tmp_path / "chaoscypher-test"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"

    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)

    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    try:
        yield adapter
    finally:
        adapter.disconnect()


def _seed(adapter: SqliteAdapter, *, source_id: str, job_id: str, task_id: str) -> None:
    adapter.create_source(
        {
            "id": source_id,
            "database_name": "default",
            "filename": f"{source_id}.txt",
            "filepath": f"/tmp/{source_id}.txt",
            "file_type": "txt",
            "file_size": 1,
            "content_hash": f"hash-{source_id}",
            "status": "extracting",
        }
    )
    adapter.create_extraction_job(job_id=job_id, source_id=source_id, database_name="default")
    adapter.create_chunk_task(
        task_id=task_id,
        job_id=job_id,
        database_name="default",
        chunk_index=0,
    )


def test_chunk_task_persists_finish_reason_and_aborted_flag(
    sqlite_adapter: SqliteAdapter,
) -> None:
    """``complete_chunk_task_with_output`` writes both observability fields."""
    source_id = "src_obs_1"
    job_id = "job_obs_1"
    task_id = "task_obs_1"
    _seed(sqlite_adapter, source_id=source_id, job_id=job_id, task_id=task_id)

    sqlite_adapter.complete_chunk_task_with_output(
        task_id=task_id,
        llm_response_json="{}",
        llm_duration_ms=10,
        raw_entities=[],
        raw_relationships=[],
        finish_reason="length",
        aborted_by_loop=False,
    )

    row = sqlite_adapter.get_chunk_task(task_id)
    assert row is not None
    assert row["finish_reason"] == "length"
    assert row["aborted_by_loop"] is False


def test_chunk_task_persists_aborted_by_loop(
    sqlite_adapter: SqliteAdapter,
) -> None:
    source_id = "src_obs_2"
    job_id = "job_obs_2"
    task_id = "task_obs_2"
    _seed(sqlite_adapter, source_id=source_id, job_id=job_id, task_id=task_id)

    sqlite_adapter.complete_chunk_task_with_output(
        task_id=task_id,
        llm_response_json="{}",
        llm_duration_ms=10,
        raw_entities=[],
        raw_relationships=[],
        finish_reason="stop",
        aborted_by_loop=True,
    )
    row = sqlite_adapter.get_chunk_task(task_id)
    assert row is not None
    assert row["aborted_by_loop"] is True


def test_legacy_call_without_observability_kwargs_keeps_columns_null(
    sqlite_adapter: SqliteAdapter,
) -> None:
    """Existing callers that don't pass the new kwargs keep working.

    The migration adds NULLABLE columns, so legacy rows simply have
    ``None`` for both fields. The completion path must not synthesize a
    bogus default — that would lie about provenance.
    """
    source_id = "src_obs_legacy"
    job_id = "job_obs_legacy"
    task_id = "task_obs_legacy"
    _seed(sqlite_adapter, source_id=source_id, job_id=job_id, task_id=task_id)

    sqlite_adapter.complete_chunk_task_with_output(
        task_id=task_id,
        llm_response_json="{}",
        llm_duration_ms=10,
        raw_entities=[],
        raw_relationships=[],
    )
    row = sqlite_adapter.get_chunk_task(task_id)
    assert row is not None
    assert row["finish_reason"] is None
    assert row["aborted_by_loop"] is None


@pytest.mark.asyncio
async def test_extract_chunk_handler_increments_truncated_counter(
    sqlite_adapter: SqliteAdapter,
) -> None:
    """``finish_reason='length'`` bumps ``llm_chunks_truncated`` on the source row."""
    from unittest.mock import AsyncMock, patch

    from chaoscypher_core.operations.extraction.chunk_extraction_service import (
        ChunkExtractionOperationsService,
    )

    source_id = "src_trunc_counter"
    job_id = "job_trunc_counter"
    task_id = "task_trunc_counter"
    _seed(sqlite_adapter, source_id=source_id, job_id=job_id, task_id=task_id)

    # Seed a chunk row + extraction config so the handler can rehydrate.
    sqlite_adapter.create_chunk(
        {
            "id": "small_1",
            "database_name": "default",
            "source_id": source_id,
            "chunk_index": 0,
            "content": "Alice met Bob in Paris." * 20,
        }
    )
    sqlite_adapter.update_extraction_job(
        job_id, {"extraction_config": '{"node_templates_formatted": ""}'}
    )
    # Move job from "pending" to "processing" so the handler doesn't skip it.
    sqlite_adapter.update_extraction_job(job_id, {"status": "in_progress"})

    service = ChunkExtractionOperationsService(source_repository=sqlite_adapter)

    async def _fake_extract_single_chunk(**_kwargs: Any) -> tuple[Any, ...]:
        return (
            [],
            [],
            10,
            20,
            {
                "raw_llm_response": "",
                "input_tokens": 10,
                "output_tokens": 20,
                "entity_count": 0,
                "relationship_count": 0,
                "invalid_relationship_count": 0,
                "evidence_stats": {},
                "sentences": [],
                "filtering_log": None,
                "_prompt_data": {},
                "finish_reason": "length",
                "aborted_by_loop": False,
            },
        )

    with (
        patch(
            "chaoscypher_core.services.sources.engine.extraction.utils.ai_entities.AIEntityExtractor.extract_single_chunk",
            new=AsyncMock(side_effect=_fake_extract_single_chunk),
        ),
        patch(
            "chaoscypher_core.queue.queue_client.track_tokens",
            new=AsyncMock(return_value=None),
        ),
        patch.object(service, "queue_finalize_extraction", new=AsyncMock(return_value="qid")),
    ):
        result = await service._extract_chunk_handler(
            data={
                "chunk_task_id": task_id,
                "job_id": job_id,
                "database_name": "default",
                "chunk_index": 0,
                "small_chunk_ids": ["small_1"],
            }
        )

    assert result["success"] is True
    src_row = sqlite_adapter.get_source(source_id, "default")
    assert src_row["llm_chunks_truncated"] == 1
    assert src_row["llm_chunks_aborted_by_loop"] == 0


@pytest.mark.asyncio
async def test_extract_chunk_handler_increments_aborted_counter(
    sqlite_adapter: SqliteAdapter,
) -> None:
    """``aborted_by_loop=True`` bumps ``llm_chunks_aborted_by_loop`` on the source row."""
    from unittest.mock import AsyncMock, patch

    from chaoscypher_core.operations.extraction.chunk_extraction_service import (
        ChunkExtractionOperationsService,
    )

    source_id = "src_aborted_counter"
    job_id = "job_aborted_counter"
    task_id = "task_aborted_counter"
    _seed(sqlite_adapter, source_id=source_id, job_id=job_id, task_id=task_id)

    sqlite_adapter.create_chunk(
        {
            "id": "small_2",
            "database_name": "default",
            "source_id": source_id,
            "chunk_index": 0,
            "content": "Alice met Bob in Paris." * 20,
        }
    )
    sqlite_adapter.update_extraction_job(
        job_id, {"extraction_config": '{"node_templates_formatted": ""}'}
    )
    sqlite_adapter.update_extraction_job(job_id, {"status": "in_progress"})

    service = ChunkExtractionOperationsService(source_repository=sqlite_adapter)

    async def _fake_extract_single_chunk(**_kwargs: Any) -> tuple[Any, ...]:
        return (
            [],
            [],
            10,
            20,
            {
                "raw_llm_response": "",
                "input_tokens": 10,
                "output_tokens": 20,
                "entity_count": 0,
                "relationship_count": 0,
                "invalid_relationship_count": 0,
                "evidence_stats": {},
                "sentences": [],
                "filtering_log": None,
                "_prompt_data": {},
                "finish_reason": "stop",
                "aborted_by_loop": True,
            },
        )

    with (
        patch(
            "chaoscypher_core.services.sources.engine.extraction.utils.ai_entities.AIEntityExtractor.extract_single_chunk",
            new=AsyncMock(side_effect=_fake_extract_single_chunk),
        ),
        patch(
            "chaoscypher_core.queue.queue_client.track_tokens",
            new=AsyncMock(return_value=None),
        ),
        patch.object(service, "queue_finalize_extraction", new=AsyncMock(return_value="qid")),
    ):
        result = await service._extract_chunk_handler(
            data={
                "chunk_task_id": task_id,
                "job_id": job_id,
                "database_name": "default",
                "chunk_index": 0,
                "small_chunk_ids": ["small_2"],
            }
        )

    assert result["success"] is True
    src_row = sqlite_adapter.get_source(source_id, "default")
    assert src_row["llm_chunks_aborted_by_loop"] == 1
    assert src_row["llm_chunks_truncated"] == 0
