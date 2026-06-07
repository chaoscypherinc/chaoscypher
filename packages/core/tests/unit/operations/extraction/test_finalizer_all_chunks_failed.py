# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""When every chunk fails, the source must land in status=error, not committed_empty.

Phase 1 fix (2026-05-21 incident): When completed_tasks is empty but failed_tasks
exist on the job, finalize must route to fail_extraction (status=error) instead
of commit_empty (status=committed). Previously the two cases were indistinguishable.

Incident: Ollama returned 404 for qwen3:30b-instruct (not pulled). 16+ chunk-level
failures. All 3 sources finalized with status='committed', entities=0, no error.

NOTE: The plan specified using `adapter.get_failed_chunk_tasks(job_id)` for the
disambiguation, but that method filters to RETRYABLE tasks only (retry_count <
max_retries). For exhausted-retry chunks (the incident scenario), it returns empty.
The implementation instead derives failed tasks from `all_tasks` (already loaded at
line 293) by filtering for status=='failed', which correctly catches all failed
chunks including those at max retries.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.repos import GraphRepository
from chaoscypher_core.operations.extraction.chunk_extraction_service import (
    ChunkExtractionOperationsService,
)
from chaoscypher_core.operations.extraction.extraction_finalizer import (
    finalize_extraction_handler,
)


# ---------------------------------------------------------------------------
# Shared fixture helpers (mirrors test_pipeline_mocked.py style)
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """File-backed real adapter per test."""
    db_dir = tmp_path / "chaoscypher-all-failed-test"
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


def _seed_source_with_tasks(
    adapter: SqliteAdapter,
    *,
    source_id: str,
    job_id: str,
    chunk_specs: list[tuple[str, str, int]],
) -> list[str]:
    """Seed source + job + chunks + chunk_tasks. Returns task_ids.

    Each chunk_specs entry is (task_id, chunk_id, chunk_index).
    """
    adapter.create_source(
        {
            "id": source_id,
            "database_name": "default",
            "filename": f"{source_id}.txt",
            "filepath": f"/tmp/{source_id}.txt",
            "file_type": "txt",
            "file_size": 100,
            "content_hash": f"hash-{source_id}",
            "status": "extracting",
        }
    )
    adapter.create_extraction_job(job_id=job_id, source_id=source_id, database_name="default")
    adapter.update_extraction_job(job_id, {"extraction_config": '{"node_templates_formatted": ""}'})
    adapter.update_extraction_job(job_id, {"status": "in_progress"})

    task_ids = []
    for task_id, chunk_id, chunk_index in chunk_specs:
        adapter.create_chunk(
            {
                "id": chunk_id,
                "database_name": "default",
                "source_id": source_id,
                "chunk_index": chunk_index,
                "content": f"Test content chunk {chunk_index}.",
            }
        )
        adapter.create_chunk_task(
            task_id=task_id,
            job_id=job_id,
            database_name="default",
            chunk_index=chunk_index,
        )
        task_ids.append(task_id)
    return task_ids


