# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Migration 0020: the memory provenance columns and the ``memory_audit`` table.

A memory is a source (``source_type = "memory"``) that says who remembered it, in which scope
and from when; its facts are edges that carry a validity window and the source that closed
them. ``forget`` leaves an audit row without the text. All additive, so ``safe_auto``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_repository import GraphRepository
from chaoscypher_core.database.migrations.runner import upgrade_to_head
from chaoscypher_core.models import EdgeCreate, NodeCreate, TemplateCreate


def _columns(db_path: Path, table: str) -> dict[str, tuple[str, int, str | None]]:
    """``name -> (declared type, notnull, default)`` from ``PRAGMA table_info``."""
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1]: (r[2].upper(), r[3], r[4]) for r in rows}


def test_upgrade_adds_the_provenance_columns_and_the_audit_table(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    sqlite3.connect(str(db_path)).close()
    try:
        upgrade_to_head(db_path)

        sources = _columns(db_path, "sources")
        for name in ("memory_by", "memory_scope", "valid_from"):
            assert sources[name][0] == "VARCHAR" and sources[name][1] == 0, name

        edges = _columns(db_path, "graph_edges")
        for name in ("valid_from", "valid_to", "closed_by_source_id"):
            assert edges[name][0] == "VARCHAR" and edges[name][1] == 0, name

        audit = _columns(db_path, "memory_audit")
        assert set(audit) == {
            "id",
            "database_name",
            "at",
            "action",
            "handle",
            "by",
            "scope",
            "facts_removed",
            "facts_kept",
        }
        assert audit["facts_removed"] == ("INTEGER", 1, "'0'")
        assert audit["facts_kept"] == ("INTEGER", 1, "'0'")
        assert audit["by"][1] == 0 and audit["scope"][1] == 0
        with sqlite3.connect(str(db_path)) as conn:
            indexes = {r[1] for r in conn.execute("PRAGMA index_list(memory_audit)")}
            assert "ix_memory_audit_database_name_at" in indexes
            tier = conn.execute("PRAGMA foreign_key_list(graph_edges)").fetchall()
            assert all(r[3] != "closed_by_source_id" for r in tier), (
                "no foreign key: Task 12 reopens"
            )
    finally:
        evict_engine(db_path)


def test_migration_0020_is_safe_auto() -> None:
    from chaoscypher_core.database.migrations.tiers import read_migration_info

    info = read_migration_info("0020")
    assert info.tier.value == "safe_auto"


def test_an_edge_created_without_validity_reads_back_open(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name="test")
    adapter.connect()
    try:
        repo = GraphRepository(adapter.session, database_name="test")
        repo.create_template(TemplateCreate(name="Person", template_type="node"), custom_id="p")
        repo.create_template(TemplateCreate(name="knows", template_type="edge"), custom_id="k")
        for nid in ("a", "b"):
            repo.create_node(NodeCreate(template_id="p", label=nid.upper()), custom_id=nid)
        edge = repo.create_edge(
            EdgeCreate(template_id="k", source_node_id="a", target_node_id="b", label="knows")
        )
        read = repo.get_edge(edge.id)
        assert read is not None
        assert read.valid_from is None and read.valid_to is None
        assert read.closed_by_source_id is None
    finally:
        adapter.disconnect()
        evict_engine(db_path)
