# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Upgrade orchestration service and DTOs.

Reads and mutates the chaoscypher_upgrade_state table, invokes Alembic
apply/rollback, and returns DTOs consumed by both the Cortex HTTP API
and the CLI ``chaoscypher db migrate`` command.

Lives in core so CLI and HTTP callers share the same code path without
either side crossing package boundaries.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import structlog
from pydantic import BaseModel, Field

from chaoscypher_core.database.backup import backup_database
from chaoscypher_core.database.engine import get_db_path
from chaoscypher_core.database.migrations.runner import (
    current_revision,
    pending_revisions,
    upgrade_to_head,
)
from chaoscypher_core.database.migrations.state import (
    clear_upgrade_state,
    get_upgrade_state,
)
from chaoscypher_core.database.migrations.tiers import MigrationTier, read_migration_info


logger = structlog.get_logger(__name__)


class PendingMigration(BaseModel):
    """One row of the pending-migration list surfaced to the UI."""

    revision: str = Field(description="Alembic revision id.")
    tier: MigrationTier = Field(description="Risk classification.")
    description: str = Field(description="Plain-language summary for the UI.")


class PendingMigrationsResponse(BaseModel):
    """Response body for GET /upgrade/pending."""

    ready: bool = Field(description="True if the app is ready to serve requests.")
    blocked_on: list[PendingMigration] = Field(
        default_factory=list,
        description="Migrations blocking the app. Empty when ready=True.",
    )
    message: str = Field(default="", description="Human-readable status message.")
    last_backup: str | None = Field(
        default=None,
        description="Path to the pre-upgrade backup, or null if no backup exists.",
    )
    last_applied: list[str] = Field(
        default_factory=list,
        description=(
            "Revisions a silent startup auto-upgrade applied. Non-empty (with "
            "data_changing=True) signals a data-changing upgrade ran without "
            "blocking; the UI surfaces a dismissible notice + rollback."
        ),
    )
    data_changing: bool = Field(
        default=False,
        description="True if a silently auto-applied migration changed data.",
    )


class ApplyResponse(BaseModel):
    """Response body for POST /upgrade/apply."""

    applied: list[str] = Field(description="Revision ids that were applied.")
    current_revision: str | None = Field(description="Revision the DB is now stamped at.")
    backup_path: str | None = Field(description="Pre-apply backup location.")


class RollbackResponse(BaseModel):
    """Response body for POST /upgrade/rollback."""

    restored_from: str = Field(description="Backup file the DB was restored from.")
    revision: str | None = Field(description="Revision after restore.")


class UpgradeService:
    """Surface the migration state and drive apply/rollback.

    Thin layer over the core migration helpers; every DB-touching
    operation delegates to functions in
    ``chaoscypher_core.database.migrations`` so the CLI and HTTP API
    share the same code path.
    """

    def __init__(self, database_name: str) -> None:
        """Bind the service to the database at ``database_name``."""
        self.db_path: Path = get_db_path(database_name)

    def pending(self) -> PendingMigrationsResponse:
        """Return upgrade state and the list of unapplied migrations."""
        state = get_upgrade_state(self.db_path)
        revisions = pending_revisions(self.db_path)
        infos = [read_migration_info(r) for r in revisions]
        return PendingMigrationsResponse(
            ready=state.ready,
            blocked_on=[
                PendingMigration(
                    revision=info.revision,
                    tier=info.tier,
                    description=info.description,
                )
                for info in infos
            ],
            message=state.message,
            last_backup=state.last_backup,
            last_applied=state.last_applied,
            data_changing=state.data_changing,
        )

    def apply(self) -> ApplyResponse:
        """Apply all pending migrations, backing up first if needed."""
        revisions = pending_revisions(self.db_path)
        state = get_upgrade_state(self.db_path)

        # If no backup exists (e.g., operator triggered apply outside
        # the startup path), take one now.
        backup_path = state.last_backup
        if backup_path is None and revisions:
            result = backup_database(self.db_path, label=f"pre-{revisions[0]}")
            backup_path = str(result.backup_path)

        upgrade_to_head(self.db_path)
        clear_upgrade_state(self.db_path)

        logger.info(
            "upgrade_applied",
            revisions=revisions,
            backup=backup_path,
        )
        return ApplyResponse(
            applied=revisions,
            current_revision=current_revision(self.db_path),
            backup_path=backup_path,
        )

    def rollback(self) -> RollbackResponse:
        """Restore the database from the pre-upgrade backup."""
        state = get_upgrade_state(self.db_path)
        if not state.last_backup:
            msg = "No backup available to roll back to."
            raise RuntimeError(msg)

        backup_path = Path(state.last_backup)
        if not backup_path.exists():
            msg = f"Backup file missing: {backup_path}"
            raise FileNotFoundError(msg)

        # The app runs app.db in WAL mode. Before overwriting it we must
        # dispose cached engines and delete the post-upgrade ``.db-wal`` /
        # ``.db-shm`` sidecars — otherwise the next connection replays those
        # stale frames onto the just-restored backup (SQLite restore
        # corruption). Mirrors ``services/backup.py`` restore.
        self._reset_database_files()

        # Copy backup over the live DB file. The upgrade gate should
        # already be keeping workers out; if not, SQLite's file lock
        # will surface any lingering writers as an error.
        shutil.copy2(backup_path, self.db_path)

        # Dispose again so subsequent connections re-open the restored file.
        self._dispose_engines("after_rollback")

        rev = current_revision(self.db_path)
        clear_upgrade_state(self.db_path)
        logger.warning(
            "upgrade_rolled_back",
            restored_from=str(backup_path),
            revision=rev,
        )
        return RollbackResponse(
            restored_from=str(backup_path),
            revision=rev,
        )

    def _reset_database_files(self) -> None:
        """Dispose cached engines and remove WAL/SHM sidecars before overwriting app.db.

        WAL mode means a leftover ``.db-wal`` would replay onto a freshly
        restored file (corruption); cached engines would otherwise keep stale
        pooled connections (and hold the sidecars open on Windows).
        """
        self._dispose_engines("before_overwrite")
        for suffix in (".db-wal", ".db-shm"):
            sidecar = self.db_path.with_suffix(suffix)
            if sidecar.exists():
                sidecar.unlink()
                logger.info("wal_file_removed_before_rollback", path=str(sidecar))

    @staticmethod
    def _dispose_engines(when: str) -> None:
        """Invalidate cached SQLAlchemy engines so new connections re-open clean."""
        try:
            from chaoscypher_core.adapters.sqlite.engine import dispose_all_engines

            dispose_all_engines()
        except Exception:
            logger.debug("engine_invalidation_skipped", when=when)


__all__ = [
    "ApplyResponse",
    "PendingMigration",
    "PendingMigrationsResponse",
    "RollbackResponse",
    "UpgradeService",
]
