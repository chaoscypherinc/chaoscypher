# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Row-status guards on the two chunk-task lifecycle transitions.

``OPERATION_RETRY_ON_CRASH[OP_EXTRACT_CHUNK]`` is ``True``, so the queue
reconciler and ``SourceRecovery`` may both re-deliver a chunk task that a
worker slot is already executing. Before 2026-09-10 both writes were blind:
``start_chunk_task_with_input`` re-claimed unconditionally and
``complete_chunk_task_with_output`` wrote ``completed`` unconditionally and
returned nothing, so two deliveries of one ``chunk_task_id`` produced two
terminal writes and — via the caller — two job-counter increments for one
chunk, satisfying the finalize predicate
(``completed_chunks + failed_chunks >= total_chunks``) one chunk early.

These tests pin the guards against a real ``SqliteAdapter``. They mirror
the contract the sibling vision pipeline already states on
``update_vision_page_guarded``: rows=0 means stale dispatch, and the caller
must treat it as a no-op.

The start guard deliberately admits ``running`` — ``SourceRecovery``
re-dispatches stale "running zombies" *without* rewinding their status, so
refusing that claim would strand the chunks that path exists to rescue.
``test_start_claim_admits_a_running_row`` pins that carve-out.
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
    """File-backed adapter (CC040 forbids ``:memory:``)."""
    db_dir = tmp_path / "chaoscypher-test"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"

    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)

    instance = SqliteAdapter(str(db_path), database_name="default")
    instance.connect()
    try:
        yield instance
    finally:
        instance.disconnect()


def _seed(adapter: SqliteAdapter, *, task_id: str = "ct-cas-1") -> str:
    """Insert a source + extraction job + one chunk task at the ``pending`` default."""
    source_id = f"src-{task_id}"
    job_id = f"job-{task_id}"
    adapter.create_source(
        {
            "id": source_id,
            "database_name": "default",
            "filename": f"{task_id}.txt",
            "filepath": f"/tmp/{task_id}.txt",
            "file_type": "txt",
            "file_size": 1,
            "content_hash": f"hash-{task_id}",
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
    return job_id


def _complete(adapter: SqliteAdapter, task_id: str, *, marker: str) -> int:
    """Run the guarded completion with an identifiable payload."""
    return adapter.complete_chunk_task_with_output(
        task_id=task_id,
        llm_response_json=marker,
        llm_duration_ms=10,
        raw_entities=[{"name": marker, "type": "Person", "description": "x"}],
        raw_relationships=[],
    )


def test_start_claim_succeeds_from_pending(adapter: SqliteAdapter) -> None:
    """The default post-create status is claimable.

    ``import_service`` enqueues the chunk task *before*
    ``mark_chunk_tasks_queued_batch`` runs, and ``chunk_rerun_service``
    resets to ``pending`` and never marks queued at all — so a claim
    predicate narrower than this would break normal extraction.
    """
    _seed(adapter, task_id="ct-pending")

    assert adapter.start_chunk_task_with_input("ct-pending", "text") == 1
    assert adapter.get_chunk_task("ct-pending")["status"] == "running"


def test_start_claim_admits_a_running_row(adapter: SqliteAdapter) -> None:
    """A ``running`` row stays claimable — SourceRecovery never rewinds it.

    ``services/sources/recovery.py`` re-dispatches stale ``running`` chunk
    rows without resetting their status ("running zombies whose worker died
    after claiming"). Refusing this claim would leave those chunks
    unrunnable and stall the source in ``extracting`` forever, which is the
    exact failure that recovery path exists to prevent.
    """
    _seed(adapter, task_id="ct-zombie")
    assert adapter.start_chunk_task_with_input("ct-zombie", "first") == 1

    assert adapter.start_chunk_task_with_input("ct-zombie", "second") == 1
    assert adapter.get_chunk_task("ct-zombie")["status"] == "running"


def test_start_claim_refused_once_completed(adapter: SqliteAdapter) -> None:
    """A row that completed between the stale check and the claim is not re-claimed.

    This closes the read-then-write gap in ``_check_stale_chunk_task``: its
    ``completed`` short-circuit reads the row, and a concurrent delivery can
    finish before the claim lands.
    """
    _seed(adapter, task_id="ct-done")
    assert adapter.start_chunk_task_with_input("ct-done", "text") == 1
    assert _complete(adapter, "ct-done", marker="winner") == 1

    assert adapter.start_chunk_task_with_input("ct-done", "text again") == 0
    row = adapter.get_chunk_task("ct-done")
    assert row["status"] == "completed"
    assert row["llm_response_json"] == "winner"


def test_start_claim_returns_zero_for_missing_row(adapter: SqliteAdapter) -> None:
    """A vanished task reports 0 rather than silently doing nothing."""
    assert adapter.start_chunk_task_with_input("ct-nonexistent", "text") == 0


def test_completion_is_guarded_on_running(adapter: SqliteAdapter) -> None:
    """A row never claimed cannot be completed."""
    _seed(adapter, task_id="ct-unclaimed")

    assert _complete(adapter, "ct-unclaimed", marker="x") == 0
    assert adapter.get_chunk_task("ct-unclaimed")["status"] == "pending"


def test_second_delivery_loses_the_completion_race(adapter: SqliteAdapter) -> None:
    """Two deliveries of one chunk task produce exactly one terminal write.

    This is the P1: with blind writes both deliveries returned normally, so
    the caller bumped ``completed_chunks`` twice for a single chunk. The
    loser now reports 0 and the winner's output is preserved intact.
    """
    _seed(adapter, task_id="ct-race")
    assert adapter.start_chunk_task_with_input("ct-race", "text") == 1

    assert _complete(adapter, "ct-race", marker="winner") == 1
    assert _complete(adapter, "ct-race", marker="loser") == 0

    row = adapter.get_chunk_task("ct-race")
    assert row["status"] == "completed"
    assert row["llm_response_json"] == "winner", (
        "the losing delivery overwrote the winner's durable output"
    )


def test_completion_after_a_rerun_reset_is_refused(adapter: SqliteAdapter) -> None:
    """An in-flight delivery cannot clobber a row a rerun has just reset.

    ``reset_chunk_task_for_rerun`` walks a terminal row back to ``pending``.
    A slow delivery still holding the old attempt's output must not land on
    the fresh row.
    """
    _seed(adapter, task_id="ct-rerun")
    assert adapter.start_chunk_task_with_input("ct-rerun", "text") == 1
    assert _complete(adapter, "ct-rerun", marker="attempt-1") == 1

    adapter.reset_chunk_task_for_rerun(task_id="ct-rerun", source_id="src-ct-rerun")
    assert adapter.get_chunk_task("ct-rerun")["status"] == "pending"

    assert _complete(adapter, "ct-rerun", marker="stale-attempt-1") == 0
    assert adapter.get_chunk_task("ct-rerun")["status"] == "pending"
