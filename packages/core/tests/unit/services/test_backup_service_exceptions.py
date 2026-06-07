# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Exception-contract tests for BackupService.

Pins the exception types raised at each validation and IO-failure site so
that the Cortex error mapper can produce structured 4xx/422 envelopes
instead of generic 500s.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from chaoscypher_core.exceptions import (
    ChaosCypherException,
    NotFoundError,
    OperationError,
    ValidationError,
)
from chaoscypher_core.services.backup import BackupService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(tmp_path: Path) -> BackupService:
    """Return a BackupService rooted at *tmp_path*."""
    return BackupService(data_dir=str(tmp_path))


def _seed_db(db_path: Path) -> None:
    """Create a minimal SQLite database at *db_path*."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()


def _force_traversal_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Monkeypatch Path.is_relative_to to always return False.

    This simulates a path-traversal escape regardless of platform, without
    having to construct an OS-specific path that genuinely escapes the
    backup directory (which differs between Windows and POSIX).
    """
    monkeypatch.setattr(Path, "is_relative_to", lambda self, other: False)


# ---------------------------------------------------------------------------
# Line 48 -- _validate_database_name (unsafe chars in database_name)
# Called indirectly through every public method that accepts database_name.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInvalidDatabaseName:
    """ValidationError is raised when database_name contains unsafe characters."""

    def test_create_backup_raises_validation_error_for_bad_name(self, tmp_path: Path) -> None:
        service = _make_service(tmp_path)
        with pytest.raises(ValidationError) as exc_info:
            service.create_backup("../../etc/passwd")
        exc = exc_info.value
        assert isinstance(exc, ChaosCypherException)
        assert exc.code == "VALIDATION_ERROR"
        assert exc.details.get("field") == "database_name"

    def test_validation_error_is_chaoscypher_exception(self, tmp_path: Path) -> None:
        service = _make_service(tmp_path)
        with pytest.raises(ChaosCypherException):
            service.create_backup("bad name!")


# ---------------------------------------------------------------------------
# database_name is validated on EVERY public method (defense-in-depth).
# The per-filename is_relative_to guard uses ``backup_dir / database_name``
# as its base, so a traversal *in database_name* escapes before the guard
# can catch it — only an up-front _validate_database_name closes that.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDatabaseNameValidatedOnAllMethods:
    """Every public method that interpolates database_name validates it first."""

    _BAD = "../../etc"

    def test_list_backups_validates_name(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError) as exc_info:
            _make_service(tmp_path).list_backups(self._BAD)
        assert exc_info.value.details.get("field") == "database_name"

    def test_restore_backup_validates_name(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError) as exc_info:
            _make_service(tmp_path).restore_backup(self._BAD, "any.db")
        assert exc_info.value.details.get("field") == "database_name"

    def test_delete_backup_validates_name(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError) as exc_info:
            _make_service(tmp_path).delete_backup(self._BAD, "any.db")
        assert exc_info.value.details.get("field") == "database_name"

    def test_cleanup_old_backups_validates_name(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError) as exc_info:
            _make_service(tmp_path).cleanup_old_backups(self._BAD, 5)
        assert exc_info.value.details.get("field") == "database_name"

    def test_get_backup_path_validates_name(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError) as exc_info:
            _make_service(tmp_path).get_backup_path(self._BAD, "any.db")
        assert exc_info.value.details.get("field") == "database_name"


# ---------------------------------------------------------------------------
# Line 85 -- create_backup path traversal guard
# Path is constructed from validated database_name. The guard fires when
# the resolved backup_path falls outside backup_dir. We force that by
# patching is_relative_to to always return False.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateBackupPathTraversal:
    """ValidationError is raised when the resolved backup path escapes the backup dir."""

    def test_raises_validation_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        service = _make_service(tmp_path)
        db_path = tmp_path / "databases" / "mydb" / "app.db"
        _seed_db(db_path)
        _force_traversal_escape(monkeypatch)

        with pytest.raises(ValidationError) as exc_info:
            service.create_backup("mydb")
        exc = exc_info.value
        assert isinstance(exc, ChaosCypherException)
        assert exc.code == "VALIDATION_ERROR"
        assert exc.details.get("field") == "backup_path"


# ---------------------------------------------------------------------------
# Line 154 -- restore_backup path traversal guard (backup_filename)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRestoreBackupPathTraversal:
    """ValidationError is raised when the resolved restore path escapes the backup dir."""

    def test_raises_validation_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        service = _make_service(tmp_path)
        _force_traversal_escape(monkeypatch)

        with pytest.raises(ValidationError) as exc_info:
            service.restore_backup("mydb", "any.db")
        exc = exc_info.value
        assert isinstance(exc, ChaosCypherException)
        assert exc.code == "VALIDATION_ERROR"
        assert exc.details.get("field") == "backup_path"


