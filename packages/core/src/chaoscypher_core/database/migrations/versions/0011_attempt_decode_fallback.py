# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Keep the decode fallback in the attempt history; count groups the ladder rescued.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-06 18:00:00.000000+00:00

0010 recorded the decoding a retried extraction group asked with on the chunk
task and on its LLM call metrics, but a rerun snapshots the task into
``chunk_extraction_attempts`` (the history the UI shows) without it:
``chunk_extraction_attempts.decode_fallback`` (JSON, NULL for a pinned try)
keeps it. ``sources.llm_chunks_rescued_by_fallback`` counts the groups whose
kept output came from a rung of the ladder, so a source page can say how many
were rescued. Purely additive, hence ``safe_auto``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# Revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ChaosCypher migration metadata — consumed by the runner's tier classifier.
# Tier values: "safe_auto" | "needs_confirmation" | "manual"
CC_TIER: str = "safe_auto"
CC_DESCRIPTION: str = "Add decode_fallback to chunk attempts; rescued-by-fallback counter"


def upgrade() -> None:
    """Add the nullable JSON column and the counter column."""
    with op.batch_alter_table("chunk_extraction_attempts") as batch_op:
        batch_op.add_column(sa.Column("decode_fallback", sa.JSON(), nullable=True))
    # Existing rows start at 0; the column then carries no server default, as every
    # other counter column (the model's default fills new rows).
    with op.batch_alter_table("sources") as batch_op:
        batch_op.add_column(
            sa.Column(
                "llm_chunks_rescued_by_fallback",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
    with op.batch_alter_table("sources") as batch_op:
        batch_op.alter_column("llm_chunks_rescued_by_fallback", server_default=None)


def downgrade() -> None:
    """Drop the two columns."""
    with op.batch_alter_table("sources") as batch_op:
        batch_op.drop_column("llm_chunks_rescued_by_fallback")
    with op.batch_alter_table("chunk_extraction_attempts") as batch_op:
        batch_op.drop_column("decode_fallback")
