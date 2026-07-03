# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""add full_text store column to sources

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-20 00:00:00.000000+00:00

CCX 3.0 migration. ``sources`` gains a nullable ``full_text`` TEXT column
holding the extracted plain text of the source, so an exported CCX package
can carry the full text without re-deriving it from chunks. Purely additive
(nullable TEXT column), hence ``safe_auto``.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ChaosCypher migration metadata — consumed by the runner's tier classifier.
# Tier values: "safe_auto" | "needs_confirmation" | "manual"
CC_TIER: str = "safe_auto"
CC_DESCRIPTION: str = "Add full_text store column to sources"


def upgrade() -> None:
    """Add a nullable ``full_text`` TEXT column to ``sources``."""
    with op.batch_alter_table("sources") as batch_op:
        batch_op.add_column(sa.Column("full_text", sa.Text(), nullable=True))


def downgrade() -> None:
    """Drop the ``full_text`` column from ``sources``."""
    with op.batch_alter_table("sources") as batch_op:
        batch_op.drop_column("full_text")
