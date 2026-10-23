# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Memory provenance: who remembered a source, from when; an edge's validity window; the forget audit.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-20 12:00:00.000000+00:00

A memory is a source of type ``memory`` (spec 2026-10-20, decisions 2 and 5): ``memory_by``
says who stated it, ``memory_scope`` which scope it belongs to, ``valid_from`` from when it
holds (an ISO date, ``YYYY-MM-DD``). Its facts are edges; a later memory on a single-valued
relation closes the earlier edge (``valid_to`` = the later ``valid_from``,
``closed_by_source_id`` = the later source). ``closed_by_source_id`` carries no foreign key on
purpose: the closing source may be forgotten later, and the release reopens what it closed.

``forget`` records a row in ``memory_audit`` without the text: when, which handle, by whom, in
which scope, how many facts went and how many stayed.

Purely additive: six nullable columns and one table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# Revision identifiers, used by Alembic.
revision: str = "0020"
down_revision: str | Sequence[str] | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ChaosCypher migration metadata — consumed by the runner's tier classifier.
# Tier values: "safe_auto" | "needs_confirmation" | "manual"
CC_TIER: str = "safe_auto"
CC_DESCRIPTION: str = (
    "Add memory provenance columns to sources (memory_by, memory_scope, valid_from) and "
    "graph_edges (valid_from, valid_to, closed_by_source_id), and the memory_audit table"
)

_SOURCE_COLUMNS = ("memory_by", "memory_scope", "valid_from")
_EDGE_COLUMNS = ("valid_from", "valid_to", "closed_by_source_id")


def upgrade() -> None:
    """Add the six nullable columns and the audit table."""
    for name in _SOURCE_COLUMNS:
        op.add_column("sources", sa.Column(name, sa.String(), nullable=True))
    for name in _EDGE_COLUMNS:
        op.add_column("graph_edges", sa.Column(name, sa.String(), nullable=True))
    op.create_table(
        "memory_audit",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("database_name", sa.String(), nullable=False),
        sa.Column("at", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("handle", sa.String(), nullable=False),
        sa.Column("by", sa.String(), nullable=True),
        sa.Column("scope", sa.String(), nullable=True),
        sa.Column("facts_removed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("facts_kept", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memory_audit_database_name_at", "memory_audit", ["database_name", "at"])


def downgrade() -> None:
    """Drop the audit table and the six columns."""
    op.drop_index("ix_memory_audit_database_name_at", table_name="memory_audit")
    op.drop_table("memory_audit")
    with op.batch_alter_table("graph_edges") as batch_op:
        for name in _EDGE_COLUMNS:
            batch_op.drop_column(name)
    with op.batch_alter_table("sources") as batch_op:
        for name in _SOURCE_COLUMNS:
            batch_op.drop_column(name)
