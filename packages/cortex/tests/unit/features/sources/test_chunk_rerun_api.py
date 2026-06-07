# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for chunk_rerun_api endpoint.

Mirrors the vision_pages retry endpoint test style: call the endpoint
coroutine directly with a mocked service.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from chaoscypher_core.exceptions import ConflictError, NotFoundError
from chaoscypher_cortex.features.sources.chunk_rerun_api import (
    rerun_chunk_endpoint,
)


@pytest.mark.asyncio
async def test_rerun_chunk_endpoint_returns_202_body() -> None:
    service = AsyncMock()
    service.rerun_chunk = AsyncMock(
        return_value={
            "chunk_task_id": "tsk-1",
            "queue_task_id": "qt-1",
            "attempt_number": 1,
            "source_status": "extracting",
        }
    )

    resp = await rerun_chunk_endpoint(
        source_id="src-1",
        chunk_index=0,
        _="user",
        service=service,
    )

    assert resp.chunk_task_id == "tsk-1"
    assert resp.queue_task_id == "qt-1"
    assert resp.attempt_number == 1
    assert resp.source_status == "extracting"
    service.rerun_chunk.assert_awaited_once_with(source_id="src-1", chunk_index=0)


@pytest.mark.asyncio
async def test_rerun_chunk_endpoint_propagates_not_found() -> None:
    service = AsyncMock()
    service.rerun_chunk = AsyncMock(side_effect=NotFoundError("source", "x"))

    with pytest.raises(NotFoundError):
        await rerun_chunk_endpoint(
            source_id="x",
            chunk_index=0,
            _="user",
            service=service,
        )


@pytest.mark.asyncio
async def test_rerun_chunk_endpoint_propagates_conflict() -> None:
    service = AsyncMock()
    service.rerun_chunk = AsyncMock(side_effect=ConflictError("committing"))

    with pytest.raises(ConflictError):
        await rerun_chunk_endpoint(
            source_id="src-1",
            chunk_index=0,
            _="user",
            service=service,
        )
