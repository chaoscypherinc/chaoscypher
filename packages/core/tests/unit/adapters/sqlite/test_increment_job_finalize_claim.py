# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The terminal transition must be claimed exactly once (single finalize enqueue).

``increment_job_completed_and_check`` used to increment, commit, then re-read
and compute ``is_terminal = completed + failed >= total``. Because the re-read
happened after the write lock was released, two concurrent last-chunk handlers
could both observe the terminal counts and both return ``is_terminal=True`` —
double-enqueuing OP_FINALIZE_EXTRACTION. The fix adds a ``finalize_claimed``
column and claims the transition with a single atomic UPDATE
(``WHERE finalize_claimed = 0 AND completed + failed >= total``), so exactly
one caller's claim affects a row.
"""

from __future__ import annotations

from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import update as sqla_update
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import ChunkExtractionJob
from chaoscypher_core.utils.id import generate_id


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_dir = tmp_path / "chaoscypher-test"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="default")
    a.connect()
    yield a
    a.disconnect()


def _seed_job(adapter: SqliteAdapter, total: int) -> str:
    source_id = generate_id(prefix="src")
    job_id = generate_id(prefix="job")
    adapter.create_source(
        {
            "id": source_id,
            "database_name": "default",
            "filename": "t.txt",
            "filepath": "/tmp/t.txt",
            "file_type": "text",
            "file_size": 1,
            "content_hash": generate_id(),
            "status": "extracting",
        }
    )
    adapter.create_extraction_job(job_id=job_id, source_id=source_id, database_name="default")
    adapter.update_extraction_job_total(job_id=job_id, total_chunks=total, database_name="default")
    return job_id


def test_already_claimed_job_does_not_re_observe_terminal(adapter: SqliteAdapter) -> None:
    """Once finalize_claimed is set, a later increment is NOT terminal.

    The pre-fix logic returned is_terminal=True here (counts >= total),
    double-enqueuing finalize. The claim guard returns False.
    """
    job_id = _seed_job(adapter, total=2)
    # Simulate: the terminal transition was already claimed by another worker.
    adapter.session.execute(
        sqla_update(ChunkExtractionJob)
        .where(ChunkExtractionJob.id == job_id)
        .values(completed_chunks=2, finalize_claimed=True)
    )
    adapter.session.commit()

    progress = adapter.increment_job_completed_and_check(
        job_id=job_id, database_name="default", outcome="completed"
    )

    assert progress["completed"] == 3  # counts still advance
    assert progress["total"] == 2
    assert progress["is_terminal"] is False  # but the transition is NOT re-claimed


def test_concurrent_last_chunks_claim_terminal_exactly_once(adapter: SqliteAdapter) -> None:
    """N workers completing the final N chunks → exactly one is_terminal=True."""
    total = 8
    job_id = _seed_job(adapter, total=total)
    db_path = str(adapter.db_path)

    def complete_one() -> bool:
        a = SqliteAdapter(db_path=db_path, database_name="default")
        a.connect()
        try:
            progress = a.increment_job_completed_and_check(
                job_id=job_id, database_name="default", outcome="completed"
            )
            return bool(progress["is_terminal"])
        finally:
            a.disconnect()

    with ThreadPoolExecutor(max_workers=total) as pool:
        terminal_flags = list(pool.map(lambda _: complete_one(), range(total)))

    assert sum(terminal_flags) == 1, (
        f"expected exactly one terminal claim, got {sum(terminal_flags)}"
    )
