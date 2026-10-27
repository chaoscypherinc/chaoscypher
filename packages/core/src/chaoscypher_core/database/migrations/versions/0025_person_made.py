# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Person-made graph rows: graph nodes and edges say whether a person made them.

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-25 12:00:00.000000+00:00

A node or edge a person made (``create_node``, ``create_edge``: the MCP tools, the API,
the SDK, the CLI) belongs to that person: a source attesting it (a memory attached to
it) is released without deleting or rewriting it. Until 0025 that was inferred from
``source_id IS NULL``, which also reads true for a CCX import's edges before their
rebuild and for any row whose home was deleted without the rebuild. ``person_made``
(NOT NULL, default 0) on ``graph_nodes`` and ``graph_edges`` says it instead.

The upgrade marks a row person-made when it has no home source, no membership row and no
CCX IRI: before 0.6.0 every source wrote a membership (and the importer's rebuild homed
its edges), and only the importer sets ``ccx_iri``, so such a row was made by a person.
The column keeps its server default, which the model declares too. It writes row
values, so ``needs_confirmation``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# Revision identifiers, used by Alembic.
revision: str = "0025"
down_revision: str | Sequence[str] | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ChaosCypher migration metadata — consumed by the runner's tier classifier.
# Tier values: "safe_auto" | "needs_confirmation" | "manual"
CC_TIER: str = "needs_confirmation"
CC_DESCRIPTION: str = (
    "Add person_made to graph_nodes and graph_edges and mark the rows a person made "
    "(no home source, no membership, no CCX IRI: source_id IS NULL AND ccx_iri IS NULL)"
)

_TABLES = (
    ("graph_nodes", "graph_node_sources", "node_id"),
    ("graph_edges", "graph_edge_sources", "edge_id"),
)


def upgrade() -> None:
    """Add the flag (default 0), then mark the rows with no home and no membership."""
    for table, members, owner in _TABLES:
        op.add_column(
            table,
            sa.Column("person_made", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
        op.execute(
            f"UPDATE {table} SET person_made = 1 WHERE source_id IS NULL AND ccx_iri IS NULL "
            f"AND NOT EXISTS (SELECT 1 FROM {members} m WHERE m.{owner} = {table}.id)"
        )


def downgrade() -> None:
    """Drop the flag."""
    for table, _members, _owner in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column("person_made")
