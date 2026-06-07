# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Migration rollback must clean up WAL/SHM sidecars before overwriting app.db.

The app runs SQLite in WAL mode. ``UpgradeService.rollback`` restored the
pre-upgrade backup with a bare ``shutil.copy2`` over the live ``app.db``
while leaving the post-upgrade ``app.db-wal`` / ``app.db-shm`` sidecars in
place. The next connection replays those stale post-upgrade WAL frames onto
the just-restored pre-upgrade file — classic SQLite restore corruption.

The fix mirrors ``services/backup.py`` restore: dispose cached engines and
unlink the ``.db-wal`` / ``.db-shm`` sidecars before (and dispose again
after) copying the backup over the live database.
"""

from __future__ import annotations

from pathlib import Path

from chaoscypher_core.database.migrations.runner import upgrade_to_head
from chaoscypher_core.database.migrations.state import set_upgrade_state
from chaoscypher_core.database.migrations.upgrade import UpgradeService


def _service_for(db_path: Path) -> UpgradeService:
    """Build an UpgradeService bound to ``db_path`` without the data-dir lookup."""
    svc = UpgradeService.__new__(UpgradeService)
    svc.db_path = db_path
    return svc


def test_reset_database_files_removes_wal_and_shm_sidecars(tmp_path: Path) -> None:
    """The pre-overwrite reset deletes stale WAL/SHM sidecars deterministically."""
    db = tmp_path / "app.db"
    db.write_bytes(b"main db")
    wal = db.with_suffix(".db-wal")
    shm = db.with_suffix(".db-shm")
    wal.write_bytes(b"stale wal frames")
    shm.write_bytes(b"stale shm")

    _service_for(db)._reset_database_files()

    assert not wal.exists(), "stale .db-wal must be removed before overwrite"
    assert not shm.exists(), "stale .db-shm must be removed before overwrite"


def test_rollback_restores_backup_and_clears_stale_wal(tmp_path: Path) -> None:
    """End-to-end: rollback restores the backup revision without WAL corruption."""
    db = tmp_path / "app.db"
    upgrade_to_head(db)

    # A self-contained, checkpointed backup at head (mirrors backup_database output).
    backup = tmp_path / "pre-upgrade.db"
    upgrade_to_head(backup)

    set_upgrade_state(
        db,
        ready=False,
        blocked_on=[],
        last_backup=str(backup),
        message="rolled back",
    )

    # upgrade_to_head + set_upgrade_state leave real WAL/SHM sidecars on the
    # live DB; rollback must clear them before restoring the backup.
    resp = _service_for(db).rollback()

    assert resp.restored_from == str(backup)
    # The restored DB opens cleanly (no stale-WAL corruption) and reports a revision.
    assert resp.revision is not None
