# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests for size + content-type enforcement on source uploads.

Before 2026-04-18, POST /sources and /sources/batch streamed to temp
without a per-file size cap and without content-type validation. A
single upload could fill the disk; a malicious client could upload
arbitrary binaries (e.g., executables) and have the loader plugin
attempt to process them.

After the Workstream-B refactor (Task 5), the helpers live on
``UploadService`` rather than as module-level functions in ``api.py``.
Tests now drive ``UploadService`` directly — no FastAPI stack required.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import UploadFile

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_cortex.features.sources.upload_service import UploadService


def _make_service(
    allowlist: list[str] | None = None,
    max_upload_bytes: int = 10 * 1024 * 1024,
    tmp_path: Path | None = None,
) -> UploadService:
    """Return a minimal UploadService wired to a mock settings object."""
    settings = MagicMock()
    settings.batching.upload_content_type_allowlist = allowlist or ["application/pdf", "text/plain"]
    settings.batching.max_upload_bytes = max_upload_bytes
    settings.batching.upload_chunk_size = 65536
    settings.batching.upload_max_concurrent = 4
    settings.batching.upload_disk_headroom_bytes = 100 * 1024 * 1024
    settings.data_dir = "/tmp"
    settings.database_dir = tmp_path if tmp_path is not None else Path("/tmp")
    source_processing_service = MagicMock()
    return UploadService(
        settings=settings,
        source_processing_service=source_processing_service,
    )


def _make_upload(content: bytes, content_type: str | None = None) -> UploadFile:
    file = UploadFile(file=BytesIO(content), filename="probe.bin")
    if content_type is not None:
        file.headers = {"content-type": content_type}
    return file


def test_validate_content_type_rejects_unlisted() -> None:
    service = _make_service(allowlist=["application/pdf", "text/plain"])
    file = MagicMock(spec=UploadFile)
    file.content_type = "application/x-msdownload"
    with pytest.raises(ValidationError) as excinfo:
        service.validate_content_type(file)
    assert "not allowed" in str(excinfo.value)


def test_validate_content_type_allows_listed() -> None:
    service = _make_service(allowlist=["application/pdf"])
    file = MagicMock(spec=UploadFile)
    file.content_type = "application/pdf"
    # Should not raise.
    service.validate_content_type(file)


def test_validate_content_type_allows_star_wildcard() -> None:
    service = _make_service(allowlist=["*"])
    file = MagicMock(spec=UploadFile)
    file.content_type = "anything/at-all"
    # '*' disables the check — must not raise.
    service.validate_content_type(file)


def test_validate_content_type_allows_missing_header() -> None:
    """Clients sometimes omit content-type; downstream sniffs the real type."""
    service = _make_service(allowlist=["application/pdf"])
    file = MagicMock(spec=UploadFile)
    file.content_type = None
    service.validate_content_type(file)


def test_validate_content_type_strips_charset_suffix() -> None:
    """text/plain; charset=utf-8 must match the 'text/plain' allowlist entry."""
    service = _make_service(allowlist=["text/plain"])
    file = MagicMock(spec=UploadFile)
    file.content_type = "text/plain; charset=utf-8"
    service.validate_content_type(file)


@pytest.mark.asyncio
async def test_stream_upload_to_temp_raises_400_when_max_bytes_exceeded(
    tmp_path: Path,
) -> None:
    """Uploads larger than max_bytes must raise ValidationError (→ HTTP 400)."""
    service = _make_service(max_upload_bytes=512, tmp_path=tmp_path)
    payload = b"x" * 1024  # 1 KiB > 512 B cap
    file = AsyncMock(spec=UploadFile)
    # First read returns the payload; second returns empty (EOF).
    file.read = AsyncMock(side_effect=[payload, b""])

    with pytest.raises(ValidationError) as excinfo:
        await service.stream_upload_to_temp(file)

    assert "max_upload_bytes" in str(excinfo.value)


@pytest.mark.asyncio
async def test_stream_upload_to_temp_succeeds_under_cap(tmp_path: Path) -> None:
    """Uploads within the cap should complete normally."""
    service = _make_service(max_upload_bytes=1024, tmp_path=tmp_path)
    payload = b"hello world"
    file = AsyncMock(spec=UploadFile)
    file.read = AsyncMock(side_effect=[payload, b""])

    staged_path, content_hash, size = await service.stream_upload_to_temp(file)
    assert size == len(payload)
    assert staged_path.exists()
    assert staged_path.parent == tmp_path / "uploads"
    assert content_hash  # sha256 hex
    staged_path.unlink()
