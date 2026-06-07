# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage for non-bulk SourceChunksMixin methods.

Exercises chunk CRUD, listing/pagination, embedding/status updates,
unembedded-chunk queries, hierarchical grouping, and extraction fetches
against a real file-backed SQLite database (the bulk reset path is covered
separately in ``test_sources_chunks_bulk.py``).
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import DocumentChunk
from chaoscypher_core.exceptions import NotFoundError


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


def _seed_source(adapter: SqliteAdapter, source_id: str = "src-1") -> None:
    adapter.create_source(
        {
            "id": source_id,
            "database_name": "test",
            "filename": f"{source_id}.pdf",
            "filepath": f"/tmp/{source_id}.pdf",
            "file_type": "pdf",
            "file_size": 100,
            "content_hash": f"hash-{source_id}",
            "status": "committed",
            "total_content_length": 500,
        }
    )


def _add_chunk(
    adapter: SqliteAdapter,
    chunk_id: str,
    source_id: str = "src-1",
    chunk_index: int = 0,
    *,
    content: str = "content",
    status: str = "indexed",
    chunk_metadata: dict | None = None,
    embedded_at: datetime | None = None,
) -> None:
    with adapter.transaction():
        adapter.session.add(
            DocumentChunk(
                id=chunk_id,
                database_name="test",
                source_id=source_id,
                chunk_index=chunk_index,
                content=content,
                status=status,
                chunk_metadata=chunk_metadata,
                embedded_at=embedded_at,
            )
        )


# ---------------------------------------------------------------------------
# create_chunk / get_chunk / get_chunk_by_id
# ---------------------------------------------------------------------------


