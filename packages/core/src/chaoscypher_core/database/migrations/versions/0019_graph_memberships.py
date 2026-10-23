# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Record which sources attest each graph node and edge.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-18 12:00:00.000000+00:00

Identity resolution merges nodes across sources, but a node carries one
``source_id``, so deleting one source destroyed or stranded merged nodes
(#795, spec 2026-10-17 D1/D2). ``graph_node_sources`` and ``graph_edge_sources``
hold one row per (node or edge, source) with the snapshot that source wrote.

The backfill (a clean break, no other data migration) writes one membership per
existing row from its ``source_id``. Its snapshot is the row's own record, built
as ``services.graph.resolution.apply.node_record`` / ``edge_record`` build it
(a test pins the two against each other).

``source_id`` on ``graph_nodes``, ``graph_edges`` and ``graph_templates`` stays
the home source but turns from ``ON DELETE CASCADE`` to ``ON DELETE SET NULL``:
the source row no longer takes graph rows with it, the membership release does
(D1/D5). SQLite cannot alter a foreign key, so the three tables are rebuilt
(``batch_alter_table``), hence ``needs_confirmation``.

Each snapshot holds a copy of the row's properties, so the stored graph
properties roughly double in size.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op


# Revision identifiers, used by Alembic.
revision: str = "0019"
down_revision: str | Sequence[str] | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ChaosCypher migration metadata — consumed by the runner's tier classifier.
# Tier values: "safe_auto" | "needs_confirmation" | "manual"
CC_TIER: str = "needs_confirmation"
CC_DESCRIPTION: str = (
    "Add graph node/edge source memberships, backfill one per existing row, and make "
    "graph source_id foreign keys ON DELETE SET NULL (rebuilds graph_nodes/edges/templates; "
    "the snapshots roughly double the stored graph-property size)"
)

# ``name_identity.GIVEN_ALIASES_KEY`` as of this revision; frozen here on purpose.
_GIVEN_ALIASES_KEY = "aliases_given"
_BATCH = 500

#: The tables whose ``source_id`` names a home source, not an owner.
_HOMED = ("graph_nodes", "graph_edges", "graph_templates")
#: Names the unnamed baseline foreign keys so batch mode can drop one (SQLite reflects them nameless).
_FK_NAMES = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def _props(raw: Any) -> dict[str, Any]:
    """A JSON column read through ``sa.text`` arrives as a string (or None)."""
    if isinstance(raw, str):
        raw = json.loads(raw)
    return dict(raw) if isinstance(raw, dict) else {}


def _node_snapshot(row: Any) -> dict[str, Any]:
    props = _props(row.properties)
    record: dict[str, Any] = {
        "id": row.id,
        "label": row.label,
        "type": row.entity_type or "",
        "aliases": [str(a) for a in props.get("aliases") or [] if a],
        "properties": props,
    }
    if isinstance(props.get(_GIVEN_ALIASES_KEY), list):
        record[_GIVEN_ALIASES_KEY] = [str(a) for a in props[_GIVEN_ALIASES_KEY] if a]
    return record


def _edge_snapshot(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "source": row.source_node_id,
        "target": row.target_node_id,
        "label": row.label,
        "properties": _props(row.properties),
    }


def _create(table: str, owner: str, owner_table: str) -> None:
    op.create_table(
        table,
        sa.Column(owner, sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint([owner], [f"{owner_table}.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint(owner, "source_id"),
    )
    op.create_index(f"ix_{table}_source_id", table, ["source_id"])


def _backfill(table: str, owner: str, owner_table: str, columns: str, snapshot: Any) -> None:
    """One membership per row whose home names an existing source, streamed ``_BATCH`` rows at a time.

    The runner applies migrations with foreign keys off, so the source filter is
    explicit: a home of ``''`` or of a source row that is gone gets no
    membership (it would name no source, and no release could ever take it).
    """
    conn = op.get_bind()
    insert = sa.text(
        f"INSERT INTO {table} ({owner}, source_id, snapshot) VALUES (:owner, :source_id, :snapshot)"
    )
    rows = conn.execute(
        sa.text(
            f"SELECT id, source_id, {columns} FROM {owner_table} "
            "WHERE source_id IS NOT NULL AND source_id <> '' "
            "AND source_id IN (SELECT id FROM sources)"
        )
    )
    while chunk := rows.fetchmany(_BATCH):
        conn.execute(
            insert,
            [
                {"owner": r.id, "source_id": r.source_id, "snapshot": json.dumps(snapshot(r))}
                for r in chunk
            ],
        )


def _source_fk(ondelete: str) -> None:
    """Rebuild each homed table with its ``source_id`` foreign key on ``ondelete``."""
    for table in _HOMED:
        name = f"fk_{table}_source_id_sources"
        with op.batch_alter_table(table, naming_convention=_FK_NAMES) as batch_op:
            batch_op.drop_constraint(name, type_="foreignkey")
            batch_op.create_foreign_key(name, "sources", ["source_id"], ["id"], ondelete=ondelete)


def upgrade() -> None:
    """Create both membership tables, write one membership per existing row, unhome on delete."""
    _create("graph_node_sources", "node_id", "graph_nodes")
    _create("graph_edge_sources", "edge_id", "graph_edges")
    _backfill(
        "graph_node_sources",
        "node_id",
        "graph_nodes",
        "label, entity_type, properties",
        _node_snapshot,
    )
    _backfill(
        "graph_edge_sources",
        "edge_id",
        "graph_edges",
        "source_node_id, target_node_id, label, properties",
        _edge_snapshot,
    )
    _source_fk("SET NULL")


def downgrade() -> None:
    """Put the cascading ``source_id`` foreign keys back and drop both membership tables."""
    _source_fk("CASCADE")
    op.drop_index("ix_graph_edge_sources_source_id", table_name="graph_edge_sources")
    op.drop_table("graph_edge_sources")
    op.drop_index("ix_graph_node_sources_source_id", table_name="graph_node_sources")
    op.drop_table("graph_node_sources")
