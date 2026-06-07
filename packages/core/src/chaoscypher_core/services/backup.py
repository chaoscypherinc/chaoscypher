# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Database backup and restore service."""

import re
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from chaoscypher_core.exceptions import NotFoundError, OperationError, ValidationError
from chaoscypher_core.utils.disk import check_disk_space


logger = structlog.get_logger(__name__)


class BackupService:
    """Manages SQLite database backups and restores."""

    def __init__(self, data_dir: str, backup_subdir: str = "backups") -> None:
        """Initialize backup service.

        Args:
            data_dir: Root data directory containing databases/
            backup_subdir: Subdirectory name for backups under data_dir

        """
        self._data_dir = Path(data_dir)
        self._backup_dir = self._data_dir / backup_subdir

    @staticmethod
    def _validate_database_name(database_name: str) -> None:
        """Validate database name contains only safe characters.

        Args:
            database_name: Name to validate.

        Raises:
            ValidationError: If the name contains unsafe characters.

        """
        if not re.fullmatch(r"[A-Za-z0-9_-]+", database_name):
            msg = f"Invalid database name: {database_name!r}"
            raise ValidationError(msg, field="database_name")

    def create_backup(self, database_name: str) -> dict[str, Any]:
        """Create a backup of the specified database using VACUUM INTO.

        VACUUM INTO creates a clean, compacted copy without blocking writers.

        Args:
            database_name: Name of the database to back up.

        Returns:
            Dict with backup metadata (database, filename, size, created_at).

        Raises:
            ValidationError: If ``database_name`` contains unsafe characters
                or the resolved backup path escapes the expected directory.
            NotFoundError: If the database does not exist.

        """
        self._validate_database_name(database_name)
        db_path = self._data_dir / "databases" / database_name / "app.db"
        if not db_path.exists():
            raise NotFoundError("Database", database_name)

        backup_dir = self._backup_dir / database_name
        backup_dir.mkdir(parents=True, exist_ok=True)

        # Ensure enough disk space for the backup (at least the size of the DB + 100MB headroom)
        db_size = db_path.stat().st_size
        check_disk_space(backup_dir, min_bytes=db_size + 100 * 1024 * 1024)

        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        backup_filename = f"app_{timestamp}.db"
        backup_path = backup_dir / backup_filename

        # Validate the resolved path stays within the expected backup directory
        resolved = backup_path.resolve()
        if not resolved.is_relative_to(backup_dir.resolve()):
            raise ValidationError("Invalid backup path", field="backup_path")

        conn = sqlite3.connect(str(db_path))
        try:
            # VACUUM INTO does not support parameterized queries, so we use
            # the resolved path which has been validated above. The database_name
            # is allowlisted to alphanumeric + hyphen + underscore characters only.
            # Double single quotes to escape them in the SQL string literal.
            quoted = str(resolved).replace("'", "''")
            conn.execute(f"VACUUM INTO '{quoted}'")
        finally:
            conn.close()

        size = backup_path.stat().st_size
        logger.info(
            "backup_created",
            database=database_name,
            file=backup_filename,
            size_bytes=size,
        )

        return {
            "database": database_name,
            "filename": backup_filename,
            "size": size,
            "created_at": timestamp,
        }

    def list_backups(self, database_name: str) -> list[dict[str, Any]]:
        """List available backups for a database, newest first.

        Args:
            database_name: Name of the database.

        Returns:
            List of dicts with backup metadata (filename, size, created_at).

        """
        self._validate_database_name(database_name)
        backup_dir = self._backup_dir / database_name
        if not backup_dir.exists():
            return []

        return [
            {
                "filename": f.name,
                "size": f.stat().st_size,
                "created_at": f.stem.replace("app_", ""),
            }
            for f in sorted(backup_dir.glob("app_*.db"), reverse=True)
        ]

    def restore_backup(self, database_name: str, backup_filename: str) -> dict[str, str]:
        """Restore a database from a backup file.

        Creates a safety backup of current state before restoring.

        Args:
            database_name: Name of the database to restore.
            backup_filename: Filename of the backup to restore from.

        Returns:
            Dict with restore metadata (database, restored_from).

        Raises:
            ValidationError: If the resolved backup path escapes the expected
                directory.
            NotFoundError: If the backup file does not exist.
            OperationError: If the backup file is not a valid SQLite database.

        """
        self._validate_database_name(database_name)
        backup_path = self._backup_dir / database_name / backup_filename
        resolved = backup_path.resolve()
        if not resolved.is_relative_to((self._backup_dir / database_name).resolve()):
            raise ValidationError("Invalid backup path", field="backup_path")
        if not backup_path.exists():
            raise NotFoundError("Backup", backup_filename)

        # Validate is a valid SQLite database
        conn = sqlite3.connect(str(backup_path))
        try:
            conn.execute("SELECT count(*) FROM sqlite_master")
        except sqlite3.DatabaseError as e:
            msg = f"Invalid backup file: {e}"
            raise OperationError(msg, operation="backup_restore") from e
        finally:
            conn.close()

        db_path = self._data_dir / "databases" / database_name / "app.db"

        # Safety backup of current state
        if db_path.exists():
            safety = db_path.with_suffix(".db.pre_restore")
            shutil.copy2(str(db_path), str(safety))
            logger.info("safety_backup_created", path=str(safety))

        # Clean up WAL journal files to prevent corruption after overwrite
        for suffix in (".db-wal", ".db-shm"):
            wal_file = db_path.with_suffix(suffix)
            if wal_file.exists():
                wal_file.unlink()
                logger.info("wal_file_removed", path=str(wal_file))

        # Replace current database
        shutil.copy2(str(backup_path), str(db_path))

        # Invalidate cached SQLAlchemy engines so new connections use the restored DB
        try:
            from chaoscypher_core.adapters.sqlite.engine import dispose_all_engines

            dispose_all_engines()
            logger.info("engines_invalidated_after_restore")
        except Exception:
            logger.debug("engine_invalidation_skipped")

        logger.info(
            "backup_restored",
            database=database_name,
            from_file=backup_filename,
        )
        return {"database": database_name, "restored_from": backup_filename}

    def delete_backup(self, database_name: str, backup_filename: str) -> None:
        """Delete a specific backup file.

        Args:
            database_name: Name of the database.
            backup_filename: Filename of the backup to delete.

        Raises:
            ValidationError: If the backup path is outside the expected directory.
            NotFoundError: If the backup file does not exist.

        """
        self._validate_database_name(database_name)
        backup_path = self._backup_dir / database_name / backup_filename
        resolved = backup_path.resolve()
        if not resolved.is_relative_to((self._backup_dir / database_name).resolve()):
            raise ValidationError("Invalid backup path", field="backup_path")
        if not backup_path.exists():
            raise NotFoundError("Backup", backup_filename)
        backup_path.unlink()
        logger.info("backup_deleted", database=database_name, file=backup_filename)

    def cleanup_old_backups(self, database_name: str, retention_count: int) -> int:
        """Remove old backups exceeding retention count.

        Args:
            database_name: Name of the database.
            retention_count: Number of backups to retain.

        Returns:
            Number of backups removed.

        """
        self._validate_database_name(database_name)
        backups = self.list_backups(database_name)
        if len(backups) <= retention_count:
            return 0

        to_remove = backups[retention_count:]
        for backup in to_remove:
            self.delete_backup(database_name, backup["filename"])
        return len(to_remove)

    def get_backup_path(self, database_name: str, backup_filename: str) -> Path:
        """Get the full path to a backup file for download.

        Args:
            database_name: Name of the database.
            backup_filename: Filename of the backup.

        Returns:
            Full path to the backup file.

        Raises:
            ValidationError: If the backup path is outside the expected directory.
            NotFoundError: If the backup file does not exist.

        """
        self._validate_database_name(database_name)
        path = self._backup_dir / database_name / backup_filename
        resolved = path.resolve()
        if not resolved.is_relative_to((self._backup_dir / database_name).resolve()):
            raise ValidationError("Invalid backup path", field="backup_path")
        if not path.exists():
            raise NotFoundError("Backup", backup_filename)
        return path
