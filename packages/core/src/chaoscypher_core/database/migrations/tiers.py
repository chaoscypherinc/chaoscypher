# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tier metadata extracted from Alembic migration modules.

Each migration file declares two module-level constants:

- ``CC_TIER``: ``"safe_auto"`` | ``"needs_confirmation"`` | ``"manual"``
- ``CC_DESCRIPTION``: human-readable one-liner surfaced to operators

The startup runner reads these via :func:`read_migration_info` and
routes migrations accordingly — tier-1 auto-applies silently, tier-2
stops the worker and prompts the operator.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from alembic.config import Config
from alembic.script import ScriptDirectory


class MigrationTier(StrEnum):
    """Risk classification for a single migration.

    * ``safe_auto`` — auto-apply at startup, no user intervention. Use
      for additive changes, idempotent data backfills, and dedup of
      junction-table duplicates where discarded rows carry no
      user-meaningful information.
    * ``needs_confirmation`` — loses user-meaningful data (e.g., merging
      duplicate named entities) or has a destructive repair step the user
      should be able to acknowledge. With the default
      ``auto_apply_destructive`` setting these auto-apply at startup behind
      a verified backup (recorded so they can be rolled back); when an
      operator opts out, they surface through the maintenance-mode UI with
      a plain-language summary and an Apply button.
    * ``manual`` — the most destructive class (e.g., dropping a column,
      forcing re-extraction). With the default ``auto_apply_destructive``
      setting these also auto-apply behind a verified backup; when an
      operator opts out, they gate to the maintenance-mode UI (or
      ``chaoscypher db migrate apply``) for explicit confirmation.
    """

    SAFE_AUTO = "safe_auto"
    NEEDS_CONFIRMATION = "needs_confirmation"
    MANUAL = "manual"


@dataclass(frozen=True)
class MigrationInfo:
    """Tier-aware view of a migration module."""

    revision: str
    tier: MigrationTier
    description: str


def read_migration_info(revision: str) -> MigrationInfo:
    """Load the migration module at ``revision`` and extract CC_* metadata.

    Defaults: ``tier = SAFE_AUTO``, ``description = ""`` if the migration
    didn't declare them (treat unannotated legacy migrations as safe —
    every migration template in this tree ships with the defaults set,
    so missing annotations indicate hand-written migrations that skipped
    the template).

    Args:
        revision: Alembic revision id.

    Returns:
        MigrationInfo for that revision.

    Raises:
        ValueError: If the revision id isn't known to the script directory
            or its CC_TIER is not one of the valid MigrationTier values.
    """
    from alembic.util.exc import CommandError

    from chaoscypher_core.database.migrations.runner import _config_path  # local — cycle

    cfg = Config(str(_config_path()))
    script = ScriptDirectory.from_config(cfg)
    try:
        rev = script.get_revision(revision)
    except CommandError as e:
        # Alembic raises CommandError for unknown revisions rather than
        # returning None. Re-raise as ValueError so callers get a
        # stable contract.
        msg = f"Unknown revision: {revision!r}"
        raise ValueError(msg) from e
    if rev is None:
        msg = f"Unknown revision: {revision!r}"
        raise ValueError(msg)

    module = rev.module
    tier_raw = getattr(module, "CC_TIER", "safe_auto")
    description = getattr(module, "CC_DESCRIPTION", "")

    try:
        tier = MigrationTier(tier_raw)
    except ValueError as e:
        msg = (
            f"Migration {revision} declares invalid CC_TIER={tier_raw!r}; "
            f"must be one of {[t.value for t in MigrationTier]}"
        )
        raise ValueError(msg) from e

    return MigrationInfo(revision=revision, tier=tier, description=description)


__all__ = ["MigrationInfo", "MigrationTier", "read_migration_info"]
