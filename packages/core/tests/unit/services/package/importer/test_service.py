# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests: ImportService zip-bomb rejection.

Verifies that _extract_zip enforces size caps so that a small compressed
archive that would decompress to many MB is rejected before extraction
completes.
"""

from __future__ import annotations

import io
import zipfile
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.package.importer.service import ImportService
from chaoscypher_core.settings import ArchiveSettings, EngineSettings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_import_service(max_extracted_size_mb: int = 10) -> ImportService:
    """Construct an ImportService with a tight archive cap for testing."""
    archive_settings = ArchiveSettings(
        max_extracted_size_mb=max_extracted_size_mb,
        max_files=100,
    )
    engine_settings = EngineSettings(archive=archive_settings)
    graph_repository = MagicMock()
    return ImportService(
        graph_repository=graph_repository,
        engine_settings=engine_settings,
    )


def _make_zip_bomb_bytes(uncompressed_mb: int) -> bytes:
    """Return a zip archive whose single entry decompresses to *uncompressed_mb* MB."""
    data = b"A" * (uncompressed_mb * 1024 * 1024)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("payload.txt", data)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestImportServiceZipBombRejection:
    """ImportService must reject archives that exceed the decompressed-size cap."""

    @pytest.mark.asyncio
    async def test_import_rejects_zip_bomb_via_declared_size(self) -> None:
        """A zip whose declared size exceeds the cap is rejected before extraction."""
        # Create a zip that declares 11 MB (exceeds the 10 MB cap)
        archive_bytes = _make_zip_bomb_bytes(uncompressed_mb=11)
        service = _make_import_service(max_extracted_size_mb=10)

        stats = await service.import_from_bytes(archive_bytes)

        assert stats.errors, "Expected errors list to be non-empty"
        combined = " ".join(stats.errors).lower()
        assert any(kw in combined for kw in ("size", "limit", "cap", "rejected", "exceeds")), (
            f"Expected a size/limit message in errors; got: {stats.errors}"
        )

    @pytest.mark.asyncio
    async def test_import_accepts_archive_within_cap(self) -> None:
        """A small, valid archive is accepted (no size-related errors)."""
        # 1 KB payload — well under the 10 MB cap
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", '{"version": "1.0"}')
        archive_bytes = buf.getvalue()

        service = _make_import_service(max_extracted_size_mb=10)
        stats = await service.import_from_bytes(archive_bytes)

        # No size/limit errors (there may be warnings about missing files,
        # but no archive-rejection errors)
        size_errors = [
            e
            for e in stats.errors
            if any(kw in e.lower() for kw in ("size", "limit", "cap", "exceeds", "rejected"))
        ]
        assert not size_errors, f"Unexpected size errors for small archive: {size_errors}"
