# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Regression: SearchRepository refuses to serve on DDL/config dim mismatch.

Pre-launch review F4. ``_check_model_change`` only triggers the
recreate path when ``search_metadata.vector_dim`` is present and
differs from the configured dim. When metadata is missing (e.g.
backup restored without it) the existing tables stay at whatever
dim they were built with — and sqlite-vec does not validate
dimension at query time. The new ``_assert_vec_table_dim_consistency``
hook reads the live CREATE statement out of ``sqlite_master`` and
raises ``SchemaIntegrityError`` with the operator-facing rebuild
instruction.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from sqlalchemy import text

from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
from chaoscypher_core.adapters.sqlite.repos import SearchRepository
from chaoscypher_core.exceptions import SchemaIntegrityError


def test_vec_table_dim_mismatch_raises_schema_integrity_error() -> None:
    """A pre-existing vec0 table at the wrong dim refuses to construct."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "app.db"
        engine = get_engine(db_path)

        # Pre-seed a vec0 table at dim=4. Simulates a backup restored
        # from an embedding model that emits 4-dim vectors.
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE VIRTUAL TABLE vec_search_chunks USING vec0("
                    "embedding float[4],"
                    "+item_id TEXT"
                    ")"
                )
            )

        # Construct SearchRepository at dim=8. The IF NOT EXISTS path in
        # _init_schema preserves the existing float[4] table; the empty
        # search_metadata short-circuits _check_model_change's recreate;
        # the assertion must fire and refuse to serve.
        with pytest.raises(SchemaIntegrityError) as excinfo:
            SearchRepository(engine=engine, vector_dim=8, embedding_model="test-model")

        assert "vec_search_chunks" in excinfo.value.message
        assert "float[4]" in excinfo.value.message
        assert "8" in excinfo.value.message
        assert "rebuild-search" in excinfo.value.message.lower()
        assert excinfo.value.code == "SCHEMA_INTEGRITY_ERROR"

        evict_engine(db_path)


def test_vec_table_dim_consistent_does_not_raise() -> None:
    """Matching DDL / config dim constructs cleanly (regression guard)."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "app.db"
        engine = get_engine(db_path)

        # First construction creates tables at dim=4 — no metadata yet.
        SearchRepository(engine=engine, vector_dim=4, embedding_model="test-model")
        # Second construction at the same dim must not raise — the
        # assertion sees DDL=4 matching config=4.
        SearchRepository(engine=engine, vector_dim=4, embedding_model="test-model")

        evict_engine(db_path)
