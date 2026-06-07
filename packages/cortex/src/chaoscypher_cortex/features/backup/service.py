# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Backup feature service layer."""

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from pathlib import Path

    from chaoscypher_core.services.backup import BackupService


class BackupFeatureService:
    """Thin wrapper around core BackupService for the VSA layer."""

    def __init__(self, backup_service: BackupService) -> None:
        """Initialize backup feature service.

        Args:
            backup_service: Core backup service instance.

        """
        self._service = backup_service

    def create_backup(self, database_name: str) -> dict[str, Any]:
        """Create a backup.

        Args:
            database_name: Name of the database to back up.

        Returns:
            Dict with backup metadata.

        """
        return self._service.create_backup(database_name)

    def list_backups(self, database_name: str) -> list[dict[str, Any]]:
        """List available backups.

        Args:
            database_name: Name of the database.

        Returns:
            List of dicts with backup metadata.

        """
        return self._service.list_backups(database_name)

    def restore_backup(self, database_name: str, filename: str) -> dict[str, str]:
        """Restore from a backup.

        Args:
            database_name: Name of the database.
            filename: Backup filename to restore from.

        Returns:
            Dict with restore metadata.

        """
        return self._service.restore_backup(database_name, filename)

    def delete_backup(self, database_name: str, filename: str) -> None:
        """Delete a backup.

        Args:
            database_name: Name of the database.
            filename: Backup filename to delete.

        """
        self._service.delete_backup(database_name, filename)

    def get_backup_path(self, database_name: str, filename: str) -> Path:
        """Get path for download.

        Args:
            database_name: Name of the database.
            filename: Backup filename.

        Returns:
            Full path to the backup file.

        """
        return self._service.get_backup_path(database_name, filename)
