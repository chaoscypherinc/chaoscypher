# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for database deletion safety and cascade."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_core.exceptions import NotFoundError, ValidationError


# ============================================================================
# DatabaseRepository Tests
# ============================================================================


def _make_repo(tmp_path):
    """Create DatabaseRepository with temp directory, patching PathSettings."""
    with patch("chaoscypher_core.app_config.PathSettings") as mock_ps:
        mock_ps.return_value.databases_subdir = "databases"
        mock_ps.return_value.app_db_filename = "app.db"
        from chaoscypher_core.database.repository import DatabaseRepository

        return DatabaseRepository(data_root=str(tmp_path))


def _create_db(repo, name: str) -> None:
    """Create a fake database directory with app.db."""
    db_dir = os.path.join(repo.databases_dir, name)
    os.makedirs(db_dir, exist_ok=True)
    Path(os.path.join(db_dir, "app.db")).touch()


class TestDatabaseRepositoryDelete:
    """Tests for DatabaseRepository.delete_database."""

    @pytest.fixture
    def repo(self, tmp_path):
        return _make_repo(tmp_path)

    def test_deletes_directory(self, repo) -> None:
        _create_db(repo, "test_db")
        assert os.path.exists(os.path.join(repo.databases_dir, "test_db"))
        result = repo.delete_database("test_db")
        assert result is True
        assert not os.path.exists(os.path.join(repo.databases_dir, "test_db"))

    def test_blocks_default_deletion(self, repo) -> None:
        _create_db(repo, "default")
        with pytest.raises(ValueError, match="Cannot delete default"):
            repo.delete_database("default")
        assert os.path.exists(os.path.join(repo.databases_dir, "default"))

    def test_allow_default_override(self, repo) -> None:
        _create_db(repo, "default")
        result = repo.delete_database("default", allow_default=True)
        assert result is True
        assert not os.path.exists(os.path.join(repo.databases_dir, "default"))

    def test_raises_on_nonexistent(self, repo) -> None:
        with pytest.raises(ValueError, match="does not exist"):
            repo.delete_database("ghost")

    def test_deletes_all_subdirectories(self, repo) -> None:
        _create_db(repo, "mydb")
        db_dir = os.path.join(repo.databases_dir, "mydb")
        os.makedirs(os.path.join(db_dir, "images", "src1"), exist_ok=True)
        Path(os.path.join(db_dir, "images", "src1", "page_1.png")).touch()
        repo.delete_database("mydb")
        assert not os.path.exists(db_dir)


class TestDatabaseRepositoryList:
    """Tests for DatabaseRepository.list_databases."""

    @pytest.fixture
    def repo(self, tmp_path):
        return _make_repo(tmp_path)

    def test_lists_databases(self, repo) -> None:
        _create_db(repo, "alpha")
        _create_db(repo, "beta")
        result = repo.list_databases()
        names = [db.name for db in result]
        assert "alpha" in names
        assert "beta" in names

    def test_empty_directory(self, repo) -> None:
        result = repo.list_databases()
        assert result == []

    def test_excludes_non_database_dirs(self, repo) -> None:
        os.makedirs(os.path.join(repo.databases_dir, "not_a_db"), exist_ok=True)
        result = repo.list_databases()
        assert result == []


# ============================================================================
# DatabasesService Tests
# ============================================================================


class TestDatabaseServiceDelete:
    """Tests for DatabasesService.delete_database."""

    @pytest.fixture
    def mock_repo(self):
        return MagicMock()

    @pytest.fixture
    def mock_settings(self):
        settings = MagicMock()
        settings.current_database = "default"
        return settings

    @pytest.fixture
    def service(self, mock_repo, mock_settings):
        from chaoscypher_cortex.features.databases.service import DatabasesService

        return DatabasesService(database_repository=mock_repo, settings=mock_settings)

    def test_blocks_active_database_deletion(self, service, mock_settings) -> None:
        mock_settings.current_database = "active_db"
        with pytest.raises(ValidationError):
            service.delete_database("active_db")

    def test_delegates_to_repository(self, service, mock_repo) -> None:
        service.delete_database("other_db")
        mock_repo.delete_database.assert_called_once_with("other_db")

    def test_wraps_value_error_as_validation_error(self, service, mock_repo) -> None:
        mock_repo.delete_database.side_effect = ValueError("does not exist")
        with pytest.raises(ValidationError):
            service.delete_database("ghost")

    def test_allows_non_active_database(self, service, mock_settings) -> None:
        mock_settings.current_database = "default"
        service.delete_database("other_db")


class TestDatabaseServiceSwitch:
    """Tests for DatabasesService.switch_database."""

    @pytest.fixture
    def mock_repo(self):
        repo = MagicMock()
        db_info = MagicMock()
        db_info.model_dump.return_value = {
            "name": "new_db",
            "path": "/tmp/new_db",
            "exists": True,
            "size": 1024,
            "last_modified": None,
        }
        repo.get_database.return_value = db_info
        return repo

    @pytest.fixture
    def mock_settings(self):
        settings = MagicMock()
        settings.current_database = "default"
        return settings

    @pytest.fixture
    def service(self, mock_repo, mock_settings):
        from chaoscypher_cortex.features.databases.service import DatabasesService

        return DatabasesService(database_repository=mock_repo, settings=mock_settings)

    @patch("chaoscypher_cortex.features.databases.service.reload_settings")
    @patch("chaoscypher_cortex.features.databases.service.get_config_manager")
    @patch("chaoscypher_cortex.features.databases.service.database_exists", return_value=True)
    def test_switches_database(self, _mock_db_exists, mock_config, _mock_reload, service) -> None:  # noqa: PT019 - @patch params, not fixtures
        result = service.switch_database("new_db")
        assert result.success is True
        assert result.database == "new_db"
        mock_config.return_value.update_settings.assert_called_once_with(
            {"current_database": "new_db"}
        )

    def test_raises_on_nonexistent_database(self, service, mock_repo) -> None:
        mock_repo.get_database.return_value = None
        with pytest.raises(NotFoundError):
            service.switch_database("ghost")

    @patch("chaoscypher_cortex.features.databases.service.reload_settings")
    @patch("chaoscypher_cortex.features.databases.service.get_config_manager")
    @patch("chaoscypher_cortex.features.databases.service.database_exists", return_value=False)
    @patch("chaoscypher_cortex.features.databases.service.init_database")
    def test_auto_initializes_missing_app_db(
        self,
        mock_init,
        _mock_db_exists,  # noqa: PT019 - @patch param, not fixture
        _mock_config,  # noqa: PT019 - @patch param, not fixture
        _mock_reload,  # noqa: PT019 - @patch param, not fixture
        service,
    ) -> None:
        service.switch_database("new_db")
        mock_init.assert_called_once_with("new_db")
