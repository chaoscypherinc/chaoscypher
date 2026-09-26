# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Add start_time / end_time media timestamps to document_chunks.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-21 00:00:00.000000+00:00

Chunks of transcribed audio and video now record where in the recording
they came from — ``start_time`` / ``end_time`` in seconds — so a citation
can point at 12:34 of an episode the way a PDF citation points at page 7.
Both columns are nullable REAL and NULL for every other loader. Purely
additive, hence ``safe_auto``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# Revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ChaosCypher migration metadata — consumed by the runner's tier classifier.
# Tier values: "safe_auto" | "needs_confirmation" | "manual"
CC_TIER: str = "safe_auto"
CC_DESCRIPTION: str = "Add start_time / end_time media timestamp columns to document_chunks"


def upgrade() -> None:
    """Add the two nullable media-timestamp columns."""
    with op.batch_alter_table("document_chunks") as batch_op:
        batch_op.add_column(sa.Column("start_time", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("end_time", sa.Float(), nullable=True))


def downgrade() -> None:
    """Drop the media-timestamp columns from ``document_chunks``."""
    with op.batch_alter_table("document_chunks") as batch_op:
        batch_op.drop_column("end_time")
        batch_op.drop_column("start_time")
