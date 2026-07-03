# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Roundtrip test for migration 0003 — ``ccx_iri`` columns.

0003 adds a nullable, indexed ``ccx_iri`` column to ``graph_nodes``,
``graph_edges`` and ``sources`` (the CCX 3.0 stable-identity anchor).
This pins that the upgrade adds the column to all three tables and the
downgrade removes it again, using the real Alembic runner primitives.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import chaoscypher_core.adapters.sqlite.models  # noqa: F401 — register metadata
from chaoscypher_core.adapters.sqlite.engine import evict_engine
from chaoscypher_core.database.migrations.runner import downgrade_to, upgrade_to


_TABLES = ("graph_nodes", "graph_edges", "sources")


def _columns(db_path: Path, table: str) -> set[str]:
    """Return the column names of ``table`` via PRAGMA table_info."""
    with sqlite3.connect(str(db_path)) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_0003_adds_ccx_iri_columns(tmp_path: Path) -> None:
    """Upgrading to 0003 adds ``ccx_iri`` to all three target tables."""
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0003")
        for table in _TABLES:
            assert "ccx_iri" in _columns(db, table), (
                f"ccx_iri missing from {table} after upgrade to 0003"
            )
    finally:
        evict_engine(db)


def test_0003_downgrade_removes_columns(tmp_path: Path) -> None:
    """Downgrading 0003 → 0002 removes ``ccx_iri`` from all three tables."""
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0003")
        downgrade_to(db, "0002")
        for table in _TABLES:
            assert "ccx_iri" not in _columns(db, table), (
                f"ccx_iri still present in {table} after downgrade to 0002"
            )
    finally:
        evict_engine(db)
