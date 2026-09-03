# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pin the load_only projections on chunk-task lifecycle reads.

- get_completed_chunk_results must still carry the aggregation inputs
  (input_text, chunk_sentences, raw_entities) while skipping llm_response_json.
- get_running_chunk_task returns its four consumed fields.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """Per-test file-backed SqliteAdapter."""
    db_path = tmp_path / "app.db"
    engine = get_engine(db_path)
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="default")
    a.connect()
    yield a
    a.disconnect()


def _seed_task(adapter: SqliteAdapter) -> None:
    """Insert one source, one job, and one chunk task with known payloads."""
    adapter.create_source(
        {
            "id": "src-1",
            "database_name": "default",
            "filename": "test.txt",
            "filepath": "/tmp/test.txt",
            "file_type": "txt",
            "file_size": 100,
            "content_hash": "abc123",
            "status": "processing",
        }
    )
    adapter.create_extraction_job(job_id="job-1", source_id="src-1", database_name="default")
    adapter.create_chunk_task(
        task_id="task-1", job_id="job-1", database_name="default", chunk_index=0
    )
    adapter.update_chunk_task(
        "task-1",
        {
            "input_text": "chunk input text",
            "chunk_sentences": ["chunk input text."],
            "llm_response_json": '{"huge": "payload"}',
        },
    )


def test_completed_results_carry_aggregation_inputs(adapter: SqliteAdapter) -> None:
    """Projection keeps input_text/chunk_sentences/raw_entities, drops llm_response_json."""
    _seed_task(adapter)
    adapter.complete_chunk_task("task-1", raw_entities=[{"name": "A"}], raw_relationships=[])

    results = adapter.get_completed_chunk_results("job-1")
    assert len(results) == 1
    task = results[0]
    assert task["input_text"] == "chunk input text"
    assert task["chunk_sentences"] == ["chunk input text."]
    assert task["raw_entities"] == [{"name": "A"}]
    # Not loaded — load_only leaves it unset.
    assert task.get("llm_response_json") is None


def test_running_task_returns_consumed_fields(adapter: SqliteAdapter) -> None:
    """get_running_chunk_task returns chunk_index/retry_count/max_retries/timing."""
    _seed_task(adapter)
    adapter.start_chunk_task("task-1")

    running = adapter.get_running_chunk_task("job-1")
    assert running is not None
    assert running["chunk_index"] == 0
    assert running["retry_count"] == 0
    assert running["max_retries"] == 3
    assert running["started_at"] is not None
    assert running["elapsed_seconds"] is not None
