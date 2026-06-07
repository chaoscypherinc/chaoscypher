# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract test: applying the baseline migration produces the same schema
as SQLModel.metadata.create_all().
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite import models as _models  # noqa: F401
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.database.migrations.runner import upgrade_to_head


def _table_sqls(db_path: Path) -> dict[str, str]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'alembic_%' "
            "ORDER BY name"
        ).fetchall()
    finally:
        conn.close()
    return {name: (sql or "") for name, sql in rows}


def _normalize(sql: str) -> str:
    """Whitespace + case normalization so the comparison is semantic.

    SQLite's stored ``sqlite_master.sql`` preserves original quoting and
    whitespace exactly, which differs between create_all() and Alembic's
    emitter even when the resulting schema is identical. We strip and
    lowercase so those incidental differences don't fail the test.
    """
    return " ".join(sql.split()).lower()


def _columns_for(db_path: Path, table: str) -> set[tuple[str, str, int]]:
    """Return (name, type, notnull) tuples for every column in ``table``.

    Drops ``dflt_value`` from the comparison — Alembic and create_all
    render default values slightly differently (e.g., quoted vs unquoted
    integers) without semantic difference.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    finally:
        conn.close()
    # row = (cid, name, type, notnull, dflt_value, pk)
    return {(name, col_type.upper(), notnull) for _, name, col_type, notnull, *_ in rows}


def test_baseline_table_set_matches_create_all(tmp_path: Path) -> None:
    # DB1: built via Alembic baseline.
    db_alembic = tmp_path / "alembic.db"
    upgrade_to_head(db_alembic)

    # DB2: built via SQLModel.metadata.create_all.
    db_create = tmp_path / "create.db"
    engine = get_engine(db_create)
    SQLModel.metadata.create_all(engine)

    tables_a = set(_table_sqls(db_alembic))
    tables_c = set(_table_sqls(db_create))

    # The baseline's table set must exactly equal create_all()'s.
    # Any drift means the baseline missed a model.
    assert tables_a == tables_c, (
        f"table set differs.\n"
        f"  alembic-only: {sorted(tables_a - tables_c)}\n"
        f"  create_all-only: {sorted(tables_c - tables_a)}"
    )


def test_baseline_columns_match_create_all(tmp_path: Path) -> None:
    # Same setup as above — build a DB via each path and diff.
    db_alembic = tmp_path / "alembic.db"
    upgrade_to_head(db_alembic)

    db_create = tmp_path / "create.db"
    engine = get_engine(db_create)
    SQLModel.metadata.create_all(engine)

    tables = set(_table_sqls(db_create))
    drift: dict[str, tuple[set, set]] = {}
    for table in sorted(tables):
        cols_a = _columns_for(db_alembic, table)
        cols_c = _columns_for(db_create, table)
        if cols_a != cols_c:
            drift[table] = (cols_a - cols_c, cols_c - cols_a)

    assert not drift, (
        "per-table column drift:\n"
        + "\n".join(
            f"  {t}: alembic-extra={sorted(a)} create-extra={sorted(c)}"
            for t, (a, c) in sorted(drift.items())
        )
    )
