# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage tests for SourceTagsMixin tag/assignment methods.

Exercises tag CRUD, assign/unassign and the tag-to-source query methods on
a connected, file-backed SqliteAdapter (the bulk clear helpers are already
covered by test_sources_tags_bulk.py). Sources are seeded via create_source
so FK-bearing assignment rows are valid.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine


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


def _make_tag(adapter: SqliteAdapter, tag_id: str, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": tag_id,
        "database_name": "test",
        "name": f"name-{tag_id}",
    }
    data.update(overrides)
    return adapter.create_tag(data)


# ---------------------------------------------------------------------------
# Tag CRUD
# ---------------------------------------------------------------------------


def test_create_and_get_tag(adapter: SqliteAdapter) -> None:
    _make_tag(adapter, "t1", color="#fff", description="d")
    fetched = adapter.get_tag("t1", database_name="test")
    assert fetched is not None
    assert fetched["color"] == "#fff"


def test_get_tag_missing_returns_none(adapter: SqliteAdapter) -> None:
    assert adapter.get_tag("nope", database_name="test") is None


def test_get_tag_wrong_database_returns_none(adapter: SqliteAdapter) -> None:
    _make_tag(adapter, "t1")
    # Tag exists but database_name mismatch -> None
    assert adapter.get_tag("t1", database_name="other") is None


def test_list_tags_scoped_by_database(adapter: SqliteAdapter) -> None:
    _make_tag(adapter, "t1")
    _make_tag(adapter, "t2")
    _make_tag(adapter, "t3", database_name="other")
    rows = adapter.list_tags("test")
    assert {r["id"] for r in rows} == {"t1", "t2"}


def test_update_tag_sets_fields(adapter: SqliteAdapter) -> None:
    _make_tag(adapter, "t1", name="orig")
    updated = adapter.update_tag({"id": "t1", "name": "changed", "color": "#000"})
    assert updated is not None
    assert updated["name"] == "changed"
    assert updated["color"] == "#000"


def test_update_tag_skips_none_values(adapter: SqliteAdapter) -> None:
    _make_tag(adapter, "t1", name="orig", color="#abc")
    # None values are ignored; color is preserved.
    updated = adapter.update_tag({"id": "t1", "name": "renamed", "color": None})
    assert updated is not None
    assert updated["name"] == "renamed"
    assert updated["color"] == "#abc"


def test_update_tag_missing_returns_none(adapter: SqliteAdapter) -> None:
    assert adapter.update_tag({"id": "missing", "name": "x"}) is None


def test_delete_tag_without_assignments(adapter: SqliteAdapter) -> None:
    # Covers the delete path including the (empty) assignment-removal loop.
    _make_tag(adapter, "t1")
    assert adapter.delete_tag("t1") is True
    assert adapter.get_tag("t1", database_name="test") is None


def test_delete_tag_clears_its_assignments(adapter: SqliteAdapter) -> None:
    # delete_tag walks SourceTagAssignment rows for the tag and deletes
    # them. Drive that loop by removing the assignment first via the
    # dedicated unassign path, then delete the now-unreferenced tag.
    _seed_source(adapter, "s1")
    _make_tag(adapter, "t1")
    adapter.assign_tag("s1", "t1", "test")
    assert adapter.unassign_tag("s1", "t1") is True
    assert adapter.delete_tag("t1") is True
    assert adapter.get_source_tags("s1") == []


def test_delete_tag_with_live_assignment(adapter: SqliteAdapter) -> None:
    # Deleting a tag that STILL has a live assignment must remove the assignment
    # rows first. delete_tag() flushes the child deletes before the parent delete
    # so FK enforcement does not reject the parent SourceTag DELETE.
    _seed_source(adapter, "s1")
    _make_tag(adapter, "t1")
    adapter.assign_tag("s1", "t1", "test")
    assert adapter.get_source_tags("s1") != []
    assert adapter.delete_tag("t1") is True
    assert adapter.get_tag("t1", database_name="test") is None
    assert adapter.get_source_tags("s1") == []


def test_delete_tag_missing_returns_false(adapter: SqliteAdapter) -> None:
    assert adapter.delete_tag("missing") is False


