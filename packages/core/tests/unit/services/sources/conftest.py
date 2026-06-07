# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared fixtures for source-services unit tests.

Provides `in_memory_adapter` — a fresh SqliteAdapter connected to a
per-test `tmp_path` directory with all tables created. Also exposed
as `integration_adapter` alias for readability in integration-style
tests that seed and query real data.
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

    Tables are created via SQLModel.metadata.create_all() on the
    engine — this is the same path `initialize_database()` takes as
    its fallback when migrations aren't available.

    Yields a connected adapter. Disconnects on teardown.
    """
    db_dir = tmp_path / "chaoscypher-test"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"

    # Create tables before the adapter tries to use them
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)

    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    yield adapter
    adapter.disconnect()


@pytest.fixture
def adapter_with_default_templates(in_memory_adapter: SqliteAdapter) -> SqliteAdapter:
    """Adapter with default system templates seeded.

    Commit-path tests rely on the `system_template_item` fallback when
    an entity's type has no user-created template; with the FK on
    ``graph_{nodes,edges}.template_id`` declared, that fallback row must
    actually exist. This fixture seeds the full default set once.
    """
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository
    from chaoscypher_core.templates.default_templates import get_all_default_templates

    assert in_memory_adapter.session is not None
    graph_repo = GraphRepository(in_memory_adapter.session, in_memory_adapter.database_name)
    graph_repo.ensure_default_templates_exist(default_templates_provider=get_all_default_templates)
    in_memory_adapter.session.commit()
    return in_memory_adapter


@pytest.fixture
def integration_adapter(
    in_memory_adapter: SqliteAdapter,
) -> SqliteAdapter:
    """Alias for integration-style tests. Same instance as in_memory_adapter.

    Having a named alias keeps the integration tests' intent readable.
    """
    return in_memory_adapter
