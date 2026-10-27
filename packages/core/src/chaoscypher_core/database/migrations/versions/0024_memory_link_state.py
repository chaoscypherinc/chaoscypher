# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Memory link state: what happened to a memory's link into the graph, when, and who links it now.

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-24 12:00:00.000000+00:00

Before 0.6.0 a memory whose link failed, timed out or found no model left the same row
behind (``indexed``, ``commit_complete`` false), and nothing read it, so such a memory
stayed unlinked for good (spec 2026-10-24, D11; the trader's two memories). Three nullable
columns on ``sources``, set on memory rows only (``source_type = 'memory'``):

- ``memory_link_state``: one of ``MemoryLinkState`` (pending, linking, no_model, failed,
  linked, linked_empty);
- ``memory_linked_at``: when it became linked or linked_empty;
- ``memory_link_claim``: the token of the process linking it now (its heartbeat is the
  existing ``last_activity_at``), NULL when nobody is.

The upgrade sets ``linked`` on committed memories that attest a node, ``linked_empty`` on
committed memories that attest none, and ``pending`` on every other memory, so a memory
left unlinked before 0.6.0 is linked too. "Committed" is ``commit_complete = 1`` or
``processing_status = 'committed'``: a memory imported from a pack before 0.6.0 is written
``committed`` with ``commit_complete`` 0 and no ``commit_completed_at``, and must not be
extracted again. ``memory_linked_at`` is the first of ``commit_completed_at``,
``updated_at``, ``created_at`` that is set. Documents keep all three NULL. It writes row
values, so ``needs_confirmation``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# Revision identifiers, used by Alembic.
revision: str = "0024"
down_revision: str | Sequence[str] | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ChaosCypher migration metadata — consumed by the runner's tier classifier.
# Tier values: "safe_auto" | "needs_confirmation" | "manual"
CC_TIER: str = "needs_confirmation"
CC_DESCRIPTION: str = (
    "Add the memory link state to sources (memory_link_state, memory_linked_at, "
    "memory_link_claim) and set it on every memory: linked, linked_empty or pending"
)

_ATTESTS = "EXISTS (SELECT 1 FROM graph_node_sources m WHERE m.source_id = sources.id)"
_COMMITTED = "(commit_complete = 1 OR processing_status = 'committed')"
_LINKED_AT = "COALESCE(commit_completed_at, updated_at, created_at)"


def upgrade() -> None:
    """Add the three nullable columns, then set the state of every memory row."""
    op.add_column("sources", sa.Column("memory_link_state", sa.String(), nullable=True))
    op.add_column("sources", sa.Column("memory_linked_at", sa.DateTime(), nullable=True))
    op.add_column("sources", sa.Column("memory_link_claim", sa.String(), nullable=True))
    op.execute(
        f"UPDATE sources SET memory_link_state = 'linked', memory_linked_at = {_LINKED_AT} "
        f"WHERE source_type = 'memory' AND {_COMMITTED} AND {_ATTESTS}"
    )
    op.execute(
        f"UPDATE sources SET memory_link_state = 'linked_empty', memory_linked_at = {_LINKED_AT} "
        f"WHERE source_type = 'memory' AND {_COMMITTED} AND NOT {_ATTESTS}"
    )
    op.execute(
        "UPDATE sources SET memory_link_state = 'pending' "
        "WHERE source_type = 'memory' AND memory_link_state IS NULL"
    )


def downgrade() -> None:
    """Drop the three columns."""
    with op.batch_alter_table("sources") as batch_op:
        batch_op.drop_column("memory_link_claim")
        batch_op.drop_column("memory_linked_at")
        batch_op.drop_column("memory_link_state")