def test_create_chunk(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    created = adapter.create_chunk(
        {
            "id": "c-1",
            "database_name": "test",
            "source_id": "src-1",
            "chunk_index": 0,
            "content": "hello",
        }
    )
    assert created["id"] == "c-1"
    assert created["content"] == "hello"


def test_get_chunk_found_and_scoped(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    _add_chunk(adapter, "c-1")
    found = adapter.get_chunk("c-1", "test")
    assert found is not None
    assert found["id"] == "c-1"
    # Wrong database_name -> None
    assert adapter.get_chunk("c-1", "other") is None


def test_get_chunk_not_found(adapter: SqliteAdapter) -> None:
    assert adapter.get_chunk("missing", "test") is None


def test_get_chunk_by_id_database_agnostic(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    _add_chunk(adapter, "c-1")
    found = adapter.get_chunk_by_id("c-1")
    assert found is not None
    assert found["id"] == "c-1"


def test_get_chunk_by_id_not_found(adapter: SqliteAdapter) -> None:
    assert adapter.get_chunk_by_id("missing") is None


# ---------------------------------------------------------------------------
# list_chunks
# ---------------------------------------------------------------------------


def test_list_chunks_filters_and_limit(adapter: SqliteAdapter) -> None:
    _seed_source(adapter, "src-1")
    _seed_source(adapter, "src-2")
    _add_chunk(adapter, "c-1", "src-1", 0, status="indexed")
    _add_chunk(adapter, "c-2", "src-1", 1, status="committed")
    _add_chunk(adapter, "c-3", "src-2", 0, status="indexed")

    all_db = adapter.list_chunks("test")
    assert {c["id"] for c in all_db} == {"c-1", "c-2", "c-3"}

    by_source = adapter.list_chunks("test", source_id="src-1")
    assert {c["id"] for c in by_source} == {"c-1", "c-2"}

    by_status = adapter.list_chunks("test", status="committed")
    assert {c["id"] for c in by_status} == {"c-2"}

    limited = adapter.list_chunks("test", limit=1)
    assert len(limited) == 1


def test_list_chunks_include_content(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    _add_chunk(adapter, "c-1", content="the body text")
    with_content = adapter.list_chunks("test", include_content=True)
    assert with_content[0]["content"] == "the body text"


def test_list_chunks_empty(adapter: SqliteAdapter) -> None:
    assert adapter.list_chunks("test") == []


# ---------------------------------------------------------------------------
# get_chunks_by_source (pagination + total)
# ---------------------------------------------------------------------------


def test_get_chunks_by_source_pagination(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    for i in range(5):
        _add_chunk(adapter, f"c-{i}", chunk_index=i)
    page1, total = adapter.get_chunks_by_source("src-1", page=1, page_size=2)
    assert total == 5
    assert [c["chunk_index"] for c in page1] == [0, 1]
    page3, total = adapter.get_chunks_by_source("src-1", page=3, page_size=2)
    assert [c["chunk_index"] for c in page3] == [4]


def test_get_chunks_by_source_status_filter(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    _add_chunk(adapter, "c-0", chunk_index=0, status="indexed")
    _add_chunk(adapter, "c-1", chunk_index=1, status="committed")
    rows, total = adapter.get_chunks_by_source("src-1", status="committed")
    assert total == 1
    assert [c["id"] for c in rows] == ["c-1"]


def test_get_chunks_by_source_include_embeddings(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    _add_chunk(adapter, "c-0", chunk_index=0)
    rows, total = adapter.get_chunks_by_source("src-1", include_embeddings=True)
    assert total == 1
    assert rows[0]["id"] == "c-0"


def test_get_chunks_by_source_empty(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    rows, total = adapter.get_chunks_by_source("src-1")
    assert rows == []
    assert total == 0


# ---------------------------------------------------------------------------
# update_chunk
# ---------------------------------------------------------------------------


def test_update_chunk_skips_protected_fields(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    _add_chunk(adapter, "c-1", content="old")
    updated = adapter.update_chunk(
        "c-1",
        {
            "content": "new",
            "status": "committed",
            # Protected fields are ignored by the mixin.
            "id": "should-not-change",
            "source_id": "should-not-change",
            "database_name": "should-not-change",
        },
    )
    assert updated["id"] == "c-1"
    assert updated["content"] == "new"
    assert updated["status"] == "committed"
    assert updated["source_id"] == "src-1"
    assert updated["database_name"] == "test"


def test_update_chunk_missing_raises(adapter: SqliteAdapter) -> None:
    with pytest.raises(NotFoundError):
        adapter.update_chunk("missing", {"content": "x"})


# ---------------------------------------------------------------------------
# update_chunk_embedding
# ---------------------------------------------------------------------------


def test_update_chunk_embedding(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    _add_chunk(adapter, "c-1")
    adapter.update_chunk_embedding(
        "c-1",
        embedding="base64data",
        embedding_model="model-x",
        embedding_dimensions=384,
        status="indexed",
    )
    found = adapter.get_chunk_by_id("c-1")
    assert found is not None
    assert found["embedding_model"] == "model-x"
    assert found["embedding_dimensions"] == 384
    assert found["status"] == "indexed"
    # The embedding is persisted as a BLOB (bytes) but the dict serializer
    # surfaces it as the decoded UTF-8 string.
    assert found["embedding"] in (b"base64data", "base64data")


def test_update_chunk_embedding_missing_raises(adapter: SqliteAdapter) -> None:
    with pytest.raises(NotFoundError):
        adapter.update_chunk_embedding(
            "missing",
            embedding="x",
            embedding_model="m",
            embedding_dimensions=1,
            status="indexed",
        )


# ---------------------------------------------------------------------------
# Unembedded chunk queries
# ---------------------------------------------------------------------------


def test_list_unembedded_chunks_basic(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    now = datetime.now(UTC)
    _add_chunk(adapter, "c-0", chunk_index=0, embedded_at=None)
    _add_chunk(adapter, "c-1", chunk_index=1, embedded_at=now)
    _add_chunk(adapter, "c-2", chunk_index=2, embedded_at=None)
    rows = adapter.list_unembedded_chunks(source_id="src-1", database_name="test")
    assert [r["chunk_index"] for r in rows] == [0, 2]


def test_list_unembedded_chunks_keyset_and_limit(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    for i in range(4):
        _add_chunk(adapter, f"c-{i}", chunk_index=i, embedded_at=None)
    # Keyset cursor: only chunks after index 0.
    after = adapter.list_unembedded_chunks(
        source_id="src-1", database_name="test", after_chunk_index=0
    )
    assert [r["chunk_index"] for r in after] == [1, 2, 3]
    # Limit caps the wave.
    wave = adapter.list_unembedded_chunks(source_id="src-1", database_name="test", limit=2)
    assert [r["chunk_index"] for r in wave] == [0, 1]


def test_list_unembedded_chunks_empty(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    assert adapter.list_unembedded_chunks(source_id="src-1", database_name="test") == []


def test_count_unembedded_chunks(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    now = datetime.now(UTC)
    _add_chunk(adapter, "c-0", chunk_index=0, embedded_at=None)
    _add_chunk(adapter, "c-1", chunk_index=1, embedded_at=None)
    _add_chunk(adapter, "c-2", chunk_index=2, embedded_at=now)
    assert adapter.count_unembedded_chunks(source_id="src-1", database_name="test") == 2


def test_count_unembedded_chunks_zero(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    assert adapter.count_unembedded_chunks(source_id="src-1", database_name="test") == 0


def test_mark_chunks_embedded(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    _add_chunk(adapter, "c-0", chunk_index=0, embedded_at=None)
    _add_chunk(adapter, "c-1", chunk_index=1, embedded_at=None)
    now = datetime.now(UTC)
    updated = adapter.mark_chunks_embedded(
        chunk_ids=["c-0", "c-1"], embedded_at=now, database_name="test"
    )
    assert updated == 2
    assert adapter.count_unembedded_chunks(source_id="src-1", database_name="test") == 0


def test_mark_chunks_embedded_empty_short_circuits(adapter: SqliteAdapter) -> None:
    assert adapter.mark_chunks_embedded(chunk_ids=[], embedded_at=None, database_name="test") == 0


# ---------------------------------------------------------------------------
# update_chunk_source / update_chunk_status
# ---------------------------------------------------------------------------


def test_update_chunk_source(adapter: SqliteAdapter) -> None:
    _seed_source(adapter, "src-1")
    _seed_source(adapter, "src-2")
    _add_chunk(adapter, "c-1", "src-1")
    adapter.update_chunk_source("c-1", "src-2")
    found = adapter.get_chunk_by_id("c-1")
    assert found is not None
    assert found["source_id"] == "src-2"


def test_update_chunk_source_missing_raises(adapter: SqliteAdapter) -> None:
    with pytest.raises(NotFoundError):
        adapter.update_chunk_source("missing", "src-1")


def test_update_chunk_status_bulk(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    _add_chunk(adapter, "c-0", chunk_index=0, status="indexed")
    _add_chunk(adapter, "c-1", chunk_index=1, status="indexed")
    count = adapter.update_chunk_status("src-1", "committed")
    assert count == 2
    rows, _ = adapter.get_chunks_by_source("src-1")
    assert all(c["status"] == "committed" for c in rows)


def test_update_chunk_status_no_rows(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    assert adapter.update_chunk_status("src-1", "committed") == 0


# ---------------------------------------------------------------------------
# delete_chunks_for_source
# ---------------------------------------------------------------------------


def test_delete_chunks_for_source(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    _add_chunk(adapter, "c-0", chunk_index=0)
    _add_chunk(adapter, "c-1", chunk_index=1)
    adapter.delete_chunks_for_source("src-1")
    rows, total = adapter.get_chunks_by_source("src-1")
    assert total == 0
    assert rows == []


# ---------------------------------------------------------------------------
# get_small_chunks
# ---------------------------------------------------------------------------


def test_get_small_chunks(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    _add_chunk(adapter, "c-1", chunk_index=1)
    _add_chunk(adapter, "c-0", chunk_index=0)
    rows = adapter.get_small_chunks("src-1")
    # Ordered by chunk_index ascending.
    assert [r["chunk_index"] for r in rows] == [0, 1]


def test_get_small_chunks_empty(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    assert adapter.get_small_chunks("src-1") == []


# ---------------------------------------------------------------------------
# store_chunks_and_groups + get_hierarchical_groups
# ---------------------------------------------------------------------------


def test_store_chunks_and_groups_and_read_back(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    small_chunks = [
        {
            "id": "c-0",
            "database_name": "test",
            "source_id": "src-1",
            "chunk_index": 0,
            "content": "alpha",
            "status": "staged",
        },
        {
            "id": "c-1",
            "database_name": "test",
            "source_id": "src-1",
            "chunk_index": 1,
            "content": "beta",
            "status": "staged",
        },
    ]
    groups = [
        {
            "id": "g-0",
            "group_index": 0,
            "small_chunk_ids": ["c-0", "c-1"],
            "combined_content": "alpha\n\nbeta",
            "char_start": 0,
            "char_end": 10,
        }
    ]
    adapter.store_chunks_and_groups(small_chunks, groups)

    rows, total = adapter.get_chunks_by_source("src-1")
    assert total == 2

    read_groups = adapter.get_hierarchical_groups("src-1")
    assert len(read_groups) == 1
    assert read_groups[0]["id"] == "g-0"
    assert read_groups[0]["small_chunk_ids"] == ["c-0", "c-1"]


def test_store_chunks_and_groups_idempotent_replace(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    base_chunk = {
        "id": "c-0",
        "database_name": "test",
        "source_id": "src-1",
        "chunk_index": 0,
        "content": "alpha",
        "status": "staged",
    }
    adapter.store_chunks_and_groups([base_chunk], [])
    # Re-running deletes the existing chunk first, so no PK conflict.
    adapter.store_chunks_and_groups([dict(base_chunk)], [])
    _, total = adapter.get_chunks_by_source("src-1")
    assert total == 1


def test_get_hierarchical_groups_no_metadata(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    _add_chunk(adapter, "c-0", chunk_index=0, chunk_metadata=None)
    assert adapter.get_hierarchical_groups("src-1") == []


def test_get_hierarchical_groups_default_database(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    _add_chunk(
        adapter,
        "c-0",
        chunk_index=0,
        chunk_metadata={
            "hierarchical_group": {
                "id": "g-9",
                "group_index": 0,
                "small_chunk_ids": ["c-0"],
                "combined_content": "x",
                "char_start": 0,
                "char_end": 1,
            }
        },
    )
    # database_name omitted -> falls back to adapter.database_name.
    groups = adapter.get_hierarchical_groups("src-1")
    assert [g["id"] for g in groups] == ["g-9"]


# ---------------------------------------------------------------------------
# create_dynamic_hierarchical_groups
# ---------------------------------------------------------------------------


def test_create_dynamic_hierarchical_groups(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    for i in range(4):
        _add_chunk(adapter, f"c-{i}", chunk_index=i, content=f"chunk{i}")
    groups = adapter.create_dynamic_hierarchical_groups(
        "src-1", "test", group_size=2, group_overlap=0
    )
    assert len(groups) == 2
    assert groups[0]["small_chunk_ids"] == ["c-0", "c-1"]
    assert groups[1]["small_chunk_ids"] == ["c-2", "c-3"]
    assert "chunk0" in groups[0]["combined_content"]


def test_create_dynamic_hierarchical_groups_no_chunks(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    assert adapter.create_dynamic_hierarchical_groups("src-1", "test", group_size=2) == []


# ---------------------------------------------------------------------------
# get_chunks_by_ids / get_chunks_for_extraction
# ---------------------------------------------------------------------------


def test_get_chunks_by_ids(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    _add_chunk(adapter, "c-0", chunk_index=0, content="a")
    _add_chunk(adapter, "c-1", chunk_index=1, content="b")
    rows = adapter.get_chunks_by_ids(["c-1", "c-0"], "test")
    # Ordered by chunk_index ascending regardless of input order.
    assert [r["id"] for r in rows] == ["c-0", "c-1"]
    assert rows[0]["content"] == "a"


def test_get_chunks_by_ids_empty_input(adapter: SqliteAdapter) -> None:
    assert adapter.get_chunks_by_ids([], "test") == []


def test_get_chunks_for_extraction(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    _add_chunk(adapter, "c-1", chunk_index=1, content="second")
    _add_chunk(adapter, "c-0", chunk_index=0, content="first")
    rows = adapter.get_chunks_for_extraction("src-1", "test")
    assert [r["chunk_index"] for r in rows] == [0, 1]
    assert rows[0]["content"] == "first"


def test_get_chunks_for_extraction_empty(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    assert adapter.get_chunks_for_extraction("src-1", "test") == []
