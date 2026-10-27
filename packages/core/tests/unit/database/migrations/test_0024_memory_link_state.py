# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Migration 0024: a memory's link state, the time it was linked, and the claim on it.

``memory_link_state`` says what happened to the memory's link into the graph,
``memory_linked_at`` when it was linked, ``memory_link_claim`` which process links
it now. Set on memory rows only: a committed memory that attests a node is
``linked``, one that attests nothing ``linked_empty``, every other memory
``pending`` (its reason is unknown). Documents keep all three NULL. It writes row
values, so ``needs_confirmation``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from chaoscypher_core.adapters.sqlite.engine import evict_engine
from chaoscypher_core.database.migrations.runner import downgrade_to, upgrade_to
from tests.unit.database.migrations.rows import insert_row


_COLUMNS = ("memory_link_state", "memory_linked_at", "memory_link_claim")
_COMMITTED_AT = "2026-10-20 10:00:00.000000"
_IMPORTED_AT = "2026-10-21 08:30:00.000000"


def _columns(db_path: Path, table: str) -> dict[str, tuple[str, int, str | None]]:
    """``name -> (declared type, notnull, default)`` from ``PRAGMA table_info``."""
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1]: (r[2].upper(), r[3], r[4]) for r in rows}


def _link_columns(db_path: Path, source_id: str) -> tuple[Any, Any, Any]:
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT memory_link_state, memory_linked_at, memory_link_claim FROM sources "
            "WHERE id = ?",
            (source_id,),
        ).fetchone()
    return row[0], row[1], row[2]


def test_upgrade_adds_three_nullable_columns(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0024")
        sources = _columns(db, "sources")
        assert sources["memory_link_state"] == ("VARCHAR", 0, None)
        assert sources["memory_linked_at"] == ("DATETIME", 0, None)
        assert sources["memory_link_claim"] == ("VARCHAR", 0, None)
    finally:
        evict_engine(db)


def test_migration_0024_sets_linked_linked_empty_and_pending(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0023")
        with sqlite3.connect(str(db)) as conn:
            insert_row(conn, "graph_templates", id="t1", name="Person", template_type="node")
            insert_row(conn, "graph_nodes", id="n1", template_id="t1", label="Pierre")
            memory = {"source_type": "memory", "database_name": "default"}
            insert_row(
                conn,
                "sources",
                id="linked",
                commit_complete=1,
                commit_completed_at=_COMMITTED_AT,
                processing_status="committed",
                **memory,
            )
            insert_row(conn, "graph_node_sources", node_id="n1", source_id="linked")
            insert_row(
                conn,
                "sources",
                id="empty",
                commit_complete=1,
                commit_completed_at=_COMMITTED_AT,
                processing_status="committed",
                **memory,
            )
            insert_row(
                conn,
                "sources",
                id="indexed",
                commit_complete=0,
                processing_status="indexed",
                **memory,
            )
            insert_row(
                conn,
                "sources",
                id="no_chunk",
                commit_complete=0,
                processing_status="pending",
                **memory,
            )
            insert_row(
                conn,
                "sources",
                id="document",
                source_type="file",
                commit_complete=1,
                commit_completed_at=_COMMITTED_AT,
                processing_status="committed",
                database_name="default",
            )
            insert_row(
                conn,
                "sources",
                id="untyped",
                commit_complete=0,
                processing_status="indexed",
                database_name="default",
            )
            insert_row(conn, "graph_node_sources", node_id="n1", source_id="document")
            # a memory imported from a pack before 0.6.0: the importer writes
            # status committed and leaves commit_complete 0 and commit_completed_at NULL
            insert_row(
                conn,
                "sources",
                id="imported",
                commit_complete=0,
                processing_status="committed",
                updated_at=_IMPORTED_AT,
                **memory,
            )
            insert_row(conn, "graph_node_sources", node_id="n1", source_id="imported")
            insert_row(
                conn,
                "sources",
                id="imported_empty",
                commit_complete=0,
                processing_status="committed",
                updated_at=_IMPORTED_AT,
                **memory,
            )

        upgrade_to(db, "0024")

        state, linked_at, claim = _link_columns(db, "linked")
        assert (state, claim) == ("linked", None)
        assert linked_at == _COMMITTED_AT
        state, linked_at, claim = _link_columns(db, "empty")
        assert (state, claim) == ("linked_empty", None)
        assert linked_at == _COMMITTED_AT
        assert _link_columns(db, "indexed") == ("pending", None, None)
        assert _link_columns(db, "no_chunk") == ("pending", None, None)
        assert _link_columns(db, "document") == (None, None, None)
        assert _link_columns(db, "untyped") == (None, None, None)
        assert _link_columns(db, "imported") == ("linked", _IMPORTED_AT, None)
        assert _link_columns(db, "imported_empty") == ("linked_empty", _IMPORTED_AT, None)
    finally:
        evict_engine(db)


def test_0024_roundtrip_keeps_the_rows(tmp_path: Path) -> None:
    """upgrade, downgrade, upgrade again: the rows survive and get their states back."""
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0023")
        with sqlite3.connect(str(db)) as conn:
            insert_row(
                conn,
                "sources",
                id="memo",
                source_type="memory",
                database_name="default",
                commit_complete=0,
                processing_status="indexed",
                memory_by="alice",
            )
        upgrade_to(db, "0024")
        assert _link_columns(db, "memo") == ("pending", None, None)
        downgrade_to(db, "0023")
        with sqlite3.connect(str(db)) as conn:
            row = conn.execute("SELECT memory_by FROM sources WHERE id = 'memo'").fetchone()
        assert row == ("alice",)
        upgrade_to(db, "0024")
        assert _link_columns(db, "memo") == ("pending", None, None)
    finally:
        evict_engine(db)


def test_downgrade_drops_the_three_columns(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0024")
        downgrade_to(db, "0023")
        sources = _columns(db, "sources")
        for name in _COLUMNS:
            assert name not in sources, name
        assert "memory_scope" in sources
    finally:
        evict_engine(db)


def test_migration_0024_tier_is_needs_confirmation() -> None:
    from chaoscypher_core.database.migrations.tiers import read_migration_info

    assert read_migration_info("0024").tier.value == "needs_confirmation"
