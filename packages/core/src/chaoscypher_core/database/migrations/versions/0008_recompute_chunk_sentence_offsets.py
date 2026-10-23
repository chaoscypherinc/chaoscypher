# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Recompute chunk sentence_offsets after the splitter learned closing quotes.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-06 00:00:00.000000+00:00

The canonical sentence splitter now breaks after a closing quote (audit C2:
two speakers' turns were one "sentence"). Chunks stored before that keep the
old ``chunk_metadata.sentence_offsets`` (88 of the 179 chunks of the Book One
reference pack), while ``chunk_hydration`` numbers the live split: the
citation hover text, the evidence validator's stored-offset reads and the
[S] numbering a reader sees disagreed. Offsets are derived data: this
recomputes them from ``content`` with the current splitter for every
non-empty chunk, rewriting only rows that differ (deterministic and
idempotent; the shared body is ``backfills.recompute_chunk_sentence_offsets``,
the one 0005 runs; since audit wave 5 it also clears an empty chunk's offsets
and renumbers stored citations, which 0009 runs for a database already past
this one). Data migration, hence ``needs_confirmation``, as 0005.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


# Revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ChaosCypher migration metadata — consumed by the runner's tier classifier.
# Tier values: "safe_auto" | "needs_confirmation" | "manual"
CC_TIER: str = "needs_confirmation"
CC_DESCRIPTION: str = "Recompute chunk sentence_offsets with the current sentence splitter"


def upgrade() -> None:
    """Recompute ``sentence_offsets`` for every non-empty chunk with the current splitter."""
    from chaoscypher_core.database.migrations.backfills import recompute_chunk_sentence_offsets

    recompute_chunk_sentence_offsets(op.get_bind())


def downgrade() -> None:
    """No-op: the old splitter's offsets are not restorable (the recomputed ones are canonical)."""
