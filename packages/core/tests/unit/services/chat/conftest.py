# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared fixtures for chat-services unit tests.

Provides `in_memory_adapter` — a fresh SqliteAdapter connected to a
per-test `tmp_path` directory with all tables created. Mirrors the
sibling fixture in `packages/core/tests/unit/services/sources/conftest.py`
so chat-recovery tests exercise a real adapter (real SQL semantics)
instead of a hand-rolled mock.
"""

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine


@pytest.fixture
def in_memory_adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """Create a fresh SqliteAdapter against a per-test tmp_path directory.

    Despite the name, this uses a real file-backed SQLite (not :memory:)
    so that schema migrations, pragmas, and FTS behave identically to
    production. Each test gets an isolated tmp_path so there's no
    cross-test bleed.

    Yields a connected adapter. Disconnects on teardown.
    """
    db_dir = tmp_path / "chaoscypher-test"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"

    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)

    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    yield adapter
    adapter.disconnect()
