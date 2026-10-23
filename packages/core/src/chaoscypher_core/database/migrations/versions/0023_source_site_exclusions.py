# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Source site exclusions: the pages a site leaves out, so a refresh keeps them out.

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-22 22:00:00.000000+00:00

One nullable JSON column on ``source_sites`` (spec 2026-10-22-site-import-design, § 8 item 18):

- ``excluded_pages``: the page identities (``page_identity``) the user left out: the pages
  unticked in the import's preview, the list a refresh passed last, and the pages deleted
  from the site since. A refresh with no list of its own keeps these out. ``NULL`` (every
  site imported before this) reads as leaving nothing out.

Purely additive.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# Revision identifiers, used by Alembic.
revision: str = "0023"
down_revision: str | Sequence[str] | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ChaosCypher migration metadata — consumed by the runner's tier classifier.
# Tier values: "safe_auto" | "needs_confirmation" | "manual"
CC_TIER: str = "safe_auto"
CC_DESCRIPTION: str = (
    "Add a nullable JSON column on source_sites: excluded_pages (the pages the user left "
    "out of a site, so a refresh keeps them out)"
)


def upgrade() -> None:
    """Add ``excluded_pages`` to ``source_sites``."""
    with op.batch_alter_table("source_sites") as batch_op:
        batch_op.add_column(sa.Column("excluded_pages", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Drop the column."""
    with op.batch_alter_table("source_sites") as batch_op:
        batch_op.drop_column("excluded_pages")
