# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Clear empty chunks' sentence_offsets and renumber stored citations' local_index.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-06 12:00:00.000000+00:00

0008 recomputed the offsets of every non-empty chunk after the splitter
changed (audit C2) but left two derived values behind (audit wave 5): a chunk
with no content kept whatever offsets it had, and every stored citation's
``citation_metadata.sentences[].local_index`` still numbered the sentence
under the old splitter, so the citation pointed at another sentence of its
chunk. The shared backfill (``backfills.recompute_chunk_sentence_offsets``,
the body 0005 and 0008 run) now does both; this runs it once more for a
database already past 0008. Deterministic and idempotent. Data migration,
hence ``needs_confirmation``, as 0005 and 0008.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


# Revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ChaosCypher migration metadata — consumed by the runner's tier classifier.
# Tier values: "safe_auto" | "needs_confirmation" | "manual"
CC_TIER: str = "needs_confirmation"
CC_DESCRIPTION: str = "Clear empty chunks' sentence offsets and renumber citation local_index"


def upgrade() -> None:
    """Run the shared offsets backfill: empty chunks cleared, citations renumbered."""
    from chaoscypher_core.database.migrations.backfills import recompute_chunk_sentence_offsets

    recompute_chunk_sentence_offsets(op.get_bind())


def downgrade() -> None:
    """No-op: the old numbering is not restorable (the recomputed one is canonical)."""
