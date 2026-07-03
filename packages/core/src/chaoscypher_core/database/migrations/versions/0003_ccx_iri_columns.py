# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""add ccx_iri stable-identity column to graph_nodes, graph_edges, sources

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-20 00:00:00.000000+00:00

CCX 3.0 migration. Each graph node, edge, and source gains a nullable,
indexed ``ccx_iri`` column — the stable IRI that anchors a record to its
identity in an exported CCX package, so re-imports can upsert by IRI
rather than minting fresh ids. Purely additive (nullable column + index),
hence ``safe_auto``.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ChaosCypher migration metadata — consumed by the runner's tier classifier.
# Tier values: "safe_auto" | "needs_confirmation" | "manual"
CC_TIER: str = "safe_auto"
CC_DESCRIPTION: str = "Add ccx_iri stable-identity column to graph_nodes, graph_edges, and sources"

_TABLES: tuple[str, ...] = ("graph_nodes", "graph_edges", "sources")


def upgrade() -> None:
    """Add a nullable, indexed ``ccx_iri`` column to each target table."""
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column("ccx_iri", sa.String(), nullable=True))
            batch_op.create_index(f"ix_{table}_ccx_iri", ["ccx_iri"])


def downgrade() -> None:
    """Drop the ``ccx_iri`` index and column from each target table."""
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_index(f"ix_{table}_ccx_iri")
            batch_op.drop_column("ccx_iri")