@contextmanager
def _finalize_patches(
    service: ChunkExtractionOperationsService,
) -> Generator[None]:
    """Patch queue and embedding IO so finalize runs in-process."""
    with (
        patch(
            "chaoscypher_core.queue.queue_client.track_tokens",
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            service,
            "queue_finalize_extraction",
            new=AsyncMock(return_value="qid-fin"),
        ),
        patch(
            "chaoscypher_core.repo_factories.get_embedding_service",
            return_value=MagicMock(),
        ),
        patch(
            "chaoscypher_core.operations.queue_utils.queue_client.enqueue_task",
            new=AsyncMock(return_value="qid-commit"),
        ),
        patch(
            "chaoscypher_core.operations.extraction.extraction_finalizer.trigger_next_waiting_extraction",
            new=AsyncMock(return_value=None),
        ),
    ):
        yield


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_chunks_failed_routes_to_error_not_committed(
    sqlite_adapter: SqliteAdapter,
) -> None:
    """When every chunk task fails (max retries exhausted), source → status=error.

    Simulates the 2026-05-21 incident: 3 chunks seeded, all fail with
    "model 'qwen3:30b-instruct' not found". Pre-fix both this case and
    "genuinely empty document" routed to committed_empty. Post-fix the
    failed-tasks branch fires fail_extraction → status=error.
    """
    source_id = "src_all_failed"
    job_id = "job_all_failed"
    chunk_specs = [
        ("task_fail_0", "chunk_fail_0", 0),
        ("task_fail_1", "chunk_fail_1", 1),
        ("task_fail_2", "chunk_fail_2", 2),
    ]
    task_ids = _seed_source_with_tasks(
        sqlite_adapter,
        source_id=source_id,
        job_id=job_id,
        chunk_specs=chunk_specs,
    )

    # Fail all tasks directly — simulate Ollama 404 exhausting retries
    error_msg = "LLMError: model 'qwen3:30b-instruct' not found"
    for task_id in task_ids:
        # Set retry_count to max_retries so task is not retryable
        sqlite_adapter.update_chunk_task(task_id, {"max_retries": 3})
        for _ in range(3):
            sqlite_adapter.fail_chunk_task(
                task_id=task_id,
                error_message=error_msg,
                error_type="model_error",
            )

    service = ChunkExtractionOperationsService(source_repository=sqlite_adapter)
    graph_repo = GraphRepository(sqlite_adapter.session, "default")
    llm_service = MagicMock()

    with _finalize_patches(service=service):
        result = await finalize_extraction_handler(
            graph_repository=graph_repo,
            llm_service=llm_service,
            source_repository=sqlite_adapter,
            chunk_extraction_service=service,
            data={
                "job_id": job_id,
                "source_id": source_id,
                "database_name": "default",
                "generate_embeddings": False,
                "file_info": {},
            },
        )

    assert result.get("status") == "extraction_failed", f"Expected extraction_failed, got: {result}"

    src_row = sqlite_adapter.get_source(source_id, "default")
    assert src_row is not None
    assert src_row["status"] == "error", f"Expected status=error, got: {src_row['status']!r}"
    assert src_row.get("error_stage") == "extraction", (
        f"Expected error_stage=extraction, got: {src_row.get('error_stage')!r}"
    )
    assert src_row.get("error_message"), "error_message must be non-empty"
    err_msg = src_row["error_message"]
    assert (
        "qwen3" in err_msg.lower() or "failed" in err_msg.lower() or "model" in err_msg.lower()
    ), f"error_message should mention the failure: {err_msg!r}"
    # commit_complete and extraction_complete must NOT be set on failure
    assert not src_row.get("extraction_complete"), (
        "extraction_complete must be False/0 on failure path"
    )
    assert not src_row.get("commit_complete"), "commit_complete must be False/0 on failure path"


@pytest.mark.asyncio
async def test_zero_chunks_zero_failures_commits_empty(
    sqlite_adapter: SqliteAdapter,
) -> None:
    """Regression guard: a source with NO chunk tasks at all still commits empty.

    A math-heavy or pure-prose document may produce no chunks, so the chunker
    doesn't create any ChunkExtractionTask rows. Post-fix, this must still reach
    committed_empty — it's not an error, just a legitimately empty document.

    Pre-fix, the empty-commit path fired for both cases (failed AND no-tasks).
    Post-fix, we only distinguish when failed tasks > 0. Zero tasks → empty commit.
    """
    source_id = "src_zero_chunks"
    job_id = "job_zero_chunks"

    # Seed source and job but NO chunk tasks
    sqlite_adapter.create_source(
        {
            "id": source_id,
            "database_name": "default",
            "filename": f"{source_id}.txt",
            "filepath": f"/tmp/{source_id}.txt",
            "file_type": "txt",
            "file_size": 100,
            "content_hash": f"hash-{source_id}",
            "status": "extracting",
        }
    )
    sqlite_adapter.create_extraction_job(
        job_id=job_id, source_id=source_id, database_name="default"
    )
    sqlite_adapter.update_extraction_job(
        job_id, {"extraction_config": '{"node_templates_formatted": ""}'}
    )
    sqlite_adapter.update_extraction_job(job_id, {"status": "in_progress"})

    service = ChunkExtractionOperationsService(source_repository=sqlite_adapter)
    graph_repo = GraphRepository(sqlite_adapter.session, "default")
    llm_service = MagicMock()

    with _finalize_patches(service=service):
        result = await finalize_extraction_handler(
            graph_repository=graph_repo,
            llm_service=llm_service,
            source_repository=sqlite_adapter,
            chunk_extraction_service=service,
            data={
                "job_id": job_id,
                "source_id": source_id,
                "database_name": "default",
                "generate_embeddings": False,
                "file_info": {},
            },
        )

    assert result.get("status") == "committed_empty", (
        f"Zero-chunk source must commit empty, got: {result}"
    )
