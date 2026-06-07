# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: filtering_mode threads from upload routes through to the queued task payload.

Audit fix #C1 — the interface has been sending filtering_mode to
/sources/upload and /sources/upload/batch since 2026-03-25, but the
multipart Form(...) declarations dropped it silently. These tests verify
the parameter flows end-to-end from UploadService.upload_single through
the core upload_file call.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_single_upload_threads_filtering_mode_to_queue() -> None:
    """Posting filtering_mode=strict to /sources/upload lands in the queued task payload."""
    from chaoscypher_cortex.features.sources.upload_service import UploadService

    settings = MagicMock()
    settings.batching.upload_max_concurrent = 4
    settings.batching.upload_chunk_size = 1024
    settings.batching.max_upload_bytes = 10_000
    settings.batching.upload_disk_headroom_bytes = 0
    settings.batching.upload_content_type_allowlist = ["*"]
    settings.current_database = "default"
    settings.data_dir = "/tmp"

    queued_payloads: list[dict] = []

    async def _capture_upload_file(**kwargs):
        queued_payloads.append(kwargs)
        return {"id": "src_xyz", "filename": kwargs["filename"]}

    core_service = MagicMock()
    core_service.upload_file = AsyncMock(side_effect=_capture_upload_file)

    service = UploadService(
        settings=settings,
        source_processing_service=core_service,
    )
    service.preflight_disk_for_upload = MagicMock()
    service.validate_content_type = MagicMock()
    service.stream_upload_to_temp = AsyncMock(return_value=(Path("/tmp/x"), "hash", 5))

    upload_file = MagicMock()
    upload_file.filename = "doc.txt"

    await service.upload_single(
        file=upload_file,
        safe_filename="doc.txt",
        extract_entities=True,
        analysis_depth="full",
        enable_normalization=True,
        forced_domain=None,
        skip_duplicates=False,
        enable_vision=None,
        content_filtering=True,
        filtering_mode="strict",
    )

    assert len(queued_payloads) == 1
    assert queued_payloads[0]["filtering_mode"] == "strict"


@pytest.mark.asyncio
async def test_batch_upload_threads_filtering_mode_to_each_file() -> None:
    """Posting filtering_mode=maximum to /sources/upload/batch reaches every queued task."""
    from chaoscypher_cortex.features.sources.upload_service import UploadService

    settings = MagicMock()
    settings.batching.upload_max_concurrent = 4
    settings.batching.upload_chunk_size = 1024
    settings.batching.max_upload_bytes = 10_000
    settings.batching.upload_disk_headroom_bytes = 0
    settings.batching.upload_content_type_allowlist = ["*"]
    settings.batching.max_upload_files = 10
    settings.current_database = "default"
    settings.data_dir = "/tmp"

    queued_payloads: list[dict] = []

    async def _capture_upload_file(**kwargs):
        queued_payloads.append(kwargs)
        return {"id": "src_" + kwargs["filename"], "filename": kwargs["filename"]}

    core_service = MagicMock()
    core_service.upload_file = AsyncMock(side_effect=_capture_upload_file)

    service = UploadService(
        settings=settings,
        source_processing_service=core_service,
    )
    service.preflight_disk_for_upload = MagicMock()
    service.validate_content_type = MagicMock()
    service.stream_upload_to_temp = AsyncMock(return_value=(Path("/tmp/x"), "hash", 5))

    file_a = MagicMock()
    file_a.filename = "a.txt"
    file_b = MagicMock()
    file_b.filename = "b.txt"

    def _safe(name: str | None) -> str:
        return name or "unknown"

    await service.upload_batch(
        files=[file_a, file_b],
        sanitize_filename=_safe,
        extract_entities=True,
        analysis_depth="full",
        enable_normalization=True,
        forced_domain=None,
        skip_duplicates=False,
        content_filtering=True,
        filtering_mode="maximum",
    )

    assert len(queued_payloads) == 2
    assert all(p["filtering_mode"] == "maximum" for p in queued_payloads)


@pytest.mark.asyncio
async def test_filtering_mode_none_is_forwarded_as_none() -> None:
    """When filtering_mode is omitted (None), None reaches the core upload_file."""
    from chaoscypher_cortex.features.sources.upload_service import UploadService

    settings = MagicMock()
    settings.batching.upload_max_concurrent = 4
    settings.batching.upload_chunk_size = 1024
    settings.batching.max_upload_bytes = 10_000
    settings.batching.upload_disk_headroom_bytes = 0
    settings.batching.upload_content_type_allowlist = ["*"]
    settings.current_database = "default"
    settings.data_dir = "/tmp"

    queued_payloads: list[dict] = []

    async def _capture_upload_file(**kwargs):
        queued_payloads.append(kwargs)
        return {"id": "src_xyz", "filename": kwargs["filename"]}

    core_service = MagicMock()
    core_service.upload_file = AsyncMock(side_effect=_capture_upload_file)

    service = UploadService(
        settings=settings,
        source_processing_service=core_service,
    )
    service.preflight_disk_for_upload = MagicMock()
    service.validate_content_type = MagicMock()
    service.stream_upload_to_temp = AsyncMock(return_value=(Path("/tmp/x"), "hash", 5))

    upload_file = MagicMock()
    upload_file.filename = "doc.txt"

    await service.upload_single(
        file=upload_file,
        safe_filename="doc.txt",
        extract_entities=True,
        analysis_depth="full",
        enable_normalization=True,
        forced_domain=None,
        skip_duplicates=False,
        enable_vision=None,
        content_filtering=True,
        # filtering_mode omitted — defaults to None
    )

    assert len(queued_payloads) == 1
    assert queued_payloads[0]["filtering_mode"] is None
