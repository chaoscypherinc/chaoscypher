# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Roundtrip test for migration 0016 — the kept-flagged counters of the last three drop sites.

Keep, flag, never delete (2026-10-08): the entity evidence filter keeps an
entity with a description its sentence does not name
(``sources.entities_unevidenced_kept``), the exclusion filter an excluded name
that carries evidence (``sources.excluded_entities_kept``), and the strict
type-constraint check a relationship it refuses
(``sources.relationships_unmatched_label_kept``,
``sources.relationships_type_mismatch_kept``). Pins that the upgrade adds the
columns in the model's shape (NOT NULL, no server default) and the downgrade
removes them, leaving the counters whose meaning moved.
"""

from __future__ import annotations

import importlib
import sqlite3
from typing import TYPE_CHECKING

import chaoscypher_core.adapters.sqlite.models  # noqa: F401 — register metadata
from chaoscypher_core.adapters.sqlite.engine import evict_engine
from chaoscypher_core.adapters.sqlite.models import SourceRow
from chaoscypher_core.database.migrations.runner import downgrade_to, upgrade_to
from chaoscypher_core.services.quality.counters import _RESET_DEFAULTS, QualityCounter

from ._schema import table_columns


if TYPE_CHECKING:
    from pathlib import Path

COLUMNS: tuple[str, ...] = importlib.import_module(
    "chaoscypher_core.database.migrations.versions.0016_kept_flagged_counters"
).COLUMNS
MOVED = ("evidence_entities_dropped", "relationships_dropped_type_unmatched")


def test_the_columns_are_the_new_counters() -> None:
    """One list: every column the migration adds is a counter, a model field and reset."""
    counters = {c.value for c in QualityCounter}
    for name in COLUMNS:
        assert name in counters
        assert name in SourceRow.model_fields
        assert _RESET_DEFAULTS[name] == 0


def test_0016_adds_the_counters_in_the_model_shape(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0016")
        columns = table_columns(db, "sources")
        for name in COLUMNS:
            assert columns[name].notnull
            assert columns[name].default is None
        for name in MOVED:  # kept, meaning moved
            assert name in columns
    finally:
        evict_engine(db)


def test_0016_downgrade_removes_the_counters(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0016")
        downgrade_to(db, "0015")
        columns = table_columns(db, "sources")
        for name in COLUMNS:
            assert name not in columns
        for name in MOVED:
            assert name in columns
        assert "relationships_unevidenced_kept" in columns  # 0015's stays
    finally:
        evict_engine(db)
