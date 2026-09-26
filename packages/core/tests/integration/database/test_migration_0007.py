# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Roundtrip test for migration 0007 — ``document_chunks.start_time`` / ``end_time``.

0007 adds the media-timestamp columns that chunks of transcribed audio and
video carry, so a citation can point at a moment in a recording. Pins that
the upgrade adds both nullable columns and the downgrade removes them,
using the real Alembic runner primitives.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import chaoscypher_core.adapters.sqlite.models  # noqa: F401 — register metadata
from chaoscypher_core.adapters.sqlite.engine import evict_engine
from chaoscypher_core.database.migrations.runner import downgrade_to, upgrade_to


def _columns(db_path: Path, table: str) -> dict[str, tuple[str, int]]:
    """Return ``{column: (type, notnull)}`` for ``table`` via PRAGMA table_info."""
    # ``with sqlite3.connect(...)`` commits but does not close — close explicitly
    # so no connection leaks into later tests' ResourceWarning detectors.
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    finally:
        conn.close()
    return {row[1]: (row[2], row[3]) for row in rows}


def test_0007_adds_nullable_media_timestamp_columns(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0007")
        columns = _columns(db, "document_chunks")
        for name in ("start_time", "end_time"):
            assert name in columns, f"{name} missing from document_chunks after upgrade to 0007"
            column_type, notnull = columns[name]
            assert column_type.upper() in ("FLOAT", "REAL"), (name, column_type)
            assert notnull == 0, f"{name} must be nullable (NULL for every non-media loader)"
    finally:
        evict_engine(db)


def test_0007_downgrade_removes_columns(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0007")
        downgrade_to(db, "0006")
        columns = _columns(db, "document_chunks")
        assert "start_time" not in columns
        assert "end_time" not in columns
    finally:
        evict_engine(db)
