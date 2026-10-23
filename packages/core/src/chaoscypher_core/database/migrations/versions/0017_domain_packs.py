# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""A source and an extraction job carry a list of packs.

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-08 12:00:00.000000+00:00

Additive domains (spec 2026-10-07 § 5): every extraction gets the floor and
a source chooses the packs that add to it, so the single domain name becomes
a JSON list:

- ``sources.forced_packs`` (None = detect, ``[]`` = the floor alone, chosen)
  and ``sources.extraction_packs`` (the packs it was extracted with);
- ``chunk_extraction_jobs.forced_packs`` and ``.detected_packs``.

The old single-name columns stay for one release and are written from the
lists (``domains.packs.pack_columns``): ``forced_domain`` and
``detected_domain`` hold the first pack or ``generic``, ``extraction_domain``
the packs' label (``historical+literary``).

Backfill: ``extraction_packs`` from ``extraction_domain`` (``[domain]``, or
``[]`` for ``generic`` or none) and ``forced_packs`` from ``forced_domain``
(``[domain]``, ``[]`` for ``generic``, NULL for none or an auto token
``__auto__``/``auto`` a client may have stored raw, which is "detect").
Jobs are not backfilled: a job row is read through ``job_packs``, which falls
back to the old columns. Purely additive, hence ``safe_auto``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# Revision identifiers, used by Alembic.
revision: str = "0017"
down_revision: str | Sequence[str] | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ChaosCypher migration metadata — consumed by the runner's tier classifier.
# Tier values: "safe_auto" | "needs_confirmation" | "manual"
CC_TIER: str = "safe_auto"
CC_DESCRIPTION: str = "Add the pack lists to sources and extraction jobs (backfilled on sources)"

COLUMNS: dict[str, tuple[str, ...]] = {
    "sources": ("forced_packs", "extraction_packs"),
    "chunk_extraction_jobs": ("forced_packs", "detected_packs"),
}
"""The JSON list columns this migration adds, by table."""

FLOOR_NAME = "generic"
"""The floor's name: a stored ``generic`` is the floor alone, no pack."""

AUTO_TOKENS = frozenset({"__auto__", "auto"})
"""Every spelling of "detect" a client may have stored raw as ``forced_domain`` (the UI's sentinel, the CLI's choice)."""


def packs_of_name(name: str | None) -> list[str]:
    """The packs one stored domain name means: ``[]`` for none, the floor or an auto token, else its names in order."""
    if not name or name.strip() in AUTO_TOKENS:
        return []
    packs: dict[str, None] = {}
    for part in name.split("+"):
        if part.strip() and part.strip() != FLOOR_NAME:
            packs.setdefault(part.strip(), None)
    return list(packs)


def forced_of_name(name: str | None) -> list[str] | None:
    """The forced packs a stored ``forced_domain`` means: None (detect) for none or an auto token."""
    if not name or name.strip() in AUTO_TOKENS:
        return None
    return packs_of_name(name)


def upgrade() -> None:
    """Add the four nullable JSON columns, then backfill the source lists from the old names."""
    for table, names in COLUMNS.items():
        with op.batch_alter_table(table) as batch_op:
            for name in names:
                batch_op.add_column(sa.Column(name, sa.JSON(), nullable=True))

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, forced_domain, extraction_domain FROM sources")).all()
    for source_id, forced_domain, extraction_domain in rows:
        forced_packs = forced_of_name(forced_domain)
        forced = json.dumps(forced_packs) if forced_packs is not None else None
        bind.execute(
            sa.text(
                "UPDATE sources SET extraction_packs = :extraction, forced_packs = :forced "
                "WHERE id = :id"
            ),
            {
                "extraction": json.dumps(packs_of_name(extraction_domain)),
                "forced": forced,
                "id": source_id,
            },
        )


def downgrade() -> None:
    """Drop the four columns (the old single-name columns still hold the first pack or the label)."""
    for table, names in COLUMNS.items():
        with op.batch_alter_table(table) as batch_op:
            for name in names:
                batch_op.drop_column(name)
