# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Migration 0021: the ``source_sites`` table, ``sources.site_id`` and ``sources.site_gone_at``.

A site import (spec 2026-10-22, decision 4) is a row of its own; each of its
pages is a source that names it through ``site_id`` (nullable, indexed, a
foreign key that is set to NULL when the site row goes). A page the site no
longer lists is marked with ``site_gone_at``, never deleted. All additive, so
``safe_auto``.
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


def _indexes(db_path: Path, table: str) -> set[str]:
    with sqlite3.connect(str(db_path)) as conn:
        return {r[1] for r in conn.execute(f"PRAGMA index_list({table})")}


def _foreign_keys(db_path: Path, table: str) -> list[tuple[str, str, str, str]]:
    """``(from column, to table, to column, on delete)`` per foreign key."""
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
    return [(r[3], r[2], r[4], r[6]) for r in rows]


def test_0021_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0021")

        sites = _columns(db, "source_sites")
        assert set(sites) == {
            "id",
            "database_name",
            "url",
            "title",
            "discovery_method",
            "status",
            "page_count",
            "options",
            "error_message",
            "created_at",
            "last_checked_at",
        }
        for required in ("id", "database_name", "url", "title", "status", "page_count"):
            assert sites[required][1] == 1, required
        for optional in ("discovery_method", "options", "error_message", "last_checked_at"):
            assert sites[optional][1] == 0, optional
        assert "ix_source_sites_database_name_created_at" in _indexes(db, "source_sites")

        sources = _columns(db, "sources")
        assert sources["site_id"] == ("VARCHAR", 0)
        assert sources["site_gone_at"][1] == 0
        assert "ix_sources_site_id" in _indexes(db, "sources")
        assert ("site_id", "source_sites", "id", "SET NULL") in _foreign_keys(db, "sources")

        downgrade_to(db, "0020")
        assert "site_id" not in _columns(db, "sources")
        assert "site_gone_at" not in _columns(db, "sources")
        assert "ix_sources_site_id" not in _indexes(db, "sources")
        assert all(fk[1] != "source_sites" for fk in _foreign_keys(db, "sources"))
        with sqlite3.connect(str(db)) as conn:
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        assert "source_sites" not in tables
    finally:
        evict_engine(db)


def test_migration_0021_is_safe_auto() -> None:
    from chaoscypher_core.database.migrations.tiers import read_migration_info

    info = read_migration_info("0021")
    assert info.tier.value == "safe_auto"
