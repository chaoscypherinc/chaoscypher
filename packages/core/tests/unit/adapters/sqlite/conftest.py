# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared fixtures for SqliteAdapter unit tests.

Provides a ``sqlite_adapter`` fixture that opens a per-test, file-backed
SQLite database (CC040 forbids ``:memory:`` SQLite in tests; use
``tmp_path`` instead) with all ``SourceRow`` columns wired in via
``SQLModel.metadata.create_all()``.

The ``llm_stage_progress`` table is created via Alembic migration 0030 and
is NOT part of ``SQLModel.metadata`` — it is created inline with DDL so
that StageProgressMixin tests can exercise FK constraints.

The pattern mirrors ``tests/unit/services/sources/conftest.py`` and
``tests/integration/sources/conftest.py``.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine


def _create_llm_stage_progress_table(engine: object) -> None:
    """Create the llm_stage_progress table inline (Alembic-managed, not in SQLModel.metadata)."""
    from sqlalchemy.engine import Engine  # type: ignore[import-untyped]

    with Engine.connect(engine) as conn:  # type: ignore[arg-type]
        conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS llm_stage_progress (
                source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                stage_name TEXT NOT NULL,
                total INTEGER NOT NULL DEFAULT 0,
                processed INTEGER NOT NULL DEFAULT 0,
                avg_ms INTEGER,
                started_at DATETIME,
                last_activity DATETIME,
                completed_at DATETIME,
                extras_json TEXT,
                PRIMARY KEY (source_id, stage_name)
            )
        """)
        )
        conn.commit()


@pytest.fixture
def sqlite_adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """Per-test file-backed ``SqliteAdapter`` with all tables created.

    Identical setup to ``in_memory_adapter`` / ``integration_adapter``
    in sibling conftests but exposed under the canonical ``sqlite_adapter``
    name used by the 2026-05-07 pipeline-remediation test suites.

    Also creates ``llm_stage_progress`` (Alembic migration 0030) so
    StageProgressMixin tests can exercise FK constraints.
    """
    db_dir = tmp_path / "chaoscypher-adapter-test"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"

    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    _create_llm_stage_progress_table(engine)

    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    try:
        yield adapter
    finally:
        adapter.disconnect()
