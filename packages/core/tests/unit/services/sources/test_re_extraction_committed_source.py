# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: complete_extraction must raise on committed sources, not no-op."""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoscypher_core.adapters.sqlite import SqliteAdapter
from chaoscypher_core.exceptions import InvalidStateError
from chaoscypher_core.models import SourceStatus


@pytest.fixture
def adapter(tmp_path: Path) -> SqliteAdapter:
    """Create a fresh SqliteAdapter against an isolated tmp_path DB."""
    from sqlmodel import SQLModel

    from chaoscypher_core.adapters.sqlite.engine import get_engine

    db_dir = tmp_path / "chaoscypher-test"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"

    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)

    a = SqliteAdapter(str(db_path), database_name="default")
    a.connect()
    return a


def test_complete_extraction_raises_on_committed_source(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    """complete_extraction must raise InvalidStateError on a committed source.

    Regression for audit fix #H5: previously the method silently discarded
    new extraction results when called on a committed source.
    """
    adapter.upload_source(
        source_id="src_done",
        database_name="default",
        filename="x.txt",
        file_content=b"x",
        staging_dir=str(tmp_path),
    )
    # Simulate a committed source.
    adapter.update_file(
        source_id="src_done",
        database_name="default",
        updates={
            "status": SourceStatus.COMMITTED,
            "commit_complete": True,
            "extraction_complete": True,
        },
    )

    with pytest.raises(InvalidStateError, match="committed"):
        adapter.complete_extraction(
            source_id="src_done",
            entities=[{"name": "X"}],
            relationships=[],
        )

    # Source must still be COMMITTED with no entity rows.
    detail = adapter.get_source_detail("src_done", "default")
    assert detail is not None
    assert detail["status"] == SourceStatus.COMMITTED
    entity_rows = adapter.list_source_entities("src_done", "default")
    assert entity_rows == []


def test_assert_extractable_raises_on_committed(adapter: SqliteAdapter, tmp_path: Path) -> None:
    """assert_extractable raises InvalidStateError on a committed source."""
    adapter.upload_source(
        source_id="src_done",
        database_name="default",
        filename="x.txt",
        file_content=b"x",
        staging_dir=str(tmp_path),
    )
    adapter.update_file(
        source_id="src_done",
        database_name="default",
        updates={
            "status": SourceStatus.COMMITTED,
            "commit_complete": True,
        },
    )

    with pytest.raises(InvalidStateError, match="committed"):
        adapter.assert_extractable("src_done", "default")


def test_assert_extractable_no_op_on_extracted(adapter: SqliteAdapter, tmp_path: Path) -> None:
    """A source in EXTRACTED (not yet committed) is fine to extract again."""
    adapter.upload_source(
        source_id="src_e",
        database_name="default",
        filename="x.txt",
        file_content=b"x",
        staging_dir=str(tmp_path),
    )
    adapter.update_file(
        source_id="src_e",
        database_name="default",
        updates={
            "status": SourceStatus.EXTRACTED,
            "extraction_complete": True,
            "commit_complete": False,
        },
    )

    # Should not raise.
    adapter.assert_extractable("src_e", "default")
