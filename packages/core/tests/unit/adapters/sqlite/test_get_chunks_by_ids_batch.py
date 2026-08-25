# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``get_chunks_by_ids_batch`` — the search-hydration batch chunk fetch.

Pins the contract ``SearchService._hydrate_chunks`` relies on: identical
per-chunk dict shape to ``get_chunk_by_id``, input-order results,
missing ids silently absent, database-agnostic lookup.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import DocumentChunk


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_path = tmp_path / "test.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="default")
    a.connect()
    try:
        yield a
    finally:
        a.disconnect()


def _seed_source(adapter: SqliteAdapter) -> None:
    adapter.create_source(
        {
            "id": "src-1",
            "database_name": "default",
            "filename": "src-1.txt",
            "filepath": "/tmp/src-1.txt",
            "file_type": "text",
            "file_size": 10,
            "content_hash": "hash-src-1",
            "status": "indexed",
        }
    )


def _seed_chunk(adapter: SqliteAdapter, chunk_id: str, index: int) -> None:
    assert adapter.session is not None
    adapter.session.add(
        DocumentChunk(
            id=chunk_id,
            database_name="default",
            source_id="src-1",
            chunk_index=index,
            content=f"content-{chunk_id}",
        )
    )
    adapter.session.commit()


def test_returns_get_chunk_by_id_shape_in_input_order(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    for i, cid in enumerate(["c1", "c2", "c3"]):
        _seed_chunk(adapter, cid, i)

    batch = adapter.get_chunks_by_ids_batch(["c3", "c1", "c2"])

    assert [c["id"] for c in batch] == ["c3", "c1", "c2"], "results must follow input order"
    single = adapter.get_chunk_by_id("c1")
    assert single is not None
    match = next(c for c in batch if c["id"] == "c1")
    assert match == single, "batch shape must be identical to get_chunk_by_id"


def test_missing_ids_are_silently_absent(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    _seed_chunk(adapter, "c1", 0)
    batch = adapter.get_chunks_by_ids_batch(["c1", "missing"])
    assert [c["id"] for c in batch] == ["c1"]


def test_empty_input_short_circuits(adapter: SqliteAdapter) -> None:
    assert adapter.get_chunks_by_ids_batch([]) == []
