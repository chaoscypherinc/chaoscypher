# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Count what the pass-1 known-entities guards take off a group's records.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-11 12:00:00.000000+00:00

The source's known entities guard each group's pass-1 records
(``known_entities.KnownEntities.guard``, spec 2026-10-11 D3):

- an alias that is the name or a given name of another known entry is dropped
  (``properties.alias_crossed_known`` holds it): ``sources.aliases_crossed_known_dropped``;
- a known name its cited sentences do not evidence is demoted to the one
  reference phrase they write (``properties.referent_unevidenced`` holds the
  name): ``sources.referents_unevidenced_demoted``. One kept as written and
  flagged is no drop and is not counted.

Purely additive, hence ``safe_auto``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# Revision identifiers, used by Alembic.
revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ChaosCypher migration metadata — consumed by the runner's tier classifier.
# Tier values: "safe_auto" | "needs_confirmation" | "manual"
CC_TIER: str = "safe_auto"
CC_DESCRIPTION: str = "Add the known-entities guard counters (crossed aliases, demoted names)"

COLUMNS = (
    "aliases_crossed_known_dropped",
    "referents_unevidenced_demoted",
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
