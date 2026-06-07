# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""PR2a Task 15 - create_workflow_safe wraps IntegrityError as ConflictError."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.exceptions import ConflictError


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_path = tmp_path / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="test")
    a.connect()
    yield a
    a.disconnect()


def test_create_workflow_safe_succeeds_for_unique_name(adapter: SqliteAdapter) -> None:
    created = adapter.create_workflow_safe(
        workflow={
            "id": "w1",
            "database_name": "test",
            "name": "alpha",
        }
    )
    assert created["id"] == "w1"
    assert created["name"] == "alpha"


def test_create_workflow_safe_raises_conflict_on_duplicate_name(
    adapter: SqliteAdapter,
) -> None:
    adapter.create_workflow_safe(workflow={"id": "w1", "database_name": "test", "name": "alpha"})
    with pytest.raises(ConflictError) as ctx:
        adapter.create_workflow_safe(
            workflow={"id": "w2", "database_name": "test", "name": "alpha"}
        )
    assert "alpha" in str(ctx.value)
    assert ctx.value.details.get("name") == "alpha"


def test_create_workflow_safe_allows_same_name_different_database(
    adapter: SqliteAdapter,
) -> None:
    """The unique constraint should be (database_name, name), not just name."""
    adapter.create_workflow_safe(workflow={"id": "w1", "database_name": "a", "name": "alpha"})
    # Same name in a different database should succeed
    created = adapter.create_workflow_safe(
        workflow={"id": "w2", "database_name": "b", "name": "alpha"}
    )
    assert created["id"] == "w2"
