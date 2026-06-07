# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""PR2a Task 3 — bulk tag clear methods on SourceTagStorageProtocol."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import (
    SourceTag,
    SourceTagAssignment,
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


def _seed_source(adapter: SqliteAdapter, source_id: str) -> None:
    adapter.create_source(
        {
            "id": source_id,
            "database_name": "test",
            "filename": f"{source_id}.pdf",
            "filepath": f"/tmp/{source_id}.pdf",
            "file_type": "pdf",
            "file_size": 100,
            "content_hash": f"hash-{source_id}",
            "status": "indexed",
        }
    )


def test_clear_all_tag_assignments_returns_count(adapter: SqliteAdapter) -> None:
    _seed_source(adapter, "s1")
    with adapter.transaction():
        adapter.session.add(SourceTag(id="t1", database_name="test", name="foo"))
        adapter.session.add(SourceTag(id="t2", database_name="test", name="bar"))
    with adapter.transaction():
        adapter.session.add(
            SourceTagAssignment(id="a1", database_name="test", source_id="s1", tag_id="t1")
        )
        adapter.session.add(
            SourceTagAssignment(id="a2", database_name="test", source_id="s1", tag_id="t2")
        )
    assert adapter.clear_all_tag_assignments() == 2
    assert adapter.clear_all_tag_assignments() == 0


def test_clear_all_tags_returns_count(adapter: SqliteAdapter) -> None:
    with adapter.transaction():
        for i in range(3):
            adapter.session.add(SourceTag(id=f"t{i}", database_name="test", name=f"n{i}"))
    assert adapter.clear_all_tags() == 3


def test_clear_all_tag_assignments_empty_noop(adapter: SqliteAdapter) -> None:
    assert adapter.clear_all_tag_assignments() == 0


def test_clear_all_tags_empty_noop(adapter: SqliteAdapter) -> None:
    assert adapter.clear_all_tags() == 0
