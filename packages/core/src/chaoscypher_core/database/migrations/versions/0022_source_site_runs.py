# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Source site runs: when a site's run started, and how far through its pages it got.

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-22 18:00:00.000000+00:00

Two nullable columns on ``source_sites`` (spec 2026-10-22-site-import-design, § 8 fixes):

- ``run_started_at``: when the site's current run was queued or started. A run that has
  been "queued" or "importing" for longer than the operations worker's timeout is stale
  (its worker was killed), so a refresh or a delete is no longer refused because of it.
- ``run_cursor``: how many of the discovered pages the open pass has handled, so a run cut
  off part-way (the operations timeout, a crash, a continuation of a long site) resumes
  there instead of fetching every page again. ``NULL`` when no pass is open.

Purely additive.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# Revision identifiers, used by Alembic.
revision: str = "0022"
down_revision: str | Sequence[str] | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ChaosCypher migration metadata — consumed by the runner's tier classifier.
# Tier values: "safe_auto" | "needs_confirmation" | "manual"
CC_TIER: str = "safe_auto"
CC_DESCRIPTION: str = (
    "Add two nullable columns on source_sites: run_started_at (when the site's run was "
    "queued or started, so a killed run goes stale) and run_cursor (how far the open "
    "pass got, so a cut-off run resumes)"
)


def upgrade() -> None:
    """Add ``run_started_at`` and ``run_cursor`` to ``source_sites``."""
    with op.batch_alter_table("source_sites") as batch_op:
        batch_op.add_column(sa.Column("run_started_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("run_cursor", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Drop the two columns."""
    with op.batch_alter_table("source_sites") as batch_op:
        batch_op.drop_column("run_cursor")
        batch_op.drop_column("run_started_at")
