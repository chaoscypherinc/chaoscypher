# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""PR2a Task 6 — bulk extraction-queue methods on ExtractionQueueStorageProtocol."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import (
    ChunkExtractionJob,
    ChunkExtractionTask,
)


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_dir = tmp_path / "cc-test"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="test")
    a.connect()
    yield a
    a.disconnect()


def _seed_source(adapter: SqliteAdapter, source_id: str, database_name: str) -> None:
    adapter.create_source(
        {
            "id": source_id,
            "database_name": database_name,
            "filename": f"{source_id}.pdf",
            "filepath": f"/tmp/{source_id}.pdf",
            "file_type": "pdf",
            "file_size": 100,
            "content_hash": f"hash-{source_id}-{database_name}",
            "status": "indexed",
        }
    )


def _seed(
    adapter: SqliteAdapter,
    jobs: dict[str, int],
    tasks: dict[str, int],
) -> None:
    """Seed jobs/tasks per database. Creates one source per job."""
    counter = 0
    for db, n in jobs.items():
        for _ in range(n):
            src_id = f"src-{counter}"
            _seed_source(adapter, src_id, db)
            with adapter.transaction():
                adapter.session.add(
                    ChunkExtractionJob(
                        id=f"job-{counter}",
                        database_name=db,
                        source_id=src_id,
                        status="pending",
                    )
                )
            counter += 1
    for db, n in tasks.items():
        # Need a job to hang tasks off; create one per-db if missing
        job_id = f"job-tasks-{db}"
        src_id = f"src-tasks-{db}"
        _seed_source(adapter, src_id, db)
        with adapter.transaction():
            adapter.session.add(
                ChunkExtractionJob(
                    id=job_id,
                    database_name=db,
                    source_id=src_id,
                    status="pending",
                )
            )
        with adapter.transaction():
            for i in range(n):
                adapter.session.add(
                    ChunkExtractionTask(
                        id=f"task-{db}-{i}",
                        database_name=db,
                        job_id=job_id,
                        chunk_index=i,
                        status="pending",
                    )
                )


def test_count_extraction_jobs_scoped(adapter: SqliteAdapter) -> None:
    _seed(adapter, jobs={"a": 2, "b": 1}, tasks={})
    assert adapter.count_extraction_jobs(database_name="a") == 2
    assert adapter.count_extraction_jobs(database_name="b") == 1


def test_delete_extraction_jobs_scoped(adapter: SqliteAdapter) -> None:
    _seed(adapter, jobs={"a": 2, "b": 1}, tasks={})
    assert adapter.delete_extraction_jobs(database_name="a") == 2
    assert adapter.count_extraction_jobs(database_name="a") == 0
    assert adapter.count_extraction_jobs(database_name="b") == 1


def test_count_extraction_tasks_scoped(adapter: SqliteAdapter) -> None:
    _seed(adapter, jobs={}, tasks={"a": 3, "b": 1})
    assert adapter.count_extraction_tasks(database_name="a") == 3


def test_delete_extraction_tasks_scoped(adapter: SqliteAdapter) -> None:
    _seed(adapter, jobs={}, tasks={"a": 3, "b": 1})
    assert adapter.delete_extraction_tasks(database_name="a") == 3
    assert adapter.count_extraction_tasks(database_name="b") == 1


def test_clear_all_extraction_jobs(adapter: SqliteAdapter) -> None:
    _seed(adapter, jobs={"a": 2, "b": 1}, tasks={})
    assert adapter.clear_all_extraction_jobs() == 3


def test_clear_all_extraction_tasks(adapter: SqliteAdapter) -> None:
    _seed(adapter, jobs={}, tasks={"a": 3, "b": 1})
    assert adapter.clear_all_extraction_tasks() == 4


def test_count_extraction_jobs_empty(adapter: SqliteAdapter) -> None:
    assert adapter.count_extraction_jobs(database_name="nonexistent") == 0


def test_clear_all_extraction_tasks_empty_noop(adapter: SqliteAdapter) -> None:
    assert adapter.clear_all_extraction_tasks() == 0
