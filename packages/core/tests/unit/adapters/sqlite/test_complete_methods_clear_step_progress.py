# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Regression: complete_* methods clear step progress fields."""

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


def _seed_with_step_progress(adapter: SqliteAdapter, tmp_path: Path, status: str) -> None:
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
        {
            "status": status,
            "current_step": 5,
            "total_steps": 10,
            "step_description": "Analyzing chunk 5/10",
        },
    )


def test_complete_indexing_clears_step_progress(adapter: SqliteAdapter, tmp_path: Path) -> None:
    _seed_with_step_progress(adapter, tmp_path, SourceStatus.INDEXING)
    adapter.complete_indexing(
        source_id="src1",
        chunks_count=10,
        embedding_model="m",
        embedding_dimensions=128,
    )
    src = adapter.get_source("src1", database_name="default")
    assert src is not None
    assert src["current_step"] == 0
    assert src["total_steps"] == 0
    assert src["step_description"] == ""


def test_complete_extraction_clears_step_progress(adapter: SqliteAdapter, tmp_path: Path) -> None:
    _seed_with_step_progress(adapter, tmp_path, SourceStatus.EXTRACTING)
    adapter.complete_extraction(
        source_id="src1",
        entities=[],
        relationships=[],
    )
    src = adapter.get_source("src1", database_name="default")
    assert src is not None
    assert src["current_step"] == 0
    assert src["total_steps"] == 0
    assert src["step_description"] == ""


def test_complete_commit_clears_step_progress(adapter: SqliteAdapter, tmp_path: Path) -> None:
    _seed_with_step_progress(adapter, tmp_path, SourceStatus.COMMITTING)
    adapter.complete_commit(
        source_id="src1",
        nodes_created=1,
        edges_created=2,
        templates_created=0,
    )
    src = adapter.get_source("src1", database_name="default")
    assert src is not None
    assert src["current_step"] == 0
    assert src["total_steps"] == 0
    assert src["step_description"] == ""
