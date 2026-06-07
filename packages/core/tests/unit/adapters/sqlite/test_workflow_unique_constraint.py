# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for (database_name, name) UNIQUE on workflows."""

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_path = tmp_path / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="default")
    a.connect()
    yield a
    a.disconnect()


def test_duplicate_workflow_name_in_same_db_raises_integrity_error(
    adapter: SqliteAdapter,
) -> None:
    now = datetime.now(UTC)
    base = {
        "database_name": "default",
        "name": "MyFlow",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "created_at": now,
        "updated_at": now,
    }
    adapter.create_workflow({**base, "id": "w1"})
    with pytest.raises(IntegrityError):
        adapter.create_workflow({**base, "id": "w2"})


def test_same_name_different_database_is_allowed(adapter: SqliteAdapter) -> None:
    now = datetime.now(UTC)
    adapter.create_workflow(
        {
            "id": "w1",
            "database_name": "db_a",
            "name": "Shared",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "created_at": now,
            "updated_at": now,
        }
    )
    adapter.create_workflow(
        {
            "id": "w2",
            "database_name": "db_b",
            "name": "Shared",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "created_at": now,
            "updated_at": now,
        }
    )
