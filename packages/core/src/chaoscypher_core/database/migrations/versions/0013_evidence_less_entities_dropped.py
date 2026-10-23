# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Count the orphans the commit drops for carrying no evidence.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-07 21:00:00.000000+00:00

Keep, flag, never delete (2026-10-07): the commit keeps an entity no
relationship references when it carries evidence (a description or an
evidence sentence), flagged ``properties.orphan``, and drops only one carrying
neither. ``sources.orphan_entities_filtered`` now counts the orphans kept
(before, the ones deleted); ``sources.evidence_less_entities_dropped`` counts
the ones dropped. Purely additive, hence ``safe_auto``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# Revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ChaosCypher migration metadata — consumed by the runner's tier classifier.
# Tier values: "safe_auto" | "needs_confirmation" | "manual"
CC_TIER: str = "safe_auto"
CC_DESCRIPTION: str = "Add the evidence-less orphan drop counter"


def upgrade() -> None:
    """Add the counter column (existing rows start at 0, then no server default)."""
    with op.batch_alter_table("sources") as batch_op:
        batch_op.add_column(
            sa.Column(
                "evidence_less_entities_dropped", sa.Integer(), nullable=False, server_default="0"
            )
        )
    with op.batch_alter_table("sources") as batch_op:
        batch_op.alter_column("evidence_less_entities_dropped", server_default=None)


def downgrade() -> None:
    """Drop the counter column."""
    with op.batch_alter_table("sources") as batch_op:
        batch_op.drop_column("evidence_less_entities_dropped")
