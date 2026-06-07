# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for BackupFeatureService.

Verifies that the thin VSA wrapper delegates all operations to the core
BackupService and correctly propagates both return values and exceptions.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.exceptions import NotFoundError, OperationError
from chaoscypher_cortex.features.backup.service import BackupFeatureService


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def core_service() -> MagicMock:
    """Mocked core BackupService."""
    return MagicMock()


@pytest.fixture
def feature_service(core_service: MagicMock) -> BackupFeatureService:
    """BackupFeatureService wrapping the mocked core service."""
    return BackupFeatureService(backup_service=core_service)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateBackup:
    """Tests for BackupFeatureService.create_backup."""

    def test_delegates_to_core_service(
        self, feature_service: BackupFeatureService, core_service: MagicMock
    ) -> None:
        """create_backup calls core create_backup with the same db name."""
        core_service.create_backup.return_value = {
            "filename": "default-2026-04-10.zip",
            "size": 1024,
        }

        result = feature_service.create_backup("default")

        core_service.create_backup.assert_called_once_with("default")
        assert result["filename"] == "default-2026-04-10.zip"
        assert result["size"] == 1024


@pytest.mark.unit
class TestListBackups:
    """Tests for BackupFeatureService.list_backups."""

    def test_delegates_and_returns_list(
        self, feature_service: BackupFeatureService, core_service: MagicMock
    ) -> None:
        """list_backups returns the list produced by the core service."""
        core_service.list_backups.return_value = [
            {"filename": "a.zip"},
            {"filename": "b.zip"},
        ]

        result = feature_service.list_backups("default")

        core_service.list_backups.assert_called_once_with("default")
        assert len(result) == 2
        assert result[0]["filename"] == "a.zip"

    def test_returns_empty_list_when_no_backups(
        self, feature_service: BackupFeatureService, core_service: MagicMock
    ) -> None:
        """list_backups returns an empty list when there are no backups."""
        core_service.list_backups.return_value = []

        result = feature_service.list_backups("default")

        assert result == []


@pytest.mark.unit
class TestRestoreBackup:
    """Tests for BackupFeatureService.restore_backup."""

    def test_delegates_and_returns_result(
        self, feature_service: BackupFeatureService, core_service: MagicMock
    ) -> None:
        """restore_backup forwards args and returns the core service result."""
        core_service.restore_backup.return_value = {"status": "restored"}

        result = feature_service.restore_backup("default", "backup.zip")

        core_service.restore_backup.assert_called_once_with("default", "backup.zip")
        assert result["status"] == "restored"

    def test_propagates_not_found_error(
        self, feature_service: BackupFeatureService, core_service: MagicMock
    ) -> None:
        """restore_backup propagates NotFoundError when backup file is missing."""
        core_service.restore_backup.side_effect = NotFoundError("Backup", "missing.zip")

        with pytest.raises(NotFoundError) as exc_info:
            feature_service.restore_backup("default", "missing.zip")

        assert exc_info.value.code == "NOT_FOUND"

    def test_propagates_operation_error(
        self, feature_service: BackupFeatureService, core_service: MagicMock
    ) -> None:
        """restore_backup propagates OperationError on invalid backup file."""
        core_service.restore_backup.side_effect = OperationError(
            "Invalid backup file: file is not a database", operation="backup_restore"
        )

        with pytest.raises(OperationError, match="Invalid backup file"):
            feature_service.restore_backup("default", "corrupt.zip")


@pytest.mark.unit
class TestDeleteBackup:
    """Tests for BackupFeatureService.delete_backup."""

    def test_delegates_to_core_service(
        self, feature_service: BackupFeatureService, core_service: MagicMock
    ) -> None:
        """delete_backup calls the core service and returns None."""
        result = feature_service.delete_backup("default", "old.zip")

        core_service.delete_backup.assert_called_once_with("default", "old.zip")
        assert result is None

    def test_propagates_not_found_error(
        self, feature_service: BackupFeatureService, core_service: MagicMock
    ) -> None:
        """delete_backup propagates NotFoundError when the file does not exist."""
        core_service.delete_backup.side_effect = NotFoundError("Backup", "ghost.zip")

        with pytest.raises(NotFoundError) as exc_info:
            feature_service.delete_backup("default", "ghost.zip")

        assert exc_info.value.code == "NOT_FOUND"


@pytest.mark.unit
class TestGetBackupPath:
    """Tests for BackupFeatureService.get_backup_path."""

    def test_returns_path_from_core_service(
        self, feature_service: BackupFeatureService, core_service: MagicMock
    ) -> None:
        """get_backup_path returns the Path object from the core service."""
        expected = Path("/data/backups/default/backup.zip")
        core_service.get_backup_path.return_value = expected

        result = feature_service.get_backup_path("default", "backup.zip")

        core_service.get_backup_path.assert_called_once_with("default", "backup.zip")
        assert result == expected
        assert isinstance(result, Path)
