# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests for ``BackupService.restore_backup`` WAL handling.

Two defects, one ordering bug (hunt queue 2026-07-31):

* The safety backup was a plain ``shutil.copy2`` of ``app.db`` taken while
  the live ``-wal`` sidecar still held every recently committed transaction —
  and that sidecar was then deleted, so the promised undo copy silently lost
  the WAL delta.
* Both destructive filesystem steps (sidecar unlink, ``app.db`` overwrite)
  ran *before* ``dispose_all_engines()``, under a process that still held
  pooled WAL-mode connections on the same inode — the SQLite
  restore-corruption case ``UpgradeService.rollback`` already guards against
  (dispose -> unlink -> copy -> dispose).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from chaoscypher_core.services import backup as backup_module
from chaoscypher_core.services.backup import BackupService


def _make_wal_db(db_path: Path) -> sqlite3.Connection:
    """Create a WAL-mode database with one committed row; keep the conn open.

    The open connection prevents the close-time auto-checkpoint, so later
    commits stay WAL-resident — exactly the state a live server is in.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (val TEXT)")
    conn.execute("INSERT INTO t VALUES ('pre-backup')")
    conn.commit()
    return conn


def _rows(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {row[0] for row in conn.execute("SELECT val FROM t")}
    finally:
        conn.close()


def test_safety_backup_captures_wal_resident_transactions(tmp_path: Path) -> None:
    """The pre-restore safety copy must include commits still in the WAL."""
    service = BackupService(str(tmp_path))
    db_path = tmp_path / "databases" / "db1" / "app.db"
    holder = _make_wal_db(db_path)
    try:
        created = service.create_backup("db1")

        # Committed after the backup; with the holder connection open this
        # row lives only in app.db-wal, never in app.db itself.
        holder.execute("INSERT INTO t VALUES ('wal-only')")
        holder.commit()
        assert (tmp_path / "databases" / "db1" / "app.db-wal").stat().st_size > 0

        service.restore_backup("db1", created["filename"])

        safety = db_path.with_suffix(".db.pre_restore")
        assert safety.exists()
        # Pre-fix: a WAL-blind copy2 lost 'wal-only' (and the WAL was then
        # unlinked, destroying the only copy of that transaction).
        assert _rows(safety) == {"pre-backup", "wal-only"}
        # The restored live DB is the older snapshot, as requested. Assert
        # while the holder is still open: its close-time checkpoint replays
        # the orphaned WAL onto the restored inode — the exact corruption
        # dispose-before-overwrite exists to prevent for pooled connections,
        # and a raw out-of-pool connection like this holder is beyond any
        # in-process fix's reach.
        assert _rows(db_path) == {"pre-backup"}
    finally:
        holder.close()


def test_engines_disposed_before_db_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dispose must run before the app.db overwrite, and again after it."""
    service = BackupService(str(tmp_path))
    db_path = tmp_path / "databases" / "db1" / "app.db"
    holder = _make_wal_db(db_path)
    try:
        created = service.create_backup("db1")
    finally:
        holder.close()

    events: list[str] = []

    from chaoscypher_core.adapters.sqlite import engine as engine_module

    def record_dispose() -> None:
        events.append("dispose")

    real_copy2 = backup_module.shutil.copy2

    def record_copy2(src: str, dst: str) -> object:
        events.append("copy")
        return real_copy2(src, dst)

    monkeypatch.setattr(engine_module, "dispose_all_engines", record_dispose)
    monkeypatch.setattr(backup_module.shutil, "copy2", record_copy2)

    service.restore_backup("db1", created["filename"])

    # The safety copy is VACUUM INTO (not copy2), so the only copy2 call is
    # the destructive overwrite — it must be bracketed by disposals.
    assert events == ["dispose", "copy", "dispose"]
