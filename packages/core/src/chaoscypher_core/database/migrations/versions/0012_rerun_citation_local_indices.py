# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Re-run the shared offsets and ``local_index`` backfill under its audit-wave-6 rule.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-06 22:00:00.000000+00:00

0009 shipped (audit wave 5) renumbering every stored citation's
``citation_metadata.sentences[].local_index`` from the first occurrence of
its text in the chunk (``str.find``); audit wave 6 changed the shared body
(``backfills.recompute_chunk_sentence_offsets``) to place a citation by its
own index under the offsets it was numbered against, so a sentence the chunk
repeats keeps its own number. A database that ran the wave-6 0009 is already
canonical; this brings every other database onto the same rule by running
the shared body once more (it is called, never copied).

What it cannot do: a database that ran the wave-5 0009 has had every copy of a
repeated sentence renumbered to the first copy, and the stored rows no longer
say which copy a citation meant. That numbering is a fixed point of the
wave-6 rule (the first copy's span holds the text), so it stays as it is;
recovering it would need the extraction group's ``sent_ref`` resolution,
which the citation rows do not record a group for. Deterministic and
idempotent: a row that already matches is not written. Data migration, hence
``needs_confirmation``, as 0005, 0008 and 0009.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


# Revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ChaosCypher migration metadata — consumed by the runner's tier classifier.
# Tier values: "safe_auto" | "needs_confirmation" | "manual"
CC_TIER: str = "needs_confirmation"
CC_DESCRIPTION: str = "Re-run the citation local_index renumbering under its own-place rule"


def upgrade() -> None:
    """Run the shared offsets backfill (offsets recomputed, citations renumbered by their own place)."""
    from chaoscypher_core.database.migrations.backfills import recompute_chunk_sentence_offsets

    recompute_chunk_sentence_offsets(op.get_bind())


def downgrade() -> None:
    """No-op: the earlier numbering is not restorable (the recomputed one is canonical)."""
