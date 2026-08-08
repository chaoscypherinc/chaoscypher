# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Roundtrip test for migration 0006 — ``sources.loader_epub_chapters_skipped``.

0006 adds the EPUB chapter-skip quality counter column so the indexing
handler's loader rollup has a source-row column to increment. Pins that
the upgrade adds the column and the downgrade removes it, using the real
Alembic runner primitives.
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


def test_0006_adds_loader_epub_chapters_skipped(tmp_path: Path) -> None:
    """Upgrading to 0006 adds ``loader_epub_chapters_skipped`` to ``sources``."""
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0006")
        assert "loader_epub_chapters_skipped" in _columns(db, "sources"), (
            "loader_epub_chapters_skipped missing from sources after upgrade to 0006"
        )
    finally:
        evict_engine(db)


def test_0006_column_is_not_null_without_server_default(tmp_path: Path) -> None:
    """The column lands NOT NULL and default-free, matching the SQLModel.

    The migration uses the two-step pattern (add with ``server_default='0'``
    to backfill existing rows, then drop the default) so the final schema
    diff against ``SQLModel.metadata`` stays clean — pinned here via the
    PRAGMA ``notnull`` / ``dflt_value`` flags.
    """
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0006")
        with sqlite3.connect(str(db)) as conn:
            row = next(
                r
                for r in conn.execute("PRAGMA table_info(sources)").fetchall()
                if r[1] == "loader_epub_chapters_skipped"
            )
        _, _, _, notnull, dflt_value, _ = row
        assert notnull == 1, "loader_epub_chapters_skipped must be NOT NULL"
        assert dflt_value is None, (
            "server_default must be dropped after the backfill step so the "
            "schema matches SQLModel.metadata (test_no_undeclared_changes)"
        )
    finally:
        evict_engine(db)


def test_0006_downgrade_removes_column(tmp_path: Path) -> None:
    """Downgrading 0006 → 0005 removes the column from ``sources``."""
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0006")
        downgrade_to(db, "0005")
        assert "loader_epub_chapters_skipped" not in _columns(db, "sources"), (
            "loader_epub_chapters_skipped still present after downgrade to 0005"
        )
    finally:
        evict_engine(db)
