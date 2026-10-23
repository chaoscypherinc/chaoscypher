# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Source sites: a docs site imported as one row, each page a source that names it.

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-22 12:00:00.000000+00:00

A site import (spec 2026-10-22-site-import-design, decision 4) is a row of its own in
``source_sites``: the address the user gave, the site's title, how its pages were found, the
import's status, the page count, when it was created and last checked, and the single-URL
import options applied to every page (JSON). Each page is a source with ``sources.site_id``
naming the site: nullable, indexed, a foreign key set to NULL when the site row goes (the
pages are removed only through the source delete, on request). Not ``parent_id`` (reserved for
re-import versions) and not tags (a user can delete a tag; a site must not lose its pages).

A page the site no longer lists on a refresh is marked with ``sources.site_gone_at`` (when it
was first missed), never deleted; a page listed again clears it.

Purely additive: one table and two nullable columns.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# Revision identifiers, used by Alembic.
revision: str = "0021"
down_revision: str | Sequence[str] | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ChaosCypher migration metadata — consumed by the runner's tier classifier.
# Tier values: "safe_auto" | "needs_confirmation" | "manual"
CC_TIER: str = "safe_auto"
CC_DESCRIPTION: str = (
    "Add the source_sites table (one row per imported docs site) and two nullable columns "
    "on sources: site_id (indexed foreign key to source_sites, SET NULL on delete) and "
    "site_gone_at (a page the site no longer lists)"
)

#: Names the foreign key so the downgrade's batch copy can find and drop it.
_FK_NAMES = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}
_SITE_FK = "fk_sources_site_id_source_sites"


def upgrade() -> None:
    """Create ``source_sites``, then add ``site_id`` (with its index and key) and ``site_gone_at``."""
    op.create_table(
        "source_sites",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("database_name", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("discovery_method", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_source_sites_database_name_created_at",
        "source_sites",
        ["database_name", "created_at"],
    )
    with op.batch_alter_table("sources", naming_convention=_FK_NAMES) as batch_op:
        batch_op.add_column(sa.Column("site_id", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("site_gone_at", sa.DateTime(), nullable=True))
        batch_op.create_index("ix_sources_site_id", ["site_id"])
        batch_op.create_foreign_key(
            _SITE_FK, "source_sites", ["site_id"], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    """Drop the two columns (with the key and index), then ``source_sites``."""
    with op.batch_alter_table("sources", naming_convention=_FK_NAMES) as batch_op:
        batch_op.drop_constraint(_SITE_FK, type_="foreignkey")
        batch_op.drop_index("ix_sources_site_id")
        batch_op.drop_column("site_gone_at")
        batch_op.drop_column("site_id")
    op.drop_index("ix_source_sites_database_name_created_at", table_name="source_sites")
    op.drop_table("source_sites")
