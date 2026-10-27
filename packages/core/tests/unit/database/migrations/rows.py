# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Rows a migration test writes before an upgrade: every NOT NULL column filled."""

from __future__ import annotations

import sqlite3
from typing import Any


def fill_value(declared: str) -> Any:
    """A value for a NOT NULL column without a default, by its declared type."""
    if declared in ("INTEGER", "BOOLEAN"):
        return 0
    if declared == "DATETIME":
        return "2026-10-18 09:00:00.000000"
    if declared == "JSON":
        return "{}"
    return "x"


def insert_row(conn: sqlite3.Connection, table: str, **values: Any) -> None:
    """Insert one row, filling the NOT NULL columns the baseline gives no default."""
    for row in conn.execute(f"PRAGMA table_info({table})").fetchall():
        name, declared, notnull, default = row[1], row[2].upper(), row[3], row[4]
        if notnull and default is None and name not in values:
            values[name] = fill_value(declared)
    names = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    conn.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})", tuple(values.values()))