# ---------------------------------------------------------------------------
# Assign / unassign
# ---------------------------------------------------------------------------


def test_assign_tag_creates_assignment(adapter: SqliteAdapter) -> None:
    _seed_source(adapter, "s1")
    _make_tag(adapter, "t1")
    assignment = adapter.assign_tag("s1", "t1", "test")
    assert assignment["source_id"] == "s1"
    assert assignment["tag_id"] == "t1"


def test_assign_tag_idempotent_returns_existing(adapter: SqliteAdapter) -> None:
    _seed_source(adapter, "s1")
    _make_tag(adapter, "t1")
    first = adapter.assign_tag("s1", "t1", "test")
    second = adapter.assign_tag("s1", "t1", "test")
    assert first["id"] == second["id"]
    # No duplicate assignment row.
    assert len(adapter.get_source_tags("s1")) == 1


def test_unassign_tag_removes_assignment(adapter: SqliteAdapter) -> None:
    _seed_source(adapter, "s1")
    _make_tag(adapter, "t1")
    adapter.assign_tag("s1", "t1", "test")
    assert adapter.unassign_tag("s1", "t1") is True
    assert adapter.get_source_tags("s1") == []


def test_unassign_tag_missing_returns_false(adapter: SqliteAdapter) -> None:
    assert adapter.unassign_tag("s1", "t1") is False


# ---------------------------------------------------------------------------
# Query methods
# ---------------------------------------------------------------------------


def test_get_source_tags_returns_full_tag_details(adapter: SqliteAdapter) -> None:
    _seed_source(adapter, "s1")
    _make_tag(adapter, "t1", color="#123")
    _make_tag(adapter, "t2", color="#456")
    adapter.assign_tag("s1", "t1", "test")
    adapter.assign_tag("s1", "t2", "test")
    tags = adapter.get_source_tags("s1")
    assert {t["id"] for t in tags} == {"t1", "t2"}
    assert {t["color"] for t in tags} == {"#123", "#456"}


def test_get_source_ids_by_tag_ids_deduplicated(adapter: SqliteAdapter) -> None:
    _seed_source(adapter, "s1")
    _seed_source(adapter, "s2")
    _make_tag(adapter, "t1")
    _make_tag(adapter, "t2")
    adapter.assign_tag("s1", "t1", "test")
    adapter.assign_tag("s1", "t2", "test")  # s1 under two tags -> dedup
    adapter.assign_tag("s2", "t1", "test")
    ids = adapter.get_source_ids_by_tag_ids(["t1", "t2"], "test")
    assert set(ids) == {"s1", "s2"}
    assert len(ids) == 2


def test_get_source_ids_by_tag_ids_database_scoped(adapter: SqliteAdapter) -> None:
    _seed_source(adapter, "s1")
    _make_tag(adapter, "t1")
    adapter.assign_tag("s1", "t1", "test")
    # Querying a different database name finds nothing.
    assert adapter.get_source_ids_by_tag_ids(["t1"], "other") == []


def test_delete_tags_for_source(adapter: SqliteAdapter) -> None:
    _seed_source(adapter, "s1")
    _make_tag(adapter, "t1")
    _make_tag(adapter, "t2")
    adapter.assign_tag("s1", "t1", "test")
    adapter.assign_tag("s1", "t2", "test")
    adapter.delete_tags_for_source("s1")
    assert adapter.get_source_tags("s1") == []


def test_get_source_tags_batch(adapter: SqliteAdapter) -> None:
    _seed_source(adapter, "s1")
    _seed_source(adapter, "s2")
    _seed_source(adapter, "s3")
    _make_tag(adapter, "t1")
    _make_tag(adapter, "t2")
    adapter.assign_tag("s1", "t1", "test")
    adapter.assign_tag("s1", "t2", "test")
    adapter.assign_tag("s2", "t1", "test")
    # s3 has no tags.
    result = adapter.get_source_tags_batch(["s1", "s2", "s3"])
    assert {t["id"] for t in result["s1"]} == {"t1", "t2"}
    assert {t["id"] for t in result["s2"]} == {"t1"}
    assert "s3" not in result


def test_get_source_tags_batch_empty_input(adapter: SqliteAdapter) -> None:
    assert adapter.get_source_tags_batch([]) == {}
