# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Regression: retry_source 409s when source or system is paused."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.exceptions import ConflictError
from chaoscypher_core.models import SourceStatus


def _make_service(*, source_paused: bool, system_paused: bool):
    from chaoscypher_cortex.features.sources.service import SourceService

    engine_service = MagicMock()
    engine_service.get_source.return_value = {
        "id": "src1",
        "status": SourceStatus.ERROR,
        "error_stage": "extraction",
        "is_paused": source_paused,
    }

    storage_adapter = MagicMock()
    storage_adapter.get_system_state.return_value = (
        {"processing_paused": True} if system_paused else {"processing_paused": False}
    )
    storage_adapter.reset_for_retry = MagicMock()

    service = SourceService.__new__(SourceService)
    service.engine_service = engine_service
    service.storage_adapter = storage_adapter
    service.database_name = "default"
    service._dispatch_retry_task = AsyncMock()
    return service, storage_adapter


@pytest.mark.asyncio
async def test_retry_refuses_when_source_paused() -> None:
    service, storage_adapter = _make_service(source_paused=True, system_paused=False)

    with pytest.raises(ConflictError) as exc_info:
        await service.retry_source("src1")

    assert "SOURCE_PAUSED" in str(exc_info.value.details)
    storage_adapter.reset_for_retry.assert_not_called()


@pytest.mark.asyncio
async def test_retry_refuses_when_system_paused() -> None:
    service, storage_adapter = _make_service(source_paused=False, system_paused=True)

    with pytest.raises(ConflictError) as exc_info:
        await service.retry_source("src1")

    assert "SYSTEM_PAUSED" in str(exc_info.value.details)
    storage_adapter.reset_for_retry.assert_not_called()


@pytest.mark.asyncio
async def test_retry_proceeds_when_unpaused() -> None:
    service, storage_adapter = _make_service(source_paused=False, system_paused=False)

    # Don't care about the response — only that reset_for_retry fired
    try:
        await service.retry_source("src1")
    except Exception:
        pass

    storage_adapter.reset_for_retry.assert_called_once()
