# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Exception-contract tests for DatabaseRepository.

DatabaseRepository raised bare stdlib ValueError from every validation
site; Cortex's DatabasesService caught those and re-wrapped them as core
ValidationError (HTTP 400). The repository now raises ValidationError
directly so both layers speak ChaosCypherException — the HTTP mapping
(VALIDATION_ERROR → 400) is unchanged.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from chaoscypher_core.exceptions import ValidationError


if TYPE_CHECKING:
    from chaoscypher_core.database.repository import DatabaseRepository


def _make_repo(tmp_path: Path) -> DatabaseRepository:
    """Create DatabaseRepository with temp directory, patching PathSettings."""
    with patch("chaoscypher_core.app_config.PathSettings") as mock_ps:
        mock_ps.return_value.databases_subdir = "databases"
        mock_ps.return_value.app_db_filename = "app.db"
        from chaoscypher_core.database.repository import DatabaseRepository

        return DatabaseRepository(data_root=str(tmp_path))


def _create_db_dir(repo: DatabaseRepository, name: str) -> None:
    """Create a fake database directory with app.db."""
    db_dir = os.path.join(repo.databases_dir, name)
    os.makedirs(db_dir, exist_ok=True)
    Path(os.path.join(db_dir, "app.db")).touch()


class TestCreateDatabaseExceptions:
    """create_database validation failures raise core ValidationError."""

    def test_invalid_characters_raise_validation_error(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError, match="must be alphanumeric"):
            _make_repo(tmp_path).create_database("bad name!")

    def test_overlong_name_raises_validation_error(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError, match="characters or fewer"):
            _make_repo(tmp_path).create_database("x" * 65)

    def test_reserved_name_raises_validation_error(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError, match="reserved"):
            _make_repo(tmp_path).create_database("con")

    def test_existing_database_raises_validation_error(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _create_db_dir(repo, "taken")
        with pytest.raises(ValidationError, match="already exists"):
            repo.create_database("taken")


class TestDeleteDatabaseExceptions:
    """delete_database failures raise core ValidationError."""

    def test_default_database_raises_validation_error(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _create_db_dir(repo, "default")
        with pytest.raises(ValidationError, match="Cannot delete default"):
            repo.delete_database("default")

    def test_nonexistent_database_raises_validation_error(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError, match="does not exist"):
            _make_repo(tmp_path).delete_database("ghost")

    def test_traversal_escape_raises_validation_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make_repo(tmp_path)
        monkeypatch.setattr(Path, "is_relative_to", lambda self, other: False)
        with pytest.raises(ValidationError, match="Invalid database name"):
            repo.delete_database("sneaky")
