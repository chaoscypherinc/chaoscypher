# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``reset_for_re_extraction`` zeroes every quality counter.

Workstream 2 (2026-05-07 import pipeline remediation): when a committed
source is re-extracted, the per-stage drop counters from the previous
run must NOT linger on the row.  ``reset_for_re_extraction`` is the
adapter-level primitive backing
``services.sources.management.re_extraction.force_re_extract``; calling
it directly is the cleanest way to verify the row-level reset contract
without dragging the graph repository into a unit test.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.models import SourceStatus


@pytest.fixture
def sqlite_adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """Per-test file-backed adapter with the full schema applied."""
    db_path = tmp_path / "test.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)

    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    try:
        yield adapter
    finally:
        adapter.disconnect()


@pytest.fixture
def committed_source_id(sqlite_adapter: SqliteAdapter, tmp_path: Path) -> str:
    """A source row in the COMMITTED state, ready to be re-extracted."""
    source_id = "src-reset-1"
    sqlite_adapter.upload_source(
        source_id=source_id,
        database_name="default",
        filename="x.txt",
        file_content=b"x",
        staging_dir=str(tmp_path),
    )
    sqlite_adapter.update_file(
        source_id,
        "default",
        {
            "status": SourceStatus.COMMITTED,
            "indexing_complete": True,
            "extraction_complete": True,
            "commit_complete": True,
        },
    )
    return source_id


def test_reset_for_re_extraction_zeroes_counters(
    sqlite_adapter: SqliteAdapter, committed_source_id: str
) -> None:
    """Re-extracting a committed source clears every quality counter."""
    sqlite_adapter.update_source_columns(
        source_id=committed_source_id,
        database_name="default",
        updates={
            "loader_warnings_count": 7,
            "dedup_entities_merged": 22,
            "vector_indexing_status": "indexed",
        },
    )

    sqlite_adapter.reset_for_re_extraction(
        source_id=committed_source_id,
        database_name="default",
    )

    row = sqlite_adapter.get_source(committed_source_id, "default")
    assert row is not None
    assert row["loader_warnings_count"] == 0
    assert row["dedup_entities_merged"] == 0
    assert row["vector_indexing_status"] == "pending"