# ---------------------------------------------------------------------------
# Line 165 -- restore_backup wraps sqlite3.DatabaseError
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRestoreBackupCorruptFile:
    """OperationError is raised when the backup file is not a valid SQLite database."""

    def test_raises_operation_error_for_corrupt_backup(self, tmp_path: Path) -> None:
        service = _make_service(tmp_path)

        # Create the backup directory and a corrupt (non-SQLite) backup file.
        backup_dir = tmp_path / "backups" / "mydb"
        backup_dir.mkdir(parents=True)
        corrupt_file = backup_dir / "app_20260101_120000.db"
        corrupt_file.write_bytes(b"this is not a sqlite database")

        with pytest.raises(OperationError) as exc_info:
            service.restore_backup("mydb", "app_20260101_120000.db")
        exc = exc_info.value
        assert isinstance(exc, ChaosCypherException)
        assert exc.code == "OPERATION_ERROR"
        assert exc.details.get("operation") == "backup_restore"

    def test_operation_error_chains_original_cause(self, tmp_path: Path) -> None:
        service = _make_service(tmp_path)

        backup_dir = tmp_path / "backups" / "mydb"
        backup_dir.mkdir(parents=True)
        corrupt_file = backup_dir / "app_20260101_120000.db"
        corrupt_file.write_bytes(b"garbage data not sqlite")

        with pytest.raises(OperationError) as exc_info:
            service.restore_backup("mydb", "app_20260101_120000.db")
        assert exc_info.value.__cause__ is not None
        assert isinstance(exc_info.value.__cause__, sqlite3.DatabaseError)


# ---------------------------------------------------------------------------
# Line 218 -- delete_backup path traversal guard
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeleteBackupPathTraversal:
    """ValidationError is raised when the resolved delete path escapes the backup dir."""

    def test_raises_validation_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        service = _make_service(tmp_path)
        _force_traversal_escape(monkeypatch)

        with pytest.raises(ValidationError) as exc_info:
            service.delete_backup("mydb", "any.db")
        exc = exc_info.value
        assert isinstance(exc, ChaosCypherException)
        assert exc.code == "VALIDATION_ERROR"
        assert exc.details.get("field") == "backup_path"


# ---------------------------------------------------------------------------
# Line 263 -- get_backup_path path traversal guard
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetBackupPathTraversal:
    """ValidationError is raised when the resolved path escapes the backup dir."""

    def test_raises_validation_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        service = _make_service(tmp_path)
        _force_traversal_escape(monkeypatch)

        with pytest.raises(ValidationError) as exc_info:
            service.get_backup_path("mydb", "any.db")
        exc = exc_info.value
        assert isinstance(exc, ChaosCypherException)
        assert exc.code == "VALIDATION_ERROR"
        assert exc.details.get("field") == "backup_path"


# ---------------------------------------------------------------------------
# NotFoundError sites (FileNotFoundError sweep)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateBackupDatabaseNotFound:
    """NotFoundError is raised when the database file does not exist."""

    def test_raises_not_found_error(self, tmp_path: Path) -> None:
        service = _make_service(tmp_path)
        with pytest.raises(NotFoundError) as exc_info:
            service.create_backup("ghost_db")
        exc = exc_info.value
        assert isinstance(exc, ChaosCypherException)
        assert exc.code == "NOT_FOUND"
        assert exc.resource_type == "Database"
        assert exc.identifier == "ghost_db"


@pytest.mark.unit
class TestRestoreBackupFileNotFound:
    """NotFoundError is raised when the backup file does not exist."""

    def test_raises_not_found_error(self, tmp_path: Path) -> None:
        service = _make_service(tmp_path)
        backup_dir = tmp_path / "backups" / "mydb"
        backup_dir.mkdir(parents=True)
        with pytest.raises(NotFoundError) as exc_info:
            service.restore_backup("mydb", "ghost.db")
        exc = exc_info.value
        assert isinstance(exc, ChaosCypherException)
        assert exc.code == "NOT_FOUND"
        assert exc.resource_type == "Backup"
        assert exc.identifier == "ghost.db"


@pytest.mark.unit
class TestDeleteBackupFileNotFound:
    """NotFoundError is raised when the backup file does not exist."""

    def test_raises_not_found_error(self, tmp_path: Path) -> None:
        service = _make_service(tmp_path)
        backup_dir = tmp_path / "backups" / "mydb"
        backup_dir.mkdir(parents=True)
        with pytest.raises(NotFoundError) as exc_info:
            service.delete_backup("mydb", "ghost.db")
        exc = exc_info.value
        assert isinstance(exc, ChaosCypherException)
        assert exc.code == "NOT_FOUND"
        assert exc.resource_type == "Backup"
        assert exc.identifier == "ghost.db"


@pytest.mark.unit
class TestGetBackupPathFileNotFound:
    """NotFoundError is raised when the backup file does not exist."""

    def test_raises_not_found_error(self, tmp_path: Path) -> None:
        service = _make_service(tmp_path)
        backup_dir = tmp_path / "backups" / "mydb"
        backup_dir.mkdir(parents=True)
        with pytest.raises(NotFoundError) as exc_info:
            service.get_backup_path("mydb", "ghost.db")
        exc = exc_info.value
        assert isinstance(exc, ChaosCypherException)
        assert exc.code == "NOT_FOUND"
        assert exc.resource_type == "Backup"
        assert exc.identifier == "ghost.db"
