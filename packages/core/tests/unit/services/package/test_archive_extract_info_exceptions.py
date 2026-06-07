# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract tests: package/archive/extract.py and info.py exception hygiene.

Verifies that extract_archive and get_archive_info raise NotFoundError (not
bare FileNotFoundError) when the archive path does not exist, so the Cortex
error mapper produces a 404 envelope.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoscypher_core.exceptions import NotFoundError
from chaoscypher_core.services.package.archive.extract import extract_archive
from chaoscypher_core.services.package.archive.info import get_archive_info


@pytest.mark.unit
class TestExtractArchiveExceptions:
    """Contract tests for extract_archive exception hygiene."""

    def test_missing_archive_raises_not_found_error(self, tmp_path: Path) -> None:
        """extract_archive raises NotFoundError when archive_path does not exist."""
        missing = tmp_path / "ghost.ccx"
        dest = tmp_path / "out"

        with pytest.raises(NotFoundError) as exc_info:
            extract_archive(missing, dest)

        exc = exc_info.value
        assert exc.code == "NOT_FOUND"
        assert exc.resource_type == "Archive"
        assert str(missing) in exc.identifier


@pytest.mark.unit
class TestGetArchiveInfoExceptions:
    """Contract tests for get_archive_info exception hygiene."""

    def test_missing_archive_raises_not_found_error(self, tmp_path: Path) -> None:
        """get_archive_info raises NotFoundError when archive_path does not exist."""
        missing = tmp_path / "ghost.ccx"

        with pytest.raises(NotFoundError) as exc_info:
            get_archive_info(missing)

        exc = exc_info.value
        assert exc.code == "NOT_FOUND"
        assert exc.resource_type == "Archive"
        assert str(missing) in exc.identifier
