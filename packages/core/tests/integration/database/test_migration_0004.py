# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Roundtrip test for migration 0004 — ``sources.full_text``.

0004 adds a nullable ``full_text`` TEXT column to ``sources`` so the
extracted plain text of a source can be stored alongside its row (CCX 3.0
full-text store). This pins that the upgrade adds the column and the
downgrade removes it, using the real Alembic runner primitives.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import chaoscypher_core.adapters.sqlite.models  # noqa: F401 — register metadata
from chaoscypher_core.adapters.sqlite.engine import evict_engine
from chaoscypher_core.database.migrations.runner import downgrade_to, upgrade_to


def _columns(db_path: Path, table: str) -> set[str]:
    """Return the column names of ``table`` via PRAGMA table_info."""
    with sqlite3.connect(str(db_path)) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_0004_adds_full_text(tmp_path: Path) -> None:
    """Upgrading to 0004 adds ``full_text`` to ``sources``."""
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0004")
        assert "full_text" in _columns(db, "sources"), (
            "full_text missing from sources after upgrade to 0004"
        )
    finally:
        evict_engine(db)


def test_0004_downgrade_removes_full_text(tmp_path: Path) -> None:
    """Downgrading 0004 → 0003 removes ``full_text`` from ``sources``."""
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0004")
        downgrade_to(db, "0003")
        assert "full_text" not in _columns(db, "sources"), (
            "full_text still present in sources after downgrade to 0003"
        )
    finally:
        evict_engine(db)
