# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Roundtrip test for migration 0014 — the flagged-entity counters.

Keep, flag, never delete (2026-10-07): the per-chunk plausibility filter keeps
a name below the bar that carries evidence, flagged ``low_plausibility``
(``sources.plausibility_entities_flagged``), and the type rescue keeps an
entity whose written type no core type answers to as a flagged ``Concept``
(``sources.unplaced_type_entities_flagged``). Pins that the upgrade adds both
columns in the model's shape (NOT NULL, no server default) and that the
downgrade removes them.
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

COLUMNS = ("plausibility_entities_flagged", "unplaced_type_entities_flagged")


def test_0014_adds_the_counters_in_the_model_shape(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0014")
        columns = table_columns(db, "sources")
        for name in COLUMNS:
            assert columns[name].notnull
            assert columns[name].default is None
        assert "relationships_dropped_capped" in columns  # kept, meaning moved
    finally:
        evict_engine(db)


def test_0014_downgrade_removes_the_counters(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0014")
        downgrade_to(db, "0013")
        columns = table_columns(db, "sources")
        for name in COLUMNS:
            assert name not in columns
        assert "evidence_less_entities_dropped" in columns
    finally:
        evict_engine(db)
