# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract tests: package/archive/create.py exception hygiene.

Verifies that create_archive raises ValidationError (not bare ValueError)
when source_dir exists but is not a directory.
"""

from __future__ import annotations

import pytest

from chaoscypher_core.exceptions import NotFoundError, ValidationError
from chaoscypher_core.services.package.archive.create import create_archive


class TestCreateArchiveExceptions:
    """Contract tests for create_archive exception hygiene."""

    def test_source_is_file_raises_validation_error(self, tmp_path: pytest.TempPathFactory) -> None:
        """create_archive raises ValidationError when source_dir is a file, not a directory."""
        # Create a file at a path that would be mistaken for a directory
        source_file = tmp_path / "not_a_dir.txt"
        source_file.write_text("I am a file, not a directory")
        output_path = tmp_path / "output.ccx"

        with pytest.raises(ValidationError) as exc_info:
            create_archive(source_file, output_path)

        # Verify it's not a bare ValueError leaking through
        assert isinstance(exc_info.value, ValidationError)
        assert (
            "not a directory" in exc_info.value.message.lower()
            or "source" in exc_info.value.message.lower()
        )
        assert exc_info.value.field == "source_dir"

    def test_source_missing_raises_not_found_error(self, tmp_path: pytest.TempPathFactory) -> None:
        """create_archive raises NotFoundError when source_dir does not exist."""
        missing_dir = tmp_path / "ghost_dir"
        output_path = tmp_path / "output.ccx"

        with pytest.raises(NotFoundError) as exc_info:
            create_archive(missing_dir, output_path)

        exc = exc_info.value
        assert exc.code == "NOT_FOUND"
        assert exc.resource_type == "Directory"
        assert str(missing_dir) in exc.identifier

    def test_valid_source_dir_creates_archive(self, tmp_path: pytest.TempPathFactory) -> None:
        """create_archive succeeds when source_dir is a valid directory."""
        source_dir = tmp_path / "my_package"
        source_dir.mkdir()
        (source_dir / "manifest.json").write_text('{"name": "test"}')
        output_path = tmp_path / "output.ccx"

        result = create_archive(source_dir, output_path)

        assert result.exists()
        assert result.suffix == ".ccx"
