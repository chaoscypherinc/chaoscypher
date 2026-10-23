# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Migration 0022: ``source_sites.run_started_at`` and ``source_sites.run_cursor``.

When a site's run was queued or started (a killed run goes stale after the
operations timeout) and how far its open pass got (a cut-off run resumes
there). Both nullable, so ``safe_auto``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from chaoscypher_core.adapters.sqlite.engine import evict_engine
from chaoscypher_core.database.migrations.runner import downgrade_to, upgrade_to


def _columns(db_path: Path, table: str) -> dict[str, tuple[str, int]]:
    """``name -> (declared type, notnull)`` from ``PRAGMA table_info``."""
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1]: (r[2].upper(), r[3]) for r in rows}


def test_0022_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0022")

        sites = _columns(db, "source_sites")
        assert sites["run_started_at"] == ("DATETIME", 0)
        assert sites["run_cursor"] == ("INTEGER", 0)

        downgrade_to(db, "0021")
        sites = _columns(db, "source_sites")
        assert "run_started_at" not in sites
        assert "run_cursor" not in sites
        assert "page_count" in sites
    finally:
        evict_engine(db)
