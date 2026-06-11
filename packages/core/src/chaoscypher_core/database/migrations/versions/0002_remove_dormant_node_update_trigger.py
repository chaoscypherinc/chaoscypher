# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""remove dormant node.update auto-embed trigger

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-11 00:00:00.000000+00:00

Data-only migration. Seeding used to create a second auto-embed trigger,
``system_trigger_auto_embed_update_v1`` on ``node.update`` — an event no
code path ever publishes (Cortex's ``update_node`` re-embeds synchronously
instead), so the trigger never fired and only misled operators. The seed
no longer creates it; this migration deletes the inert row from existing
databases.

The DELETE is scoped to the seeded id AND event source so a user-created
trigger that happens to listen on ``node.update`` is never touched.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ChaosCypher migration metadata — consumed by the runner's tier classifier.
# Tier values: "safe_auto" | "needs_confirmation" | "manual"
CC_TIER: str = "safe_auto"
CC_DESCRIPTION: str = "Delete the dormant node.update auto-embed system trigger"


def upgrade() -> None:
    """Delete the dormant node.update auto-embed system trigger row."""
    op.execute(
        sa.text(
            "DELETE FROM triggers "
            "WHERE id = 'system_trigger_auto_embed_update_v1' "
            "AND event_source = 'node.update'"
        )
    )


def downgrade() -> None:
    """Intentional no-op.

    The deleted row was inert (its event never fires), carried no user
    data, and is not re-seeded by older code on startup, so there is
    nothing meaningful to restore.
    """
