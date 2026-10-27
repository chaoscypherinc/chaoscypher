# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Migration 0025: graph nodes and edges say whether a person made them (``person_made``).

A row a person made (``create_node``, ``create_edge``) is never deleted or rewritten by a
source's release; until 0025 that was inferred from ``source_id IS NULL``, which a CCX
import's edges and any row whose home was deleted without the rebuild also read as. The
upgrade marks a row person-made when it has no home source and no membership row: before
0.6.0 every source wrote a membership (and the importer's rebuild homed its edges), so
such a row was made by a person.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from chaoscypher_core.adapters.sqlite.engine import evict_engine
from chaoscypher_core.database.migrations.runner import downgrade_to, upgrade_to
from tests.unit.database.migrations.rows import insert_row


def _person_made(db: Path, table: str) -> dict[str, int]:
    with sqlite3.connect(str(db)) as conn:
        return dict(conn.execute(f"SELECT id, person_made FROM {table}").fetchall())


def _columns(db: Path, table: str) -> dict[str, tuple[str, int]]:
    with sqlite3.connect(str(db)) as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1]: (r[2].upper(), r[3]) for r in rows}


def _graph(db: Path) -> None:
    """Three nodes and two edges: a person's, a source's, and one homed nowhere but attested."""
    with sqlite3.connect(str(db)) as conn:
        insert_row(conn, "sources", id="s1", database_name="default")
        insert_row(conn, "graph_templates", id="t1", name="Thing", template_type="node")
        insert_row(conn, "graph_templates", id="t2", name="rel", template_type="edge")
        insert_row(conn, "graph_nodes", id="person", template_id="t1", label="Journal")
        insert_row(conn, "graph_nodes", id="sourced", template_id="t1", label="Pierre")
        conn.execute("UPDATE graph_nodes SET source_id = 's1' WHERE id = 'sourced'")
        insert_row(conn, "graph_nodes", id="attested", template_id="t1", label="Lyon")
        insert_row(conn, "graph_node_sources", node_id="attested", source_id="s1")
        # an imported row no source cites: the importer set its IRI, so no person made it
        insert_row(conn, "graph_nodes", id="imported", template_id="t1", label="Moscow")
        conn.execute("UPDATE graph_nodes SET ccx_iri = 'urn:cc:node:moscow' WHERE id = 'imported'")
        for edge_id, src, tgt in (
            ("person_edge", "person", "attested"),
            ("src_edge", "sourced", "attested"),
        ):
            insert_row(
                conn,
                "graph_edges",
                id=edge_id,
                template_id="t2",
                source_node_id=src,
                target_node_id=tgt,
                label="rel",
            )
        conn.execute("UPDATE graph_edges SET source_id = 's1' WHERE id = 'src_edge'")


def test_upgrade_adds_a_not_null_flag_to_nodes_and_edges(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0025")
        assert _columns(db, "graph_nodes")["person_made"] == ("BOOLEAN", 1)
        assert _columns(db, "graph_edges")["person_made"] == ("BOOLEAN", 1)
    finally:
        evict_engine(db)


def test_migration_0025_marks_unhomed_unattested_rows_person_made(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0024")
        _graph(db)

        upgrade_to(db, "0025")

        assert _person_made(db, "graph_nodes") == {"person": 1, "sourced": 0, "attested": 0, "imported": 0}
        assert _person_made(db, "graph_edges") == {"person_edge": 1, "src_edge": 0}
    finally:
        evict_engine(db)


def test_0025_roundtrip_keeps_the_rows(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0024")
        _graph(db)
        upgrade_to(db, "0025")
        downgrade_to(db, "0024")
        assert "person_made" not in _columns(db, "graph_nodes")
        assert "person_made" not in _columns(db, "graph_edges")
        upgrade_to(db, "0025")
        assert _person_made(db, "graph_nodes") == {"person": 1, "sourced": 0, "attested": 0, "imported": 0}
    finally:
        evict_engine(db)
