# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the pause API endpoints.

Follows the existing cortex pattern (see test_queue_api_reconcile.py)
of calling endpoint functions directly with a mocked service rather
than spinning up a TestClient. Much faster and simpler.
"""

from unittest.mock import AsyncMock

import pytest

from chaoscypher_cortex.features.pause.api import (
    bulk_pause_endpoint,
    bulk_resume_endpoint,
    pause_source_endpoint,
    resume_source_endpoint,
    system_pause_endpoint,
    system_resume_endpoint,
    system_status_endpoint,
)
from chaoscypher_cortex.features.pause.models import (
    BulkPauseActionResponse,
    BulkPauseRequest,
    BulkResumeRequest,
    PauseSourceRequest,
    PauseSystemRequest,
    SourcePauseActionResponse,
    SystemPauseActionResponse,
    SystemPauseStatusResponse,
)


_FAKE_USER: dict = {"id": "admin"}


@pytest.mark.asyncio
async def test_pause_source_endpoint_delegates() -> None:
    service = AsyncMock()

    result = await pause_source_endpoint(
        source_id="s-1",
        _="test-user",
        request=PauseSourceRequest(reason="manual"),
        service=service,
    )

    assert result == SourcePauseActionResponse(source_id="s-1", paused=True)
    service.pause_source.assert_awaited_once_with(
        source_id="s-1", database_name="default", reason="manual"
    )


@pytest.mark.asyncio
async def test_resume_source_endpoint_delegates() -> None:
    service = AsyncMock()

    result = await resume_source_endpoint(
        source_id="s-1",
        _="test-user",
        service=service,
    )

    assert result == SourcePauseActionResponse(source_id="s-1", paused=False)
    service.resume_source.assert_awaited_once_with(source_id="s-1", database_name="default")


@pytest.mark.asyncio
async def test_bulk_pause_endpoint_returns_count() -> None:
    service = AsyncMock()
    service.pause_sources = AsyncMock(return_value=3)

    result = await bulk_pause_endpoint(
        _="test-user",
        request=BulkPauseRequest(source_ids=["a", "b", "c"], reason="bulk"),
        service=service,
    )
    assert result == BulkPauseActionResponse(count=3)
    service.pause_sources.assert_awaited_once_with(
        source_ids=["a", "b", "c"],
        database_name="default",
        reason="bulk",
    )


@pytest.mark.asyncio
async def test_bulk_resume_endpoint_returns_count() -> None:
    service = AsyncMock()
    service.resume_sources = AsyncMock(return_value=2)

    result = await bulk_resume_endpoint(
        _="test-user",
        request=BulkResumeRequest(source_ids=["a", "b"]),
        service=service,
    )
    assert result == BulkPauseActionResponse(count=2)
    service.resume_sources.assert_awaited_once_with(source_ids=["a", "b"], database_name="default")


@pytest.mark.asyncio
async def test_system_pause_endpoint() -> None:
    service = AsyncMock()

    result = await system_pause_endpoint(
        _="test-user",
        request=PauseSystemRequest(reason="deploy"),
        service=service,
    )
    assert result == SystemPauseActionResponse(paused=True)
    service.pause_system.assert_awaited_once_with(reason="deploy")


@pytest.mark.asyncio
async def test_system_resume_endpoint() -> None:
    service = AsyncMock()

    result = await system_resume_endpoint(
        _="test-user",
        service=service,
    )
    assert result == SystemPauseActionResponse(paused=False)
    service.resume_system.assert_awaited_once()


@pytest.mark.asyncio
async def test_system_status_endpoint_returns_typed_response() -> None:
    service = AsyncMock()
    service.get_system_status = AsyncMock(
        return_value={
            "paused": True,
            "paused_at": None,
            "reason": "test",
        }
    )

    result = await system_status_endpoint(
        _="test-user",
        service=service,
    )
    assert isinstance(result, SystemPauseStatusResponse)
    assert result.paused is True
    assert result.reason == "test"
