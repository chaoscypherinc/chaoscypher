# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Regression: skip-duplicate uploads do not emit a file_uploaded activity event."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _settings(current_database: str = "default") -> SimpleNamespace:
    return SimpleNamespace(
        batching=SimpleNamespace(
            max_upload_bytes=10_485_760,
            upload_max_concurrent=4,
            upload_chunk_size=4096,
            upload_disk_headroom_bytes=10_000_000,
            upload_content_type_allowlist={"*"},
            max_upload_files=20,
        ),
        data_dir=str(Path("/tmp")),
        current_database=current_database,
    )


@pytest.mark.asyncio
async def test_upload_single_skips_event_on_dedup_hit(tmp_path: Path) -> None:
    from chaoscypher_cortex.features.sources.upload_service import UploadService

    source_processing_service = MagicMock()
    source_processing_service.upload_file = AsyncMock(
        return_value={
            "id": "src_existing",
            "filename": "dup.txt",
            "skipped_duplicate": True,
        },
    )

    service = UploadService(
        settings=_settings(),
        source_processing_service=source_processing_service,
    )
    service.preflight_disk_for_upload = MagicMock()
    service.validate_content_type = MagicMock()
    staged = tmp_path / "dup.txt"
    staged.write_bytes(b"x")
    service.stream_upload_to_temp = AsyncMock(return_value=(staged, "hash", 1))

    file = MagicMock()
    file.filename = "dup.txt"

    with patch("chaoscypher_cortex.features.sources.upload_service.event_bus") as bus:
        result = await service.upload_single(
            file=file,
            safe_filename="dup.txt",
            extract_entities=True,
            analysis_depth="full",
            enable_normalization=True,
            forced_domain=None,
            skip_duplicates=True,
            enable_vision=None,
            content_filtering=True,
        )

    assert result["skipped_duplicate"] is True
    bus.emit.assert_not_called()


@pytest.mark.asyncio
async def test_upload_single_emits_event_on_real_upload(tmp_path: Path) -> None:
    from chaoscypher_cortex.features.sources.upload_service import UploadService

    source_processing_service = MagicMock()
    source_processing_service.upload_file = AsyncMock(
        return_value={"id": "src_new", "filename": "new.txt"},
    )

    service = UploadService(
        settings=_settings(),
        source_processing_service=source_processing_service,
    )
    service.preflight_disk_for_upload = MagicMock()
    service.validate_content_type = MagicMock()
    staged = tmp_path / "new.txt"
    staged.write_bytes(b"x")
    service.stream_upload_to_temp = AsyncMock(return_value=(staged, "hash", 1))

    file = MagicMock()
    file.filename = "new.txt"

    with patch("chaoscypher_cortex.features.sources.upload_service.event_bus") as bus:
        await service.upload_single(
            file=file,
            safe_filename="new.txt",
            extract_entities=True,
            analysis_depth="full",
            enable_normalization=True,
            forced_domain=None,
            skip_duplicates=False,
            enable_vision=None,
            content_filtering=True,
        )

    bus.emit.assert_called_once()
    assert bus.emit.call_args.args[0] == "file_uploaded"
