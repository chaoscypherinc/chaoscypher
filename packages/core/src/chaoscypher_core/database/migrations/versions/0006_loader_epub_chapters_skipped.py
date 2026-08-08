# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""add loader_epub_chapters_skipped quality counter to sources

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-23 00:00:00.000000+00:00

The EPUB loader silently dropped spine items with no manifest entry and
manifest chapters missing from the zip — the only loader whose skips had
no quality counter. ``sources`` gains the ``loader_epub_chapters_skipped``
INTEGER counter column (rolled up by the indexing handler, surfaced via
``SourceResponse.quality_metrics``). Two-step add: ``server_default='0'``
backfills existing rows, then the default is dropped so the final schema
matches ``SQLModel.metadata`` (the model declares no server default).
Purely additive, hence ``safe_auto``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# Revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ChaosCypher migration metadata — consumed by the runner's tier classifier.
# Tier values: "safe_auto" | "needs_confirmation" | "manual"
CC_TIER: str = "safe_auto"
CC_DESCRIPTION: str = "Add loader_epub_chapters_skipped quality counter column to sources"


def upgrade() -> None:
    """Add the NOT NULL counter column, backfilling existing rows with 0."""
    with op.batch_alter_table("sources") as batch_op:
        batch_op.add_column(
            sa.Column(
                "loader_epub_chapters_skipped",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
    with op.batch_alter_table("sources") as batch_op:
        batch_op.alter_column("loader_epub_chapters_skipped", server_default=None)


def downgrade() -> None:
    """Drop the ``loader_epub_chapters_skipped`` column from ``sources``."""
    with op.batch_alter_table("sources") as batch_op:
        batch_op.drop_column("loader_epub_chapters_skipped")
