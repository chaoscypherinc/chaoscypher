# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Count the per-chunk entities kept flagged instead of dropped.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-07 23:00:00.000000+00:00

Keep, flag, never delete (2026-10-07): an entity the plausibility score puts
below the bar is kept when it carries evidence, flagged
``properties.low_plausibility`` (``sources.plausibility_entities_flagged``),
and one whose written type no core type answers to is kept as a ``Concept``
flagged ``properties.unplaced_type`` (``sources.unplaced_type_entities_flagged``).
Until then both were dropped and the first was counted nowhere. Purely
additive, hence ``safe_auto``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# Revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ChaosCypher migration metadata — consumed by the runner's tier classifier.
# Tier values: "safe_auto" | "needs_confirmation" | "manual"
CC_TIER: str = "safe_auto"
CC_DESCRIPTION: str = "Add the flagged-entity counters (plausibility, unplaced type)"

COLUMNS = ("plausibility_entities_flagged", "unplaced_type_entities_flagged")


def upgrade() -> None:
    """Add the counter columns (existing rows start at 0, then no server default)."""
    with op.batch_alter_table("sources") as batch_op:
        for name in COLUMNS:
            batch_op.add_column(sa.Column(name, sa.Integer(), nullable=False, server_default="0"))
    with op.batch_alter_table("sources") as batch_op:
        for name in COLUMNS:
            batch_op.alter_column(name, server_default=None)


def downgrade() -> None:
    """Drop the counter columns."""
    with op.batch_alter_table("sources") as batch_op:
        for name in COLUMNS:
            batch_op.drop_column(name)
