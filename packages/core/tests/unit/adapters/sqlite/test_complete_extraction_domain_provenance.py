# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""complete_extraction persists domain provenance (version + content hash)."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.models import SourceStatus


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_path = tmp_path / "test.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    adp = SqliteAdapter(str(db_path), database_name="default")
    adp.connect()
    yield adp
    adp.disconnect()


def _seed_extracting_source(adapter: SqliteAdapter, tmp_path: Path) -> None:
    """Create a source in the pre-extraction EXTRACTING state.

    ``complete_extraction`` raises ``InvalidStateError`` for an
    already-committed source, so the row is left un-committed.
    """
    adapter.upload_source(
        source_id="src1",
        database_name="default",
        filename="x.txt",
        file_content=b"x",
        staging_dir=str(tmp_path),
    )
    adapter.update_file(
        "src1",
        "default",
        {"status": SourceStatus.EXTRACTING},
    )


def test_complete_extraction_persists_domain_version_and_hash(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    _seed_extracting_source(adapter, tmp_path)
    adapter.complete_extraction(
        source_id="src1",
        entities=[],
        relationships=[],
        detected_domain="technical",
        domain_version="1.9.0",
        domain_content_hash="a" * 64,
    )
    src = adapter.get_source("src1", database_name="default")
    assert src is not None
    assert src["domain_version"] == "1.9.0"
    assert src["domain_content_hash"] == "a" * 64


def test_complete_extraction_defaults_provenance_to_none(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    _seed_extracting_source(adapter, tmp_path)
    adapter.complete_extraction(
        source_id="src1",
        entities=[],
        relationships=[],
        detected_domain="technical",
    )
    src = adapter.get_source("src1", database_name="default")
    assert src is not None
    assert src["domain_version"] is None
    assert src["domain_content_hash"] is None
