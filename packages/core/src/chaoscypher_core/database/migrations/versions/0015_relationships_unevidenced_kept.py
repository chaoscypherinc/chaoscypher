# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Count the relationships the evidence filter keeps flagged instead of dropping.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-07 23:30:00.000000+00:00

Keep, flag, never delete (2026-10-07): a relationship between two entities of
its chunk whose cited sentence does not state it is kept, flagged
``properties.unevidenced`` with the reason in ``properties.evidence_reason``.
``sources.relationships_unevidenced_kept`` counts those;
``sources.evidence_relationships_dropped`` now counts only the relationships
the filter still drops, an endpoint that is no entity of the chunk (before,
every relationship without evidence, all of them dropped). Purely additive,
hence ``safe_auto``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# Revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ChaosCypher migration metadata — consumed by the runner's tier classifier.
# Tier values: "safe_auto" | "needs_confirmation" | "manual"
CC_TIER: str = "safe_auto"
CC_DESCRIPTION: str = "Add the unevidenced-relationships-kept counter"


def upgrade() -> None:
    """Add the counter column (existing rows start at 0, then no server default)."""
    with op.batch_alter_table("sources") as batch_op:
        batch_op.add_column(
            sa.Column(
                "relationships_unevidenced_kept", sa.Integer(), nullable=False, server_default="0"
            )
        )
    with op.batch_alter_table("sources") as batch_op:
        batch_op.alter_column("relationships_unevidenced_kept", server_default=None)


def downgrade() -> None:
    """Drop the counter column."""
    with op.batch_alter_table("sources") as batch_op:
        batch_op.drop_column("relationships_unevidenced_kept")
