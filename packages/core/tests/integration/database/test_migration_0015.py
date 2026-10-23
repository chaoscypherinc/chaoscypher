# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Roundtrip test for migration 0015 — the unevidenced-relationships-kept counter.

Keep, flag, never delete (2026-10-07): the evidence filter keeps a
relationship without evidence between two entities of its chunk, flagged
``properties.unevidenced``, and counts it in
``sources.relationships_unevidenced_kept``; ``evidence_relationships_dropped``
stays and now counts only the invalid-endpoint drops. Pins that the upgrade
adds the column in the model's shape (NOT NULL, no server default) and that
the downgrade removes it.
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


def test_0015_adds_the_counter_in_the_model_shape(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0015")
        counter = table_columns(db, "sources")["relationships_unevidenced_kept"]
        assert counter.notnull
        assert counter.default is None
        assert "evidence_relationships_dropped" in table_columns(db, "sources")  # meaning moved
    finally:
        evict_engine(db)


def test_0015_downgrade_removes_the_counter(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0015")
        downgrade_to(db, "0014")
        assert "relationships_unevidenced_kept" not in table_columns(db, "sources")
        assert "evidence_relationships_dropped" in table_columns(db, "sources")
        assert "plausibility_entities_flagged" in table_columns(db, "sources")  # 0014's stay
    finally:
        evict_engine(db)
