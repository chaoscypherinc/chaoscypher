# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Regression: failed-counter increments are atomic under concurrency.

Audit fix #H/storage (increment_job_progress race) — the legacy
read-modify-write path dropped increments under concurrent failure.
The replacement primitive uses a single arithmetic UPDATE so SQLite
serialises every bump at the database level.
"""

from __future__ import annotations

from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
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
    adapter.create_extraction_job(
        job_id=job_id,
        source_id=source_id,
        database_name="default",
    )
    adapter.update_extraction_job_total(job_id=job_id, total_chunks=total, database_name="default")
    return job_id


def test_failed_outcome_increments_failed_chunks(adapter: SqliteAdapter) -> None:
    job_id = _seed_job(adapter, total=3)

    progress = adapter.increment_job_completed_and_check(
        job_id=job_id, database_name="default", outcome="failed"
    )

    assert progress["failed"] == 1
    assert progress["completed"] == 0
    assert progress["total"] == 3
    assert progress["is_terminal"] is False


def test_failed_terminal_observed_once(adapter: SqliteAdapter) -> None:
    job_id = _seed_job(adapter, total=2)

    p1 = adapter.increment_job_completed_and_check(
        job_id=job_id, database_name="default", outcome="failed"
    )
    p2 = adapter.increment_job_completed_and_check(
        job_id=job_id, database_name="default", outcome="failed"
    )

    assert p1["is_terminal"] is False
    assert p2["is_terminal"] is True
    assert p2["failed"] == 2


def test_concurrent_failed_increments_dont_drop(adapter: SqliteAdapter) -> None:
    # Seed a job with a wider total than worker count so we never hit
    # is_terminal mid-test (terminality semantics tested separately).
    job_id = _seed_job(adapter, total=64)
    db_path = str(adapter.db_path)

    def bump() -> None:
        a = SqliteAdapter(db_path=db_path, database_name="default")
        a.connect()
        try:
            a.increment_job_completed_and_check(
                job_id=job_id, database_name="default", outcome="failed"
            )
        finally:
            a.disconnect()

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: bump(), range(32)))

    final = adapter.increment_job_completed_and_check(
        job_id=job_id, database_name="default", outcome="failed"
    )
    # 32 worker bumps + 1 from the assertion call = 33
    assert final["failed"] == 33


def test_invalid_outcome_raises(adapter: SqliteAdapter) -> None:
    job_id = _seed_job(adapter, total=1)

    with pytest.raises(ValueError, match="outcome must be"):
        adapter.increment_job_completed_and_check(
            job_id=job_id, database_name="default", outcome="cancelled"
        )
