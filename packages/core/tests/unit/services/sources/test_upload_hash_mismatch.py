# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""F14: caller-provided content_hash must match the actual file content.

Defense in depth: the upload service trusts the caller for performance
(skip rehashing in the common path) but verifies when both a hash and the
content are supplied. A mismatch — typically a caller reusing a hash from a
different file — must raise ``ValidationError`` BEFORE we create a corrupt
SourceRow that would later silently fail dedup and integrity checks.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.services.sources.management.service import (
    SourceProcessingService,
)


def _make_service(tmp_path: Path) -> tuple[SourceProcessingService, MagicMock]:
    source_manager = MagicMock()
    source_manager.upload_source.side_effect = lambda **kwargs: {
        "id": "src_1",
        **kwargs,
    }
    source_manager.find_by_content_hash.return_value = None

    settings = MagicMock()
    settings.current_database = "default"
    settings.database_dir = tmp_path
    # F12: file size check now reads batching.max_upload_bytes (not the
    # deprecated source_processing_max_file_size_gb).
    settings.batching.max_upload_bytes = 500 * 1024 * 1024  # 500 MB
    settings.source_processing.source_processing_max_file_size_gb = 10
    settings.priorities.background = 50
    config_manager = MagicMock()
    config_manager.get_settings.return_value = settings

    operations_manager = MagicMock()
    operations_manager.queue_import_indexing = AsyncMock(return_value={"task_id": "tsk_1"})
    validators = MagicMock()

    service = SourceProcessingService(
        source_manager=source_manager,
        operations_manager=operations_manager,
        config_manager=config_manager,
        validators=validators,
    )
    return service, source_manager


@pytest.mark.asyncio
async def test_mismatched_hash_with_bytes_raises(tmp_path: Path) -> None:
    """Caller passes hash of file A but bytes of file B → ValidationError."""
    service, source_manager = _make_service(tmp_path)

    file_a = b"this is file A's content"
    file_b = b"this is file B's content - completely different"
    hash_of_a = hashlib.sha256(file_a).hexdigest()

    with pytest.raises(ValidationError) as exc_info:
        await service.upload_file(
            file_content=file_b,
            filename="b.txt",
            content_hash=hash_of_a,
        )

    assert exc_info.value.field == "content_hash"
    assert "does not match" in str(exc_info.value)
    # The corrupt row must NOT have been written.
    source_manager.upload_source.assert_not_called()


@pytest.mark.asyncio
async def test_mismatched_hash_with_staged_path_raises(tmp_path: Path) -> None:
    """Caller passes hash of file A but staged path to file B → ValidationError."""
    service, source_manager = _make_service(tmp_path)

    file_b = tmp_path / "b.txt"
    file_b.write_bytes(b"actually file B")
    bogus_hash = "0" * 64  # hash of absolutely nothing relevant

    with pytest.raises(ValidationError) as exc_info:
        await service.upload_file(
            staged_file_path=file_b,
            filename="b.txt",
            content_hash=bogus_hash,
            file_size=file_b.stat().st_size,
        )

    assert exc_info.value.field == "content_hash"
    source_manager.upload_source.assert_not_called()


@pytest.mark.asyncio
async def test_matching_hash_with_bytes_succeeds(tmp_path: Path) -> None:
    """Caller passes correct hash and bytes → upload proceeds normally."""
    service, source_manager = _make_service(tmp_path)

    body = b"matching content"
    real_hash = hashlib.sha256(body).hexdigest()

    await service.upload_file(
        file_content=body,
        filename="ok.txt",
        content_hash=real_hash,
    )

    source_manager.upload_source.assert_called_once()
    captured_kwargs = source_manager.upload_source.call_args.kwargs
    assert captured_kwargs["content_hash"] == real_hash


@pytest.mark.asyncio
async def test_matching_hash_with_staged_path_succeeds(tmp_path: Path) -> None:
    """Caller passes correct hash and staged path → upload proceeds normally."""
    service, source_manager = _make_service(tmp_path)

    body = b"matching staged content"
    staged = tmp_path / "ok.txt"
    staged.write_bytes(body)
    real_hash = hashlib.sha256(body).hexdigest()

    await service.upload_file(
        staged_file_path=staged,
        filename="ok.txt",
        content_hash=real_hash,
        file_size=len(body),
    )

    source_manager.upload_source.assert_called_once()
