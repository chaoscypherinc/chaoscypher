# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Reset services must delete the imports dir of THEIR database.

``DataResetService.reset_source_processing_history`` and the
``reset_knowledge_base`` import-file cleanup used ``settings.database_dir``,
which resolves to the *currently active* database — ignoring the
``database_name`` the service was constructed with. A reset scoped to
database A silently deleted database B's uploaded files instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_core.services.reset.data_reset import DataResetService
from chaoscypher_core.services.reset.operations import _delete_import_files


if TYPE_CHECKING:
    from collections.abc import Iterator


def _make_settings(tmp_path: Path, current_database: str) -> MagicMock:
    """Build settings whose current database differs from the reset target."""
    settings = MagicMock()
    settings.paths.data_dir = str(tmp_path)
    settings.paths.databases_subdir = "databases"
    settings.paths.imports_subdir = "imports"
    settings.current_database = current_database
    # The buggy path: database_dir always resolves to the CURRENT database.
    settings.database_dir = tmp_path / "databases" / current_database
    return settings


def _make_imports_dir(tmp_path: Path, database_name: str) -> Path:
    """Create databases/<name>/imports with one file inside."""
    imports_dir = tmp_path / "databases" / database_name / "imports"
    imports_dir.mkdir(parents=True)
    (imports_dir / "upload.txt").write_text("payload")
    return imports_dir


@pytest.fixture
def _mock_adapter() -> Iterator[MagicMock]:
    """Patch the sqlite adapter factory used by DataResetService."""
    adapter = MagicMock(name="adapter")
    adapter.count_sources.return_value = 0
    adapter.count_staged_chunks.return_value = 0
    adapter.count_embeddings.return_value = 0
    adapter.count_extraction_tasks.return_value = 0
    adapter.count_extraction_jobs.return_value = 0
    with patch(
        "chaoscypher_core.services.reset.data_reset.get_sqlite_adapter",
        return_value=adapter,
    ):
        yield adapter


class TestDataResetImportsDirTargeting:
    """reset_source_processing_history targets the constructed database."""

    @pytest.mark.usefixtures("_mock_adapter")
    def test_deletes_constructed_databases_imports_dir(self, tmp_path: Path) -> None:
        """With current_database=default, resetting 'myproj' deletes myproj's files."""
        current_imports = _make_imports_dir(tmp_path, "default")
        target_imports = _make_imports_dir(tmp_path, "myproj")
        settings = _make_settings(tmp_path, current_database="default")

        with patch(
            "chaoscypher_core.services.reset.data_reset.get_settings",
            return_value=settings,
        ):
            stats = DataResetService("myproj").reset_source_processing_history()

        assert stats["imports_directory_deleted"] is True
        assert not target_imports.exists()
        # The active database's uploads are untouched.
        assert current_imports.exists()
        assert (current_imports / "upload.txt").exists()


class TestKnowledgeBaseResetImportsDirTargeting:
    """_delete_import_files targets the database it is given."""

    @pytest.mark.asyncio
    async def test_deletes_named_databases_imports_dir(self, tmp_path: Path) -> None:
        """With current_database=default, the 'myproj' imports dir is removed."""
        current_imports = _make_imports_dir(tmp_path, "default")
        target_imports = _make_imports_dir(tmp_path, "myproj")
        settings = _make_settings(tmp_path, current_database="default")

        stats: dict[str, object] = {}
        await _delete_import_files(settings, "myproj", stats)

        assert stats["imports_directory_deleted"] is True
        assert not target_imports.exists()
        assert current_imports.exists()

    @pytest.mark.asyncio
    async def test_absent_imports_dir_records_false(self, tmp_path: Path) -> None:
        """No imports dir for the named database records False without error."""
        settings = _make_settings(tmp_path, current_database="default")

        stats: dict[str, object] = {}
        await _delete_import_files(settings, "myproj", stats)

        assert stats["imports_directory_deleted"] is False
