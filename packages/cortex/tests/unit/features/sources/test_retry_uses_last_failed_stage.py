# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""retry_source uses last_failed_stage when error_stage is RECOVERY_EXHAUSTED."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.models import SourceErrorStage, SourceStatus


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("last_failed_stage", "expected_resume_status"),
    [
        (SourceErrorStage.COMMIT.value, SourceStatus.EXTRACTED),
        (SourceErrorStage.EXTRACTION.value, SourceStatus.INDEXED),
        (SourceErrorStage.INDEXING.value, SourceStatus.PENDING),
        (None, SourceStatus.PENDING),  # legacy / pre-migration row
    ],
)
async def test_exhausted_retry_consults_last_failed_stage(
    last_failed_stage: str | None,
    expected_resume_status: str,
) -> None:
    from chaoscypher_cortex.features.sources.service import SourceService

    storage = MagicMock()
    storage.reset_for_retry = MagicMock()
    storage.get_extraction_results.return_value = {"extraction_results": {}}
    storage.get_system_state.return_value = {"processing_paused": False}

    engine_service = MagicMock()
    engine_service.get_source.return_value = {
        "id": "src_x",
        "status": SourceStatus.ERROR,
        "error_stage": SourceErrorStage.RECOVERY_EXHAUSTED.value,
        "last_failed_stage": last_failed_stage,
        "is_paused": False,
    }

    service = SourceService.__new__(SourceService)
    service.engine_service = engine_service
    service.storage_adapter = storage
    service.database_name = "default"
    service._dispatch_retry_task = AsyncMock()

    # retry_source may raise when building SourceResponse from the minimal
    # mock dict — we only care that reset_for_retry was called with the right
    # new_status before that point.
    try:
        await service.retry_source("src_x")
    except Exception:
        pass

    storage.reset_for_retry.assert_called_once()
    assert storage.reset_for_retry.call_args.kwargs["new_status"] == expected_resume_status
