# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Roundtrip test for migration 0013 — the evidence-less orphan drop counter.

Keep, flag, never delete (2026-10-07): the commit keeps an edgeless entity
that carries evidence, flagged ``properties.orphan``, and counts the ones it
still drops in ``sources.evidence_less_entities_dropped``. Pins that the
upgrade adds the column in the model's shape (NOT NULL, no server default,
existing rows at 0) and that the downgrade removes it.
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


def test_0013_adds_the_counter_in_the_model_shape(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0013")
        counter = table_columns(db, "sources")["evidence_less_entities_dropped"]
        assert counter.notnull
        assert counter.default is None
        assert "orphan_entities_filtered" in table_columns(db, "sources")  # kept, meaning moved
    finally:
        evict_engine(db)


def test_0013_downgrade_removes_the_counter(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0013")
        downgrade_to(db, "0012")
        assert "evidence_less_entities_dropped" not in table_columns(db, "sources")
        assert "orphan_entities_filtered" in table_columns(db, "sources")
    finally:
        evict_engine(db)
