# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Migration 0023: ``source_sites.excluded_pages``, the pages a site leaves out.

The pages the user left out of a site import (unticked in the preview, or
passed to a refresh, or deleted from the site) as a JSON list of page
identities. Nullable: a site imported before it reads as leaving none out,
so ``safe_auto``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import evict_engine
from chaoscypher_core.database.migrations.runner import downgrade_to, upgrade_to


def _columns(db_path: Path, table: str) -> dict[str, tuple[str, int]]:
    """``name -> (declared type, notnull)`` from ``PRAGMA table_info``."""
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1]: (r[2].upper(), r[3]) for r in rows}


def test_0023_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0023")
        assert _columns(db, "source_sites")["excluded_pages"] == ("JSON", 0)

        downgrade_to(db, "0022")
        sites = _columns(db, "source_sites")
        assert "excluded_pages" not in sites
        assert "run_cursor" in sites
    finally:
        evict_engine(db)


def test_0023_upgrades_a_site_imported_before_it_to_leave_nothing_out(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0022")
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                "INSERT INTO source_sites (id, database_name, url, title, status, page_count, "
                "created_at) VALUES ('old', 'default', 'https://docs.example.com', 'Docs', "
                "'imported', 3, '2026-10-22 10:00:00')"
            )
        upgrade_to(db, "0023")

        adapter = SqliteAdapter(str(db), database_name="default")
        adapter.connect()
        try:
            site = adapter.get_site("old", "default")
            assert site is not None
            assert site["excluded_pages"] == []
            assert site["page_count"] == 3
        finally:
            adapter.disconnect()
    finally:
        evict_engine(db)
