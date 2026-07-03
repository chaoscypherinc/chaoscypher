# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Backfill chunk-local sentence_offsets in document_chunks.chunk_metadata

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-02 00:00:00.000000+00:00

Chunks indexed before the ``_shift_sentence_offsets`` fix (2026-06-20) store
``sentence_offsets`` in a broken coordinate base — chunk-local plus a
``new_start - old_start`` delta — so every consumer that slices the chunk's
own ``content[start:end]`` (chat citation hover text, the CLI citation
resolver, the source-page sentence highlight) silently produced wrong or
empty text for every non-first chunk. Offsets are derived data: this
recomputes them from ``content`` via the canonical splitter — exactly what
the chunker stores today — for every non-empty chunk, rewriting only rows
whose stored offsets differ (deterministic and idempotent). Data migration,
hence ``needs_confirmation``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ChaosCypher migration metadata — consumed by the runner's tier classifier.
# Tier values: "safe_auto" | "needs_confirmation" | "manual"
CC_TIER: str = "needs_confirmation"
CC_DESCRIPTION: str = "Recompute chunk sentence_offsets from content (repairs pre-2026-06-20 rows)"


def upgrade() -> None:
    """Recompute ``sentence_offsets`` for every non-empty chunk from its content.

    Uses the canonical sentence splitter so the stored offsets match what the
    runtime consumers slice against. Imported lazily so loading the migration
    module for lineage inspection stays cheap.
    """
    from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
        split_into_sentences_with_offsets,
    )

    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, content, chunk_metadata FROM document_chunks "
            "WHERE content IS NOT NULL AND content != ''"
        )
    ).fetchall()

    for chunk_id, content, meta_json in rows:
        try:
            meta = json.loads(meta_json) if meta_json else {}
        except (json.JSONDecodeError, TypeError):
            meta = {}
        if not isinstance(meta, dict):
            meta = {}

        new_offsets = split_into_sentences_with_offsets(content)
        if meta.get("sentence_offsets") == new_offsets:
            continue

        meta["sentence_offsets"] = new_offsets
        conn.execute(
            sa.text("UPDATE document_chunks SET chunk_metadata = :meta WHERE id = :id"),
            {"meta": json.dumps(meta), "id": chunk_id},
        )


def downgrade() -> None:
    """No-op: the pre-0005 corrupted offsets are not restorable (and the
    recomputed values are the canonical ones every consumer expects)."""
