# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pin the projected job read behind the extraction-progress poll.

``GET /api/v1/sources/{id}/extraction`` is polled every three seconds for
the whole life of an extraction and consumes seven scalar columns. The
unprojected ``get_extraction_job`` selects all 29 columns of
``ChunkExtractionJob``, ten of them prompt/template/config TEXT, so the
poll path uses ``get_extraction_job_progress`` instead — the same
load_only-plus-explicit-dict shape ``get_running_chunk_task`` already uses
for the chunk-task half of that same endpoint.

Pinned two ways: the emitted SELECT must not name any heavy column, and
the returned dict must still carry everything the endpoint unpacks.
``get_extraction_job`` itself must stay unprojected — other callers read
``system_prompt`` / ``generate_embeddings`` / ``detected_domain`` /
``forced_domain``, and ``load_only`` would drop those keys outright.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine


# The prompt/template/config TEXT columns on ChunkExtractionJob. None of
# these may appear in the SELECT the progress poll emits.
HEAVY_COLUMNS = (
    "domain_guidance",
    "system_prompt",
    "user_instructions",
    "relationship_instructions",
    "user_instructions_template",
    "extraction_rules_template",
    "entity_templates",
    "relationship_templates",
    "domain_examples",
    "extraction_config",
)

# Exactly what SourceService.get_extraction_status unpacks from the job.
CONSUMED_KEYS = {
    "status",
    "total_chunks",
    "completed_chunks",
    "failed_chunks",
    "extraction_depth",
    "started_at",
    "completed_at",
}


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


def _seed_job(adapter: SqliteAdapter) -> None:
    """Insert one source and one job carrying a fat prompt payload."""
    adapter.create_source(
        {
            "id": "src-1",
            "database_name": "default",
            "filename": "test.txt",
            "filepath": "/tmp/test.txt",
            "file_type": "txt",
            "file_size": 100,
            "content_hash": "abc123",
            "status": "extracting",
        }
    )
    adapter.create_extraction_job(job_id="job-1", source_id="src-1", database_name="default")
    adapter.start_extraction_job("job-1")
    adapter.update_extraction_job(
        "job-1",
        {
            "status": "running",
            "total_chunks": 10,
            "completed_chunks": 4,
            "failed_chunks": 1,
            "extraction_depth": "full",
            "system_prompt": "x" * 4096,
            "domain_guidance": "y" * 4096,
            "extraction_config": '{"exclusions": ["' + "z" * 4096 + '"]}',
        },
    )


def _capture_job_sql(adapter: SqliteAdapter, fn: Any) -> tuple[Any, list[str]]:
    """Run ``fn`` while capturing every SQL statement that reads the job table."""
    bind = adapter.session.get_bind()
    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany) -> None:
        if "chunk_extraction_job" in statement.lower() and statement.lstrip().lower().startswith(
            "select"
        ):
            statements.append(statement)

    event.listen(bind, "before_cursor_execute", _capture)
    try:
        result = fn()
    finally:
        event.remove(bind, "before_cursor_execute", _capture)
    return result, statements


def test_progress_read_omits_the_prompt_columns(adapter: SqliteAdapter) -> None:
    """The poll's SELECT must not name any prompt/template/config TEXT column."""
    _seed_job(adapter)

    progress, statements = _capture_job_sql(
        adapter, lambda: adapter.get_extraction_job_progress("job-1")
    )

    assert progress is not None
    assert statements, "expected at least one SELECT against chunk_extraction_job"
    emitted = " ".join(statements).lower()
    offenders = [column for column in HEAVY_COLUMNS if column in emitted]
    assert not offenders, f"progress poll still selects heavy columns: {offenders}"


def test_progress_read_returns_every_consumed_field(adapter: SqliteAdapter) -> None:
    """The projection must carry exactly the fields the endpoint unpacks."""
    _seed_job(adapter)

    progress = adapter.get_extraction_job_progress("job-1")

    assert progress is not None
    assert set(progress) == CONSUMED_KEYS
    assert progress["status"] == "running"
    assert progress["total_chunks"] == 10
    assert progress["completed_chunks"] == 4
    assert progress["failed_chunks"] == 1
    assert progress["extraction_depth"] == "full"
    assert progress["started_at"] is not None
    assert progress["completed_at"] is None


def test_progress_read_returns_none_for_missing_job(adapter: SqliteAdapter) -> None:
    """A job id with no row behaves like ``get_extraction_job``: ``None``."""
    _seed_job(adapter)

    assert adapter.get_extraction_job_progress("nope") is None


def test_full_job_read_stays_unprojected(adapter: SqliteAdapter) -> None:
    """``get_extraction_job`` must keep the columns its heavy callers read.

    ``_entity_to_dict``/``model_dump`` reads ``__dict__``, so a deferred
    column is a *missing key*, not a lazy load. Narrowing this method would
    silently break the prompt write-back and the finalizer's domain lookup.
    """
    _seed_job(adapter)

    job = adapter.get_extraction_job("job-1")

    assert job is not None
    for key in ("system_prompt", "generate_embeddings", "detected_domain", "forced_domain"):
        assert key in job, f"get_extraction_job dropped {key}; a heavy caller reads it"
    assert job["system_prompt"] == "x" * 4096
