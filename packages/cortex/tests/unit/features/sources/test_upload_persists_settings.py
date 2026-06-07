# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""End-to-end (component-level): UploadService.upload_single forwards every
upload setting to ``SourceProcessingService.upload_file``.

Verifies the Workstream 1 (2026-05-07) contract: the user's choices on the
upload form arrive at the core service so the source row is initialised
with them. We mock the core service to capture kwargs rather than spin up
a full Cortex test app; this matches the pattern already used in
``test_filtering_mode_thread_through.py``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


def _build_settings() -> MagicMock:
    settings = MagicMock()
    settings.batching.upload_max_concurrent = 4
    settings.batching.upload_chunk_size = 1024
    settings.batching.max_upload_bytes = 10_000
    settings.batching.upload_disk_headroom_bytes = 0
    settings.batching.upload_content_type_allowlist = ["*"]
    settings.current_database = "default"
    settings.data_dir = "/tmp"
    return settings


@pytest.mark.asyncio
async def test_upload_single_forwards_all_upload_settings() -> None:
    """Posting extract_entities=False / vision off / strict mode forwards all flags."""
    from chaoscypher_cortex.features.sources.upload_service import UploadService

    captured: list[dict] = []

    async def _capture_upload_file(**kwargs):
        captured.append(kwargs)
        return {"id": "src_xyz", "filename": kwargs["filename"]}

    core_service = MagicMock()
    core_service.upload_file = AsyncMock(side_effect=_capture_upload_file)

    service = UploadService(
        settings=_build_settings(),
        source_processing_service=core_service,
    )
    service.preflight_disk_for_upload = MagicMock()
    service.validate_content_type = MagicMock()
    service.stream_upload_to_temp = AsyncMock(return_value=(Path("/tmp/x"), "hash", 5))

    upload_file = MagicMock()
    upload_file.filename = "t.txt"

    await service.upload_single(
        file=upload_file,
        safe_filename="t.txt",
        extract_entities=False,
        analysis_depth="full",
        enable_normalization=False,
        forced_domain=None,
        skip_duplicates=False,
        enable_vision=False,
        content_filtering=False,
        filtering_mode="strict",
    )

    assert len(captured) == 1
    kwargs = captured[0]
    # auto_analyze on the core call mirrors extract_entities on the route.
    assert kwargs["auto_analyze"] is False
    assert kwargs["enable_vision"] is False
    assert kwargs["enable_normalization"] is False
    assert kwargs["content_filtering"] is False
    assert kwargs["filtering_mode"] == "strict"
