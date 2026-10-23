# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The schema reads the migration roundtrip tests share (one PRAGMA reader, not one per file)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class ColumnInfo:
    """One column as ``PRAGMA table_info`` reports it.

    Attributes:
        type: The declared type (``INTEGER``, ``JSON``, ...).
        notnull: Whether the column is NOT NULL.
        default: The server default's SQL text, or None for none.
    """

    type: str
    notnull: bool
    default: str | None


def table_columns(db_path: Path, table: str) -> dict[str, ColumnInfo]:
    """Return ``{column: ColumnInfo}`` for ``table`` in the database at ``db_path``.

    The connection is closed explicitly (``with sqlite3.connect`` commits but
    does not close), so none leaks into later tests' ResourceWarning detectors.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    finally:
        conn.close()
    return {row[1]: ColumnInfo(type=row[2], notnull=bool(row[3]), default=row[4]) for row in rows}
