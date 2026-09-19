# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``get_file``'s narrow projection must carry the columns its readers render.

``get_file`` is the source-row read the pipeline and the CLI's ``source get``
run on. Its hand-maintained ``load_only`` list omitted ``auto_analyze`` and
``total_content_length``, so the CLI's "Extract Entities" row and indexing
footer could never render from a real row (the command read phantom keys
and its fixtures baked them in). Pinned here so a projection edit that drops
one of them fails loudly instead of as an absent dict key downstream.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_path = tmp_path / "test.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="test")
    a.connect()
    try:
        yield a
    finally:
        a.disconnect()


def test_get_file_carries_auto_analyze_and_total_content_length(adapter: SqliteAdapter) -> None:
    adapter.create_source(
        {
            "id": "src-proj",
            "database_name": "test",
            "filename": "doc.md",
            "filepath": "/tmp/doc.md",
            "file_type": "markdown",
            "file_size": 10,
            "content_hash": "h",
            "status": "indexed",
            "auto_analyze": False,
            "chunk_count": 3,
            "total_content_length": 1234,
        }
    )

    row = adapter.get_file("src-proj", "test")

    assert row is not None
    assert row["auto_analyze"] is False
    assert row["chunk_count"] == 3
    assert row["total_content_length"] == 1234
