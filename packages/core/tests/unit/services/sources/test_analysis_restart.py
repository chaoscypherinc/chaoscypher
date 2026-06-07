# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for analysis handler restart safety.

The analysis handler (`_import_analysis_handler`) owns the critical
transition from "source indexed" to "per-chunk extraction tasks
enqueued on the LLM queue." Restart safety matters because that path
can legitimately take minutes, so a worker crash needs to resume
without duplicating jobs or losing completed tasks.

These tests exercise the two module-level helpers that the handler
delegates to: `_load_or_create_extraction_job` and
`_upsert_extraction_tasks`. They pin the idempotency contract that
the handler rewrite depends on.
"""

from unittest.mock import MagicMock


def test_load_or_create_reuses_existing_job() -> None:
    """When an active ChunkExtractionJob already exists for this source.

    The helper returns it without calling create_extraction_job.
    """
    from chaoscypher_core.operations.importing.import_service import (
        _load_or_create_extraction_job,
    )

    adapter = MagicMock()
    adapter.get_active_extraction_job = MagicMock(
        return_value={
            "id": "job-existing",
            "source_id": "src-1",
            "status": "running",
            "total_chunks": 10,
            "completed_chunks": 3,
        }
    )
    adapter.create_extraction_job = MagicMock()

    job = _load_or_create_extraction_job(
        source_id="src-1",
        database_name="default",
        adapter=adapter,
    )

    assert job["id"] == "job-existing"
    adapter.get_active_extraction_job.assert_called_once_with(
        source_id="src-1", database_name="default"
    )
    adapter.create_extraction_job.assert_not_called()


def test_load_or_create_creates_when_absent() -> None:
    """When no active job exists, the helper calls create_extraction_job.

    It passes whatever kwargs the caller supplied (templates, domain, etc.).
    """
    from chaoscypher_core.operations.importing.import_service import (
        _load_or_create_extraction_job,
    )

    adapter = MagicMock()
    adapter.get_active_extraction_job = MagicMock(return_value=None)
    adapter.create_extraction_job = MagicMock(
        return_value={
            "id": "job-new",
            "source_id": "src-1",
            "status": "pending",
            "total_chunks": 0,
        }
    )

    job = _load_or_create_extraction_job(
        source_id="src-1",
        database_name="default",
        adapter=adapter,
        job_id="job-new",
        extraction_depth="full",
        generate_embeddings=True,
    )

    assert job["id"] == "job-new"
    adapter.create_extraction_job.assert_called_once()
    kwargs = adapter.create_extraction_job.call_args.kwargs
    assert kwargs["job_id"] == "job-new"
    assert kwargs["source_id"] == "src-1"
    assert kwargs["database_name"] == "default"
    assert kwargs["extraction_depth"] == "full"
    assert kwargs["generate_embeddings"] is True


def test_upsert_extraction_tasks_skips_existing_indices() -> None:
    """Groups whose chunk_index already has a task row are left alone.

    Only missing indices get a fresh task row (regardless of status).
    This preserves work done in a previous attempt: a task that was
    already completed or even just queued stays exactly as it was.
    """
    from chaoscypher_core.operations.importing.import_service import (
        _upsert_extraction_tasks,
    )

    adapter = MagicMock()
    adapter.list_extraction_tasks_for_job = MagicMock(
        return_value=[
            {"id": "t0", "chunk_index": 0, "status": "completed"},
            {"id": "t1", "chunk_index": 1, "status": "pending"},
        ]
    )
    adapter.create_chunk_tasks_batch = MagicMock(
        return_value=[
            {"id": "t2-new", "chunk_index": 2, "status": "pending"},
        ]
    )

    groups = [
        {"id": "g0", "combined_content": "a", "small_chunk_ids": ["c0"]},
        {"id": "g1", "combined_content": "b", "small_chunk_ids": ["c1"]},
        {"id": "g2", "combined_content": "c", "small_chunk_ids": ["c2"]},
    ]

    created = _upsert_extraction_tasks(
        job_id="job-1",
        groups=groups,
        database_name="default",
        adapter=adapter,
    )

    # Only index 2 was missing, so exactly one task row was created via batch
    assert len(created) == 1
    adapter.list_extraction_tasks_for_job.assert_called_once_with(
        job_id="job-1", database_name="default"
    )
    adapter.create_chunk_tasks_batch.assert_called_once()
    batch_arg = adapter.create_chunk_tasks_batch.call_args.args[0]
    assert len(batch_arg) == 1
    assert batch_arg[0]["chunk_index"] == 2
    assert batch_arg[0]["job_id"] == "job-1"
    assert batch_arg[0]["database_name"] == "default"


def test_upsert_extraction_tasks_noop_when_all_present() -> None:
    """If every group already has a task row, no batch create is made."""
    from chaoscypher_core.operations.importing.import_service import (
        _upsert_extraction_tasks,
    )

    adapter = MagicMock()
    adapter.list_extraction_tasks_for_job = MagicMock(
        return_value=[
            {"id": "t0", "chunk_index": 0, "status": "completed"},
            {"id": "t1", "chunk_index": 1, "status": "completed"},
        ]
    )
    adapter.create_chunk_tasks_batch = MagicMock()

    groups = [
        {"id": "g0", "combined_content": "a", "small_chunk_ids": ["c0"]},
        {"id": "g1", "combined_content": "b", "small_chunk_ids": ["c1"]},
    ]

    created = _upsert_extraction_tasks(
        job_id="job-1",
        groups=groups,
        database_name="default",
        adapter=adapter,
    )

    assert created == []
    adapter.create_chunk_tasks_batch.assert_not_called()


def test_upsert_extraction_tasks_orphans_stale_out_of_range_rows() -> None:
    """When re-analysis shrinks the groups list, stale non-terminal task rows
    whose chunk_index >= len(groups) must be orphaned in-band with the upsert.

    Reproduces the root cause of the recovery loop bug for source
    fa992140-…: a prior analysis pass left 27 task rows at chunk_index 239..265
    while the current pass only produced 239 groups. The 21 surviving non-terminal
    rows then thrashed SourceRecovery — every 60s the reconciler re-dispatched
    them and the chunk handler short-circuited on chunks_not_found without ever
    transitioning the row to terminal, racking up recovery_attempts until the
    source flipped to error: recovery_exhausted.

    Pinning the fix here: `_upsert_extraction_tasks` must call the adapter's
    bulk-orphan helper with ``max_chunk_index=len(groups)`` so any out-of-range
    non-terminal row is transitioned to ``orphaned`` BEFORE the next reconcile
    pass can pick it up.
    """
    from chaoscypher_core.operations.importing.import_service import (
        _upsert_extraction_tasks,
    )

    adapter = MagicMock()
    # Six existing rows: three in range (0, 1, 2 — covered by groups), three
    # stale (3, 4, 5 — left behind by a prior analysis pass that had more groups).
    adapter.list_extraction_tasks_for_job = MagicMock(
        return_value=[
            {"id": "t0", "chunk_index": 0, "status": "completed"},
            {"id": "t1", "chunk_index": 1, "status": "pending"},
            {"id": "t2", "chunk_index": 2, "status": "queued"},
            {"id": "t3", "chunk_index": 3, "status": "queued"},
            {"id": "t4", "chunk_index": 4, "status": "pending"},
            {"id": "t5", "chunk_index": 5, "status": "failed"},
        ]
    )
    adapter.create_chunk_tasks_batch = MagicMock(return_value=[])
    adapter.orphan_chunk_tasks_outside_range = MagicMock(return_value=3)

    groups = [
        {"id": "g0", "combined_content": "a", "small_chunk_ids": ["c0"]},
        {"id": "g1", "combined_content": "b", "small_chunk_ids": ["c1"]},
        {"id": "g2", "combined_content": "c", "small_chunk_ids": ["c2"]},
    ]

    _upsert_extraction_tasks(
        job_id="job-shrunk",
        groups=groups,
        database_name="default",
        adapter=adapter,
    )

    adapter.orphan_chunk_tasks_outside_range.assert_called_once_with(
        job_id="job-shrunk",
        database_name="default",
        max_chunk_index=3,
    )


def test_upsert_extraction_tasks_orphan_call_runs_even_when_no_stale_rows() -> None:
    """The orphan call is unconditional — the adapter-side WHERE clause filters
    so the rowcount is zero when no stale rows exist. Service-side guard would
    add a branch with no observable upside and a new failure mode (forgetting to
    update it when statuses change). Pin the unconditional call instead.
    """
    from chaoscypher_core.operations.importing.import_service import (
        _upsert_extraction_tasks,
    )

    adapter = MagicMock()
    adapter.list_extraction_tasks_for_job = MagicMock(return_value=[])
    adapter.create_chunk_tasks_batch = MagicMock(return_value=[])
    adapter.orphan_chunk_tasks_outside_range = MagicMock(return_value=0)

    groups = [
        {"id": "g0", "combined_content": "a", "small_chunk_ids": ["c0"]},
        {"id": "g1", "combined_content": "b", "small_chunk_ids": ["c1"]},
    ]

    _upsert_extraction_tasks(
        job_id="job-clean",
        groups=groups,
        database_name="default",
        adapter=adapter,
    )

    adapter.orphan_chunk_tasks_outside_range.assert_called_once_with(
        job_id="job-clean",
        database_name="default",
        max_chunk_index=2,
    )
