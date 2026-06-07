# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: transition_source_status only updates the target database."""

from __future__ import annotations

from pathlib import Path

from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.models import SourceStatus


def _make_adapter(tmp_path: Path, db_filename: str, database_name: str) -> SqliteAdapter:
    db_path = tmp_path / db_filename
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    adp = SqliteAdapter(str(db_path), database_name=database_name)
    adp.connect()
    return adp


def _seed(adapter: SqliteAdapter, source_id: str, database_name: str, tmp_path: Path) -> None:
    adapter.upload_source(
        source_id=source_id,
        database_name=database_name,
        filename="x.txt",
        file_content=b"x",
        staging_dir=str(tmp_path),
    )
    adapter.update_file(
        source_id,
        database_name,
        {"status": SourceStatus.INDEXED},
    )


def test_transition_only_updates_target_database(tmp_path: Path) -> None:
    """Same source_id in two databases: transition only touches the target."""
    db_a = _make_adapter(tmp_path, "a.db", "alpha")
    db_b = _make_adapter(tmp_path, "b.db", "beta")
    try:
        _seed(db_a, "shared_id", "alpha", tmp_path)
        _seed(db_b, "shared_id", "beta", tmp_path)

        ok = db_a.transition_source_status(
            "shared_id",
            from_status=SourceStatus.INDEXED,
            to_status=SourceStatus.MCP_EXTRACTING,
            database_name="alpha",
        )
        assert ok is True

        src_a = db_a.get_source("shared_id", database_name="alpha")
        src_b = db_b.get_source("shared_id", database_name="beta")
        assert src_a is not None
        assert src_b is not None
        assert src_a["status"] == SourceStatus.MCP_EXTRACTING
        assert src_b["status"] == SourceStatus.INDEXED  # untouched
    finally:
        db_a.disconnect()
        db_b.disconnect()


def test_transition_returns_false_when_db_does_not_match(tmp_path: Path) -> None:
    """CAS with wrong database_name returns False and leaves the row untouched."""
    db_a = _make_adapter(tmp_path, "a.db", "alpha")
    try:
        _seed(db_a, "shared_id", "alpha", tmp_path)

        ok = db_a.transition_source_status(
            "shared_id",
            from_status=SourceStatus.INDEXED,
            to_status=SourceStatus.MCP_EXTRACTING,
            database_name="not_alpha",
        )
        assert ok is False

        src_a = db_a.get_source("shared_id", database_name="alpha")
        assert src_a is not None
        assert src_a["status"] == SourceStatus.INDEXED  # unchanged
    finally:
        db_a.disconnect()
