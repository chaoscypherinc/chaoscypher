# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Record extraction decode fallbacks and count zero-yield groups.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-06 13:00:00.000000+00:00

A retried extraction group now asks with another decoding than the pinned
one (``chunk_failures.decode_fallback``: the next seed, then a small
temperature; audit wave 5), and records it, so a rescued chunk is never
mistaken for a pinned one: ``chunk_extraction_tasks.decode_fallback`` and
``llm_call_metrics.decode_fallback`` (JSON ``{seed, temperature}``, NULL for a
first try). ``sources.llm_chunks_zero_yield`` counts groups whose model
produced tokens but kept no entity and no relationship. Purely additive,
hence ``safe_auto``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# Revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ChaosCypher migration metadata — consumed by the runner's tier classifier.
# Tier values: "safe_auto" | "needs_confirmation" | "manual"
CC_TIER: str = "safe_auto"
CC_DESCRIPTION: str = "Add decode_fallback to chunk tasks and LLM call metrics; zero-yield counter"


def upgrade() -> None:
    """Add the two nullable JSON columns and the counter column."""
    with op.batch_alter_table("chunk_extraction_tasks") as batch_op:
        batch_op.add_column(sa.Column("decode_fallback", sa.JSON(), nullable=True))
    with op.batch_alter_table("llm_call_metrics") as batch_op:
        batch_op.add_column(sa.Column("decode_fallback", sa.JSON(), nullable=True))
    # Existing rows start at 0; the column then carries no server default, as every
    # other counter column (the model's default fills new rows).
    with op.batch_alter_table("sources") as batch_op:
        batch_op.add_column(
            sa.Column("llm_chunks_zero_yield", sa.Integer(), nullable=False, server_default="0")
        )
    with op.batch_alter_table("sources") as batch_op:
        batch_op.alter_column("llm_chunks_zero_yield", server_default=None)


def downgrade() -> None:
    """Drop the three columns."""
    with op.batch_alter_table("sources") as batch_op:
        batch_op.drop_column("llm_chunks_zero_yield")
    with op.batch_alter_table("llm_call_metrics") as batch_op:
        batch_op.drop_column("decode_fallback")
    with op.batch_alter_table("chunk_extraction_tasks") as batch_op:
        batch_op.drop_column("decode_fallback")
