# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Count the entities and relationships the last three drop sites keep flagged.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-08 09:00:00.000000+00:00

Keep, flag, never delete (2026-10-08):

- an entity whose cited sentence does not name it but that carries a
  description is kept flagged ``properties.unevidenced``
  (``sources.entities_unevidenced_kept``); ``evidence_entities_dropped`` now
  counts only the ones that carry nothing;
- an entity whose name is one of the domain's exclusion examples and that
  carries evidence is kept flagged ``properties.excluded_name``
  (``sources.excluded_entities_kept``);
- a relationship the strict type-constraint check refuses is kept flagged:
  no template for its label, kept as ``related_to`` flagged
  ``properties.unmatched_label`` (``sources.relationships_unmatched_label_kept``),
  or endpoints its template refuses, flagged ``properties.type_mismatch``
  (``sources.relationships_type_mismatch_kept``);
  ``relationships_dropped_type_unmatched`` now counts the two together
  (before, the ones the check dropped).

Purely additive, hence ``safe_auto``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# Revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ChaosCypher migration metadata — consumed by the runner's tier classifier.
# Tier values: "safe_auto" | "needs_confirmation" | "manual"
CC_TIER: str = "safe_auto"
CC_DESCRIPTION: str = "Add the kept-flagged counters (unevidenced entities, exclusions, edge types)"

COLUMNS = (
    "entities_unevidenced_kept",
    "excluded_entities_kept",
    "relationships_unmatched_label_kept",
    "relationships_type_mismatch_kept",
)


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
