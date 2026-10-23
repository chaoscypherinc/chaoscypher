# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Roundtrip test for migration 0018: the known-entities guard counters.

The pass-1 guards on the source's known entities drop an alias that names
another known entry (``sources.aliases_crossed_known_dropped``) and demote a
known name its sentences do not evidence (``sources.referents_unevidenced_demoted``).
Pins that the upgrade adds the columns in the model's shape (NOT NULL, no
server default) and the downgrade removes them.
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
    "chaoscypher_core.database.migrations.versions.0018_known_entities_guard_counters"
).COLUMNS


def test_the_columns_are_the_guard_counters() -> None:
    """One list: every column the migration adds is a counter, a model field and reset."""
    assert set(COLUMNS) == {
        QualityCounter.ALIASES_CROSSED_KNOWN_DROPPED.value,
        QualityCounter.REFERENTS_UNEVIDENCED_DEMOTED.value,
    }
    for name in COLUMNS:
        assert name in SourceRow.model_fields
        assert _RESET_DEFAULTS[name] == 0


def test_0018_adds_the_counters_in_the_model_shape(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0018")
        columns = table_columns(db, "sources")
        for name in COLUMNS:
            assert columns[name].notnull
            assert columns[name].default is None
    finally:
        evict_engine(db)


def test_0018_downgrade_removes_the_counters(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0018")
        downgrade_to(db, "0017")
        columns = table_columns(db, "sources")
        for name in COLUMNS:
            assert name not in columns
        assert "excluded_entities_kept" in columns  # 0016's stays
    finally:
        evict_engine(db)
