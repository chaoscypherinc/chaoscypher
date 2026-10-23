# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pin the load_only projections on chunk-task lifecycle reads.

- get_completed_chunk_results must still carry the aggregation inputs
  (input_text, chunk_sentences, raw_entities, small_chunk_ids) while skipping
  llm_response_json.
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
        task_id="task-1",
        job_id="job-1",
        database_name="default",
        chunk_index=0,
        small_chunk_ids=["c0", "c1"],
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
    # Aggregation places cited sentences in the group's small chunks.
    assert task["small_chunk_ids"] == ["c0", "c1"]
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


def test_the_accessor_loads_only_the_three_columns(adapter: SqliteAdapter) -> None:
    """get_completed_chunk_entities: completed rows below the index, three columns, in order.

    The queue path's known-entities list is rebuilt from these rows at the
    start of every chunk task, so the read selects ``chunk_index``,
    ``raw_entities`` and ``raw_relationships`` alone (never the multi-MB
    ``llm_response_json`` or ``input_text``).
    """
    from sqlalchemy import event

    _seed_task(adapter)
    for index in (1, 2, 3, 4):
        adapter.create_chunk_task(
            task_id=f"task-{index + 1}",
            job_id="job-1",
            database_name="default",
            chunk_index=index,
        )
    # Completed out of order: the read orders by chunk_index, not completion.
    adapter.complete_chunk_task(
        "task-3", raw_entities=[{"name": "C2"}], raw_relationships=[{"source": 0}]
    )
    adapter.complete_chunk_task("task-1", raw_entities=[{"name": "C0"}], raw_relationships=[])
    adapter.start_chunk_task("task-2")  # chunk 1 still running: left out
    adapter.complete_chunk_task("task-4", raw_entities=[{"name": "C3"}], raw_relationships=[])
    adapter.complete_chunk_task("task-5", raw_entities=[{"name": "C4"}], raw_relationships=[])

    statements: list[str] = []

    def _record(_conn: object, _cursor: object, statement: str, *_args: object) -> None:
        statements.append(statement)

    bind = adapter.session.get_bind()
    event.listen(bind, "before_cursor_execute", _record)
    try:
        rows = adapter.get_completed_chunk_entities("job-1", before_chunk_index=4)
    finally:
        event.remove(bind, "before_cursor_execute", _record)

    assert rows == [
        {"chunk_index": 0, "raw_entities": [{"name": "C0"}], "raw_relationships": []},
        {"chunk_index": 2, "raw_entities": [{"name": "C2"}], "raw_relationships": [{"source": 0}]},
        {"chunk_index": 3, "raw_entities": [{"name": "C3"}], "raw_relationships": []},
    ]
    (select_sql,) = [s for s in statements if "chunk_extraction_tasks" in s]
    selected = select_sql.split(" FROM ")[0]
    assert "llm_response_json" not in selected
    assert "input_text" not in selected
    assert "raw_entity_embeddings" not in selected
    assert adapter.get_completed_chunk_entities("job-1", before_chunk_index=0) == []


def test_the_accessor_reads_rows_another_worker_completed(tmp_path: Path) -> None:
    """A row this session already holds is read fresh after another session rewrote it.

    Chunk tasks run on concurrent workers: the asking adapter's session may
    hold a lower task's row from an earlier read, and a rerun on another
    worker completes it again with new output. The identity map must not
    hand the stale copy back.
    """
    db_path = tmp_path / "app.db"
    engine = get_engine(db_path)
    SQLModel.metadata.create_all(engine, checkfirst=True)
    asker = SqliteAdapter(str(db_path), database_name="default")
    other = SqliteAdapter(str(db_path), database_name="default")
    asker.connect()
    other.connect()
    try:
        _seed_task(asker)
        asker.complete_chunk_task("task-1", raw_entities=[{"name": "A"}], raw_relationships=[])
        assert asker.get_completed_chunk_entities("job-1", before_chunk_index=1)[0][
            "raw_entities"
        ] == [{"name": "A"}]  # now held by the asker's session
        # A rerun on another worker: the row is reset and completed again.
        other.update_chunk_task("task-1", {"status": "running"})
        other.complete_chunk_task("task-1", raw_entities=[{"name": "B"}], raw_relationships=[])
        rows = asker.get_completed_chunk_entities("job-1", before_chunk_index=1)
        assert rows == [
            {"chunk_index": 0, "raw_entities": [{"name": "B"}], "raw_relationships": []}
        ]
    finally:
        other.disconnect()
        asker.disconnect()
