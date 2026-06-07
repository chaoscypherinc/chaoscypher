# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for list_chunk_attempts + get_chunk_attempt + get_chunk_task_by_job_and_index.

These read methods back the chunk-rerun history endpoint surface; they
must use load_only by default (CC003) and return ordered, summary-only
dicts unless include_body=True.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .test_chunk_tasks_lifecycle_reset_rerun import _seed_committed_source


if TYPE_CHECKING:
    from pathlib import Path

    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


def test_list_attempts_empty_for_chunk_with_no_reruns(
    sqlite_adapter: SqliteAdapter, tmp_path: Path
) -> None:
    seed = _seed_committed_source(sqlite_adapter, tmp_path)
    attempts = sqlite_adapter.list_chunk_attempts(chunk_task_id=seed["task_id"])
    assert attempts == []


def test_list_attempts_returns_attempts_in_order(
    sqlite_adapter: SqliteAdapter, tmp_path: Path
) -> None:
    seed = _seed_committed_source(sqlite_adapter, tmp_path)

    for _ in range(3):
        sqlite_adapter.reset_chunk_task_for_rerun(
            task_id=seed["task_id"], source_id=seed["source_id"]
        )
        # Mark task completed again so the next reset is allowed
        sqlite_adapter.update_chunk_task(seed["task_id"], {"status": "completed"})

    attempts = sqlite_adapter.list_chunk_attempts(chunk_task_id=seed["task_id"])
    assert [a["attempt_number"] for a in attempts] == [1, 2, 3]


def test_list_attempts_returns_summary_fields_only_by_default(
    sqlite_adapter: SqliteAdapter, tmp_path: Path
) -> None:
    seed = _seed_committed_source(sqlite_adapter, tmp_path)
    sqlite_adapter.reset_chunk_task_for_rerun(task_id=seed["task_id"], source_id=seed["source_id"])

    attempts = sqlite_adapter.list_chunk_attempts(chunk_task_id=seed["task_id"])
    a = attempts[0]
    expected = {
        "id",
        "chunk_task_id",
        "attempt_number",
        "snapshotted_at",
        "started_at",
        "completed_at",
        "entity_count",
        "relationship_count",
        "invalid_relationship_count",
        "finish_reason",
        "aborted_by_loop",
        "llm_duration_ms",
        "input_tokens",
        "output_tokens",
        "input_text_length",
        "llm_response_length",
        "error_message",
        "error_type",
    }
    assert expected <= set(a.keys())


def test_list_attempts_include_body_returns_full_snapshot(
    sqlite_adapter: SqliteAdapter, tmp_path: Path
) -> None:
    seed = _seed_committed_source(sqlite_adapter, tmp_path)
    sqlite_adapter.reset_chunk_task_for_rerun(task_id=seed["task_id"], source_id=seed["source_id"])

    attempts = sqlite_adapter.list_chunk_attempts(chunk_task_id=seed["task_id"], include_body=True)
    a = attempts[0]
    body_keys = {
        "raw_entities",
        "raw_relationships",
        "filtering_log",
        "input_text",
        "llm_response_json",
    }
    assert body_keys <= set(a.keys())
    # Body should have the previous attempt's actual values
    assert a["input_text"] == "hello"
    assert a["llm_response_json"] == '{"entities":[]}'


def test_get_attempt_by_id_returns_full_snapshot(
    sqlite_adapter: SqliteAdapter, tmp_path: Path
) -> None:
    seed = _seed_committed_source(sqlite_adapter, tmp_path)
    sqlite_adapter.reset_chunk_task_for_rerun(task_id=seed["task_id"], source_id=seed["source_id"])
    attempts = sqlite_adapter.list_chunk_attempts(chunk_task_id=seed["task_id"])
    attempt_id = attempts[0]["id"]

    full = sqlite_adapter.get_chunk_attempt(attempt_id)
    assert full is not None
    assert full["id"] == attempt_id
    assert "raw_entities" in full
    assert "filtering_log" in full
    assert "input_text" in full
    assert full["input_text"] == "hello"


def test_get_attempt_by_id_returns_none_when_missing(
    sqlite_adapter: SqliteAdapter,
) -> None:
    assert sqlite_adapter.get_chunk_attempt("nope") is None


def test_get_chunk_task_by_job_and_index_happy_path(
    sqlite_adapter: SqliteAdapter, tmp_path: Path
) -> None:
    seed = _seed_committed_source(sqlite_adapter, tmp_path)

    task = sqlite_adapter.get_chunk_task_by_job_and_index(
        job_id=seed["job_id"], chunk_index=0, database_name="default"
    )
    assert task is not None
    assert task["id"] == seed["task_id"]


def test_get_chunk_task_by_job_and_index_returns_none_missing(
    sqlite_adapter: SqliteAdapter, tmp_path: Path
) -> None:
    seed = _seed_committed_source(sqlite_adapter, tmp_path)
    assert (
        sqlite_adapter.get_chunk_task_by_job_and_index(
            job_id=seed["job_id"], chunk_index=999, database_name="default"
        )
        is None
    )


def test_get_chunk_task_by_source_and_index_happy_path(
    sqlite_adapter: SqliteAdapter, tmp_path: Path
) -> None:
    seed = _seed_committed_source(sqlite_adapter, tmp_path)

    task = sqlite_adapter.get_chunk_task_by_source_and_index(
        source_id=seed["source_id"], chunk_index=0, database_name="default"
    )
    assert task is not None
    assert task["id"] == seed["task_id"]
    assert task["job_id"] == seed["job_id"]


def test_get_chunk_task_by_source_and_index_returns_none_missing(
    sqlite_adapter: SqliteAdapter, tmp_path: Path
) -> None:
    seed = _seed_committed_source(sqlite_adapter, tmp_path)
    assert (
        sqlite_adapter.get_chunk_task_by_source_and_index(
            source_id=seed["source_id"], chunk_index=999, database_name="default"
        )
        is None
    )


def test_get_chunk_task_by_source_and_index_works_when_current_job_id_cleared(
    sqlite_adapter: SqliteAdapter, tmp_path: Path
) -> None:
    """Regression: rerun must work after extraction-complete clears the pointer.

    ``mark_extraction_complete`` (and the commit path) writes
    ``current_extraction_job_id = None`` on the source. The rerun lookup must
    not depend on that pointer — the join via ``chunk_extraction_jobs.source_id``
    finds the task regardless.
    """
    seed = _seed_committed_source(sqlite_adapter, tmp_path)
    sqlite_adapter.update_source_columns(
        source_id=seed["source_id"],
        database_name="default",
        updates={"current_extraction_job_id": None},
    )

    task = sqlite_adapter.get_chunk_task_by_source_and_index(
        source_id=seed["source_id"], chunk_index=0, database_name="default"
    )
    assert task is not None
    assert task["id"] == seed["task_id"]
