# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Migration/model fidelity regression.

A silent-drift class the autogenerate-diff and up→down→up roundtrip tests
can't catch on SQLite:

* The ``ix_sources_database_name_created_at_desc`` /
  ``ix_llm_call_metrics_database_name_started_at_desc`` composite indexes
  serve ``ORDER BY <col> DESC`` reads. Their names end in ``_desc`` but the
  columns are declared *ascending* (SQLite reverse-scans an ascending
  composite index for a DESC order-by, so a DESC expression index is
  unnecessary and would produce a perpetual false autogenerate diff). SQLite
  can't reflect index sort order, so autogenerate stays silent if the model
  and the migration ever disagree on direction. This test pins that the two
  paths — ``create_all`` from the SQLModel metadata and ``upgrade_to_head``
  from the Alembic baseline — emit byte-identical index DDL.

The 0011-downgrade fidelity case that used to live here was retired with the
2026-06-02 migration squash: revisions 0010/0011 (and the legacy status
CHECK constraints they juggled) no longer exist as separate migrations, and
the consolidated baseline carries only the constraints declared by the
current models — which the baseline-vs-metadata parity tests already verify.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlmodel import SQLModel

import chaoscypher_core.adapters.sqlite.models  # noqa: F401 — register metadata
from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
from chaoscypher_core.database.migrations.runner import upgrade_to


def _index_sql(db_path: Path, index_name: str) -> str:
    """Return the normalized CREATE INDEX DDL for ``index_name``.

    Lowercased, unquoted, whitespace-collapsed so cosmetic create_all-vs-
    migration rendering differences don't trip the comparison — but the
    column list (and any ``desc``) is preserved so a real sort-order drift
    still shows.
    """
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name = ?", (index_name,)
        ).fetchone()
    sql = (row[0] if row and row[0] else "") or ""
    return " ".join(sql.lower().replace('"', "").split())


def test_model_and_migration_composite_indexes_agree(tmp_path: Path) -> None:
    """The composite indexes must be identical whether built from the
    SQLModel metadata (create_all) or from the Alembic baseline.

    The original drift: a migration created ``created_at DESC`` expression
    indexes while the model declared the columns ascending. SQLite can't
    reflect sort order so autogenerate stayed silent, but the two definitions
    genuinely disagreed. Both now declare plain ascending columns; this test
    locks that agreement against the post-squash baseline.
    """
    model_db = tmp_path / "model.db"
    migrated_db = tmp_path / "migrated.db"
    sqlite3.connect(str(model_db)).close()
    sqlite3.connect(str(migrated_db)).close()
    try:
        SQLModel.metadata.create_all(get_engine(model_db))
        upgrade_to(migrated_db, "head")

        for index_name in (
            "ix_sources_database_name_created_at_desc",
            "ix_llm_call_metrics_database_name_started_at_desc",
        ):
            model_ddl = _index_sql(model_db, index_name)
            migration_ddl = _index_sql(migrated_db, index_name)
            assert model_ddl, f"model create_all did not create {index_name}"
            assert migration_ddl, f"migration did not create {index_name}"
            assert model_ddl == migration_ddl, (
                f"{index_name} DDL drift between model and migration:\n"
                f"  model:     {model_ddl}\n"
                f"  migration: {migration_ddl}"
            )
    finally:
        evict_engine(model_db)
        evict_engine(migrated_db)
