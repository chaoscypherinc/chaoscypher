# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: update_file must be scoped to (source_id, database_name)."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.exceptions import NotFoundError


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_path = tmp_path / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="default")
    a.connect()
    yield a
    a.disconnect()


def test_update_file_scoped_by_database(adapter: SqliteAdapter, tmp_path: Path) -> None:
    # Set up: two sources in different databases with distinct IDs.
    adapter.upload_source(
        source_id="src_a",
        database_name="db_a",
        filename="a.txt",
        file_content=b"a",
        staging_dir=str(tmp_path),
    )
    adapter.upload_source(
        source_id="src_b",
        database_name="db_b",
        filename="b.txt",
        file_content=b"b",
        staging_dir=str(tmp_path),
    )

    # Update src_a in db_a. Attempting to update src_a in db_b must fail.
    adapter.update_file(
        source_id="src_a",
        database_name="db_a",
        updates={"title": "Updated A"},
    )

    a = adapter.get_file("src_a", "db_a")
    assert a["title"] == "Updated A"

    # Attempting to update src_a with the wrong database raises NotFoundError.
    from chaoscypher_core.exceptions import NotFoundError

    with pytest.raises(NotFoundError):
        adapter.update_file(
            source_id="src_a",
            database_name="db_b",  # wrong database for src_a
            updates={"title": "Should Not Apply"},
        )

    # db_b row (src_b) was never touched
    b = adapter.get_file("src_b", "db_b")
    assert b["title"] == "b.txt"  # unchanged


def test_update_file_raises_on_missing(adapter: SqliteAdapter) -> None:
    with pytest.raises(NotFoundError):
        adapter.update_file(
            source_id="nonexistent",
            database_name="db_a",
            updates={"title": "X"},
        )


def test_update_file_rejects_string_for_datetime_column(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    adapter.upload_source(
        source_id="src_x",
        database_name="db_a",
        filename="x.txt",
        file_content=b"x",
        staging_dir=str(tmp_path),
    )

    with pytest.raises(ValueError, match="datetime"):
        adapter.update_file(
            source_id="src_x",
            database_name="db_a",
            updates={"indexing_completed_at": "2026-05-05T00:00:00Z"},
        )
