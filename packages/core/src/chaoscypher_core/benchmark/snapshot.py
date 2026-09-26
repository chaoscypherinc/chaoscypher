# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Single-file SQLite snapshots of an extracted benchmark graph."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from pathlib import Path


def snapshot_sqlite(src: Path, target: Path) -> None:
    """Write a consistent single-file copy of a (possibly WAL-mode) SQLite database.

    Uses SQLite's online backup, not a file copy: a WAL-mode database keeps
    its latest rows in ``app.db-wal`` until a checkpoint, so a bare copy can
    miss them.

    Args:
        src: The database to copy.
        target: Where to write the copy.
    """
    # ``with sqlite3.connect(...)`` only commits; closing() releases the handles.
    with closing(sqlite3.connect(src)) as source, closing(sqlite3.connect(target)) as dest:
        source.backup(dest)


def snapshot_database_name(db_path: Path) -> str | None:
    """The ``database_name`` a snapshot's rows are scoped by, read from its sources table.

    Args:
        db_path: The snapshot to read.

    Returns:
        The first source's ``database_name``, or None when there is none.
    """
    try:
        with closing(sqlite3.connect(db_path)) as conn:
            row = conn.execute("SELECT database_name FROM sources LIMIT 1").fetchone()
    except sqlite3.Error:
        return None
    return str(row[0]) if row and row[0] else None


__all__ = ["snapshot_database_name", "snapshot_sqlite"]
