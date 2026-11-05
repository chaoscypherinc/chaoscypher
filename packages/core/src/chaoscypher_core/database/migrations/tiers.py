# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tier metadata extracted from Alembic migration modules.

Each migration file declares two module-level constants:

- ``CC_TIER``: ``"safe_auto"`` | ``"needs_confirmation"`` | ``"manual"``
- ``CC_DESCRIPTION``: human-readable one-liner surfaced to operators

The startup runner reads these via :func:`read_migration_info` and
routes migrations accordingly (``startup.py``): ``safe_auto`` and
``needs_confirmation`` apply behind a verified backup; ``manual`` waits in
maintenance mode unless ``auto_apply_destructive`` is on.

A migration may also hold itself when the data needs it: a module that defines
``cc_hold_preview(conn: sqlalchemy.Connection) -> list[str]`` is previewed before
it applies (:func:`hold_preview`). The preview only reads, against the schema as
it stands before the migration, and returns one plain line per change the
migration would make to user-visible data. A non-empty preview holds that
migration (and everything after it) for the owner's confirmation **whatever
``auto_apply_destructive`` says**, stronger than the ``manual`` gate; an empty
one lets it apply as its tier says.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

import structlog
from alembic.config import Config
from alembic.script import ScriptDirectory


if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType


logger = structlog.get_logger(__name__)


class MigrationTier(StrEnum):
    """Risk classification for a single migration.

    * ``safe_auto`` — auto-apply at startup, no user intervention. Use
      for additive changes, idempotent data backfills, and dedup of
      junction-table duplicates where discarded rows carry no
      user-meaningful information.
    * ``needs_confirmation`` — loses user-meaningful data (e.g., merging
      duplicate named entities) or has a destructive repair step the user
      should be able to acknowledge. These auto-apply at startup behind a
      verified backup (recorded as a data-changing upgrade so they can be
      rolled back), whatever ``auto_apply_destructive`` says: of the tiers,
      the startup runner (``startup._plan_apply``) gates only ``manual``.
      With no room for the backup nothing applies and the database waits
      in maintenance mode.
    * ``manual`` — the most destructive class (e.g., dropping a column,
      forcing re-extraction). With the default ``auto_apply_destructive``
      setting these also auto-apply behind a verified backup; when an
      operator opts out, they gate to the maintenance-mode UI (or
      ``chaoscypher db migrate apply``) for explicit confirmation.

    Whatever the tier, a migration whose ``cc_hold_preview`` lists changes holds for
    confirmation (see the module docstring and :func:`hold_preview`).
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
    has_hold: bool = False
    """True when the module defines ``cc_hold_preview`` (it may hold for confirmation)."""


def _migration_module(revision: str) -> ModuleType:
    """Load the migration module at ``revision`` from the script directory.

    Raises:
        ValueError: If the revision id isn't known to the script directory.
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
    return rev.module


def read_migration_info(revision: str) -> MigrationInfo:
    """Load the migration module at ``revision`` and extract CC_* metadata.

    Defaults: ``tier = SAFE_AUTO``, ``description = ""`` if the migration
    didn't declare them (treat unannotated legacy migrations as safe —
    every migration template in this tree ships with the defaults set,
    so missing annotations indicate hand-written migrations that skipped
    the template). ``has_hold`` is True when the module defines a callable
    ``cc_hold_preview``.

    Args:
        revision: Alembic revision id.

    Returns:
        MigrationInfo for that revision.

    Raises:
        ValueError: If the revision id isn't known to the script directory
            or its CC_TIER is not one of the valid MigrationTier values.
    """
    module = _migration_module(revision)
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

    return MigrationInfo(
        revision=revision,
        tier=tier,
        description=description,
        has_hold=callable(getattr(module, "cc_hold_preview", None)),
    )


def hold_preview(revision: str, db_path: Path) -> list[str]:
    """The changes migration ``revision`` would make to the data at ``db_path``, one line each.

    Runs the module's ``cc_hold_preview`` on a connection to the database as it stands
    (before the migration). ``[]`` when the module has no preview, when the database has
    no schema yet (a first build: no data, so nothing to hold, as ``build_if_new``), or
    when the preview finds nothing to change; a non-empty list holds the migration for
    confirmation.

    Raises:
        ValueError: If the revision id isn't known to the script directory.
        Exception: Whatever the preview raises; :func:`hold_lines` turns it into a line.
    """
    preview = getattr(_migration_module(revision), "cc_hold_preview", None)
    if not callable(preview):
        return []
    from chaoscypher_core.adapters.sqlite.engine import get_engine  # local — adapters load late
    from chaoscypher_core.database.migrations.runner import is_first_build  # local — cycle

    if is_first_build(db_path):
        return []
    with get_engine(db_path).connect() as conn:
        return list(preview(conn))


def preview_error(exc: BaseException) -> str:
    """A failed preview's error in one plain line: never SQL text or a traceback."""
    from chaoscypher_core.database.errors import describe_database_error

    return describe_database_error(exc) or " ".join(str(exc).split()) or type(exc).__name__


def hold_lines(info: MigrationInfo, db_path: Path) -> list[str]:
    """What a surface shows for ``info``'s hold: its preview's lines, or why there are none.

    ``[]`` for a migration without a preview. A preview that fails becomes the single
    line ``Could not list the changes: <error>``, so the upgrade page, ``db migrate``
    and MCP still render the migration and still ask before applying it.
    """
    if not info.has_hold:
        return []
    try:
        return hold_preview(info.revision, db_path)
    except Exception as exc:  # a failing preview is shown, never raised to the surface
        logger.warning(
            "migration_hold_preview_failed",
            revision=info.revision,
            error_type=type(exc).__name__,
        )
        return [f"Could not list the changes: {preview_error(exc)}"]


__all__ = [
    "MigrationInfo",
    "MigrationTier",
    "hold_lines",
    "hold_preview",
    "preview_error",
    "read_migration_info",
]
