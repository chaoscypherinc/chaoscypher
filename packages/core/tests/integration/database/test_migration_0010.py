# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Roundtrip test for migration 0010 — task/metrics decode fallbacks and the zero-yield counter.

0010 landed without one (audit wave 6 found it while adding 0011): pins that
the upgrade adds ``chunk_extraction_tasks.decode_fallback``,
``llm_call_metrics.decode_fallback`` and ``sources.llm_chunks_zero_yield``
in the model's shape, and that the downgrade removes them.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import chaoscypher_core.adapters.sqlite.models  # noqa: F401 — register metadata
from chaoscypher_core.adapters.sqlite.engine import evict_engine
from chaoscypher_core.database.migrations.runner import downgrade_to, upgrade_to

from ._schema import table_columns


if TYPE_CHECKING:
    from pathlib import Path


_FALLBACK_TABLES = ("chunk_extraction_tasks", "llm_call_metrics")


def test_0010_adds_columns_in_the_model_shape(tmp_path: Path) -> None:
    """Two nullable JSON fallbacks and a NOT NULL, default-free counter."""
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0010")
        for table in _FALLBACK_TABLES:
            column = table_columns(db, table)["decode_fallback"]
            assert column.type.upper() == "JSON", table
            assert not column.notnull, table
        counter = table_columns(db, "sources")["llm_chunks_zero_yield"]
        assert counter.notnull
        assert counter.default is None
    finally:
        evict_engine(db)


def test_0010_downgrade_removes_columns(tmp_path: Path) -> None:
    """Downgrading 0010 → 0009 removes all three columns."""
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0010")
        downgrade_to(db, "0009")
        for table in _FALLBACK_TABLES:
            assert "decode_fallback" not in table_columns(db, table), table
        assert "llm_chunks_zero_yield" not in table_columns(db, "sources")
    finally:
        evict_engine(db)
