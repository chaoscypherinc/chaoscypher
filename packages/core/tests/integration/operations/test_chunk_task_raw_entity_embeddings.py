# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""End-to-end persistence of ``raw_entity_embeddings`` on chunk tasks.

Two write paths land embeddings on a chunk task row:

1. ``complete_chunk_task_with_output`` — eager co-write at chunk-extract
   completion (steady state).
2. ``set_chunk_task_embeddings`` — finalize-time backfill for legacy rows
   that pre-date the schema change.

This test exercises both against a real SqliteAdapter so the SQLModel
column wiring, JSON serialization, and read-back round-trip are all
covered without mocking the storage layer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


def _make_chunk_task_row(
    adapter: SqliteAdapter,
    *,
    task_id: str = "ct-1",
    job_id: str = "job-1",
    source_id: str = "src-1",
) -> None:
    """Insert a minimal source + job + chunk task so lifecycle methods have something to update."""
    adapter.create_source(
        {
            "id": source_id,
            "database_name": "default",
            "filename": "x.txt",
            "filepath": "/tmp/x.txt",
            "file_type": "text",
            "file_size": 10,
            "content_hash": "deadbeef",
            "status": "extracting",
        }
    )
    adapter.create_extraction_job(
        job_id=job_id,
        source_id=source_id,
        database_name="default",
    )
    adapter.create_chunk_task(
        task_id=task_id,
        job_id=job_id,
        database_name="default",
        chunk_index=0,
    )


def _read_back(adapter: SqliteAdapter, task_id: str) -> dict:
    rows = adapter.get_completed_chunk_results("job-1")
    return next(r for r in rows if r["id"] == task_id)


def test_complete_chunk_task_with_output_persists_embeddings(
    integration_adapter: SqliteAdapter,
) -> None:
    """Eager co-write path: embeddings land in the same UPDATE as raw_entities."""
    _make_chunk_task_row(integration_adapter)

    raw_entities = [{"name": "Alice", "type": "Person", "description": "lead"}]
    raw_embeddings = [[0.1, 0.2, 0.3, 0.4]]

    integration_adapter.complete_chunk_task_with_output(
        task_id="ct-1",
        llm_response_json='{"ok":true}',
        llm_duration_ms=100,
        input_tokens=10,
        output_tokens=20,
        raw_entities=raw_entities,
        raw_entity_embeddings=raw_embeddings,
        raw_relationships=[],
    )

    row = _read_back(integration_adapter, "ct-1")
    assert row["raw_entities"] == raw_entities
    assert row["raw_entity_embeddings"] == raw_embeddings


def test_complete_chunk_task_with_output_accepts_none_embeddings(
    integration_adapter: SqliteAdapter,
) -> None:
    """Embedding service unavailable at extract time → NULL persisted, finalize backfills."""
    _make_chunk_task_row(integration_adapter)

    integration_adapter.complete_chunk_task_with_output(
        task_id="ct-1",
        llm_response_json='{"ok":true}',
        llm_duration_ms=100,
        raw_entities=[{"name": "Alice", "type": "Person", "description": "lead"}],
        raw_entity_embeddings=None,
        raw_relationships=[],
    )

    row = _read_back(integration_adapter, "ct-1")
    assert row["raw_entity_embeddings"] is None


def test_set_chunk_task_embeddings_backfills_existing_row(
    integration_adapter: SqliteAdapter,
) -> None:
    """Backfill path: writes embeddings to a row that already has raw_entities."""
    _make_chunk_task_row(integration_adapter)
    integration_adapter.complete_chunk_task_with_output(
        task_id="ct-1",
        llm_response_json='{"ok":true}',
        llm_duration_ms=100,
        raw_entities=[{"name": "Alice", "type": "Person", "description": "lead"}],
        raw_entity_embeddings=None,
        raw_relationships=[],
    )

    integration_adapter.set_chunk_task_embeddings("ct-1", [[0.5, 0.6, 0.7, 0.8]])

    row = _read_back(integration_adapter, "ct-1")
    assert row["raw_entity_embeddings"] == [[0.5, 0.6, 0.7, 0.8]]


def test_set_chunk_task_embeddings_no_op_on_missing_row(
    integration_adapter: SqliteAdapter,
) -> None:
    """Unknown task_id is a logged no-op; never raises."""
    integration_adapter.set_chunk_task_embeddings("ct-does-not-exist", [[0.0]])
