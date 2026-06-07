# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Atomic SQLite database backup helper.

Copies a SQLite database file to a ``<db_parent>/backups/<label>-<ts>.db``
path before a destructive operation (migration, manual reset, etc.).
Uses ``sqlite3.Connection.backup()`` so the source DB can be hot — the
online backup API serializes with WAL checkpoints and doesn't require
stopping writers.

Returns a ``BackupResult`` with the destination path and byte counts so
callers can log what happened and can offer a rollback path pointing at
``backup_path``.
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import structlog


logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class BackupResult:
    """Outcome of a successful backup_database() call."""

    source_path: Path
    backup_path: Path
    source_bytes: int
    backup_bytes: int
    timestamp: datetime


def backup_database(
    source_path: Path,
    *,
    label: str,
    backup_dir: Path | None = None,
) -> BackupResult:
    """Create a timestamped backup of a SQLite database file.

    Uses ``sqlite3.Connection.backup()`` for an atomic, online copy.
    Callers can back up a live DB (WAL mode) without stopping writers.

    Args:
        source_path: Path to the source ``.db`` file.
        label: Short identifier embedded in the backup filename, e.g.
            ``"pre-alembic-<revision>"``.
        backup_dir: Destination directory. Defaults to
            ``source_path.parent / "backups"``.

    Returns:
        BackupResult with the destination path and byte counts.

    Raises:
        FileNotFoundError: If ``source_path`` does not exist.
        sqlite3.Error: If the online backup API fails.
    """
    source_path = Path(source_path)
    if not source_path.exists():
        msg = f"Cannot back up missing database: {source_path}"
        raise FileNotFoundError(msg)

    target_dir = backup_dir if backup_dir is not None else source_path.parent / "backups"
    target_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC)
    ts_str = timestamp.strftime("%Y%m%dT%H%M%SZ")
    safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
    backup_path = target_dir / f"{safe_label}-{ts_str}.db"

    src_conn = sqlite3.connect(str(source_path))
    try:
        dst_conn = sqlite3.connect(str(backup_path))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()

    source_bytes = source_path.stat().st_size
    backup_bytes = backup_path.stat().st_size

    logger.info(
        "database_backup_created",
        source=str(source_path),
        backup=str(backup_path),
        source_bytes=source_bytes,
        backup_bytes=backup_bytes,
        label=safe_label,
    )

    return BackupResult(
        source_path=source_path,
        backup_path=backup_path,
        source_bytes=source_bytes,
        backup_bytes=backup_bytes,
        timestamp=timestamp,
    )


def latest_backup(
    source_path: Path,
    *,
    backup_dir: Path | None = None,
) -> Path | None:
    """Return the most recent backup for ``source_path``, or None.

    Resolves the same ``<db_parent>/backups/`` directory that
    ``backup_database()`` writes to and returns the lexicographically
    largest ``*.db`` file. Filenames carry a sortable ISO-8601-UTC
    timestamp (``YYYYmmddTHHMMSSZ``), so lexical order equals
    chronological order.

    Args:
        source_path: The original database path whose backups we want.
        backup_dir: Override for the backup directory.

    Returns:
        Path to the latest backup file, or None if the directory has
        no backups yet (or doesn't exist).
    """
    target_dir = backup_dir if backup_dir is not None else Path(source_path).parent / "backups"
    if not target_dir.is_dir():
        return None
    candidates = sorted(target_dir.glob("*.db"))
    return candidates[-1] if candidates else None


def free_space_ok(
    source_path: Path,
    *,
    headroom: float = 1.2,
    backup_dir: Path | None = None,
) -> bool:
    """True if the backup target has room for a full copy of ``source_path``.

    Requires free bytes >= source size * ``headroom`` (default 20% slack).
    Probes the backups dir's filesystem, walking up to the nearest existing
    parent if the backups dir doesn't exist yet.

    Args:
        source_path: Live SQLite database that would be copied.
        headroom: Multiplier on source size for required free space.
        backup_dir: Override target dir (default ``<db_parent>/backups``).

    Returns:
        True if there is enough free space, else False.
    """
    source_path = Path(source_path)
    target = backup_dir if backup_dir is not None else source_path.parent / "backups"
    probe = target
    while not probe.exists():
        probe = probe.parent
    try:
        free = shutil.disk_usage(probe).free
    except OSError:
        logger.warning("free_space_probe_failed", path=str(probe))
        return True  # don't block on an unmeasurable filesystem
    needed = int(source_path.stat().st_size * headroom)
    ok = free >= needed
    if not ok:
        logger.warning("insufficient_backup_space", free=free, needed=needed, path=str(probe))
    return ok


__all__ = ["BackupResult", "backup_database", "free_space_ok", "latest_backup"]
