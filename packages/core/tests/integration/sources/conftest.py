# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared fixtures for source-resumability integration tests.

Re-exports the ``integration_adapter`` fixture defined in the unit
test conftest so integration tests can use the same per-test
file-backed SqliteAdapter without duplicating setup code.
"""

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine


@pytest.fixture
def integration_adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """Per-test file-backed SqliteAdapter for end-to-end flows.

    Creates all tables via SQLModel.metadata.create_all() so schema
    migrations, pragmas, and FTS behave identically to production.
    Isolated per test via tmp_path. Disconnects on teardown.
    """
    db_dir = tmp_path / "chaoscypher-integration"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"

    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)

    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    yield adapter
    adapter.disconnect()
