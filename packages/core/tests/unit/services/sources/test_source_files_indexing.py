# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests for SourceIndexingMixin atomicity."""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter

from chaoscypher_core.adapters.sqlite.models import SourceRow
from chaoscypher_core.models import SourceStatus


def test_complete_extraction_writes_status_and_flag_atomically(
    in_memory_adapter: SqliteAdapter,
) -> None:
    """SQLAlchemy flushes all extraction-complete attribute writes in one UPDATE.

    Regression test: if a future refactor splits the attribute writes
    across multiple session.commit() calls, the intermediate state
    (e.g. status=EXTRACTED but extraction_complete=False) would become
    observable. This test pins the atomic-write contract.
    """
    source_id = "src_atomic"
    in_memory_adapter.create_source(
        {
            "id": source_id,
            "database_name": in_memory_adapter.database_name,
            "filename": "x.md",
            "filepath": "/tmp/x.md",
            "file_type": "markdown",
            "file_size": 10,
            "content_hash": "hash-atomic",
            "status": SourceStatus.EXTRACTING.value,
        }
    )

    in_memory_adapter.complete_extraction(
        source_id=source_id,
        entities=[],
        relationships=[],
        forced_domain=None,
        detected_domain="technical",
    )

    in_memory_adapter.session.expire_all()
    row = in_memory_adapter.session.get(SourceRow, source_id)
    assert row.status == SourceStatus.EXTRACTED
    assert row.extraction_complete is True
    assert row.extraction_completed_at is not None


def test_complete_extraction_clears_job_id(in_memory_adapter: SqliteAdapter) -> None:
    """complete_extraction clears current_extraction_job_id.

    Prevents future retries from seeing a stale terminal job reference.
    """
    source_id = "src_be6_a"
    in_memory_adapter.create_source(
        {
            "id": source_id,
            "database_name": in_memory_adapter.database_name,
            "filename": "a.md",
            "filepath": "/tmp/a.md",
            "file_type": "markdown",
            "file_size": 10,
            "content_hash": "hash-a",
            "status": SourceStatus.EXTRACTING.value,
        }
    )
    # Simulate that an extraction job was in progress
    row = in_memory_adapter.session.get(SourceRow, source_id)
    row.current_extraction_job_id = "job_xyz"
    in_memory_adapter.session.add(row)
    in_memory_adapter.session.commit()

    # Act
    in_memory_adapter.complete_extraction(
        source_id=source_id,
        entities=[],
        relationships=[],
        forced_domain=None,
        detected_domain="technical",
    )

    # Assert
    in_memory_adapter.session.expire_all()
    row = in_memory_adapter.session.get(SourceRow, source_id)
    assert row.current_extraction_job_id is None


def test_fail_extraction_clears_job_id(in_memory_adapter: SqliteAdapter) -> None:
    """fail_extraction also clears current_extraction_job_id."""
    source_id = "src_be6_b"
    in_memory_adapter.create_source(
        {
            "id": source_id,
            "database_name": in_memory_adapter.database_name,
            "filename": "b.md",
            "filepath": "/tmp/b.md",
            "file_type": "markdown",
            "file_size": 10,
            "content_hash": "hash-b",
            "status": SourceStatus.EXTRACTING.value,
        }
    )
    # Simulate that an extraction job was in progress
    row = in_memory_adapter.session.get(SourceRow, source_id)
    row.current_extraction_job_id = "job_abc"
    in_memory_adapter.session.add(row)
    in_memory_adapter.session.commit()

    # Act
    in_memory_adapter.fail_extraction(source_id=source_id, error="test error")

    # Assert
    in_memory_adapter.session.expire_all()
    row = in_memory_adapter.session.get(SourceRow, source_id)
    assert row.current_extraction_job_id is None


def test_complete_commit_clears_job_id(in_memory_adapter: SqliteAdapter) -> None:
    """complete_commit also clears current_extraction_job_id."""
    source_id = "src_be6_c"
    in_memory_adapter.create_source(
        {
            "id": source_id,
            "database_name": in_memory_adapter.database_name,
            "filename": "c.md",
            "filepath": "/tmp/c.md",
            "file_type": "markdown",
            "file_size": 10,
            "content_hash": "hash-c",
            "status": SourceStatus.COMMITTING.value,
        }
    )
    # Simulate that an extraction job was set (edge case but possible if
    # extraction completed but commit was retried).
    row = in_memory_adapter.session.get(SourceRow, source_id)
    row.current_extraction_job_id = "job_def"
    in_memory_adapter.session.add(row)
    in_memory_adapter.session.commit()

    # Act
    in_memory_adapter.complete_commit(
        source_id=source_id,
        nodes_created=5,
        edges_created=10,
        templates_created=2,
        source_document_node_id="node_123",
    )

    # Assert
    in_memory_adapter.session.expire_all()
    row = in_memory_adapter.session.get(SourceRow, source_id)
    assert row.current_extraction_job_id is None


def test_fail_commit_clears_job_id(in_memory_adapter: SqliteAdapter) -> None:
    """fail_commit also clears current_extraction_job_id."""
    source_id = "src_be6_d"
    in_memory_adapter.create_source(
        {
            "id": source_id,
            "database_name": in_memory_adapter.database_name,
            "filename": "d.md",
            "filepath": "/tmp/d.md",
            "file_type": "markdown",
            "file_size": 10,
            "content_hash": "hash-d",
            "status": SourceStatus.COMMITTING.value,
        }
    )
    # Simulate that an extraction job was set
    row = in_memory_adapter.session.get(SourceRow, source_id)
    row.current_extraction_job_id = "job_ghi"
    in_memory_adapter.session.add(row)
    in_memory_adapter.session.commit()

    # Act
    in_memory_adapter.fail_commit(source_id=source_id, error="commit failed")

    # Assert
    in_memory_adapter.session.expire_all()
    row = in_memory_adapter.session.get(SourceRow, source_id)
    assert row.current_extraction_job_id is None
