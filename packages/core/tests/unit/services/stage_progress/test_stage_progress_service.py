# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""StageProgress helper unit tests."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from chaoscypher_core.services.stage_progress import StageName, StageProgress


def _fake_storage() -> AsyncMock:
    """An AsyncMock implementing the four port methods. All return None."""
    mock = AsyncMock()
    mock.start_stage = AsyncMock(return_value=None)
    mock.tick_stage = AsyncMock(return_value=None)
    mock.complete_stage = AsyncMock(return_value=None)
    mock.update_stage_extras = AsyncMock(return_value=None)
    return mock


@pytest.mark.asyncio
async def test_context_manager_calls_start_and_complete() -> None:
    storage = _fake_storage()
    async with StageProgress(
        storage=storage,
        parent_id="src-1",
        stage=StageName.VISION,
        total=10,
    ) as _progress:
        pass
    storage.start_stage.assert_called_once()
    args = storage.start_stage.call_args.kwargs
    assert args["parent_id"] == "src-1"
    assert args["stage_name"] == "vision"
    assert args["total"] == 10
    assert isinstance(args["started_at"], datetime)
    storage.complete_stage.assert_called_once()
    storage.tick_stage.assert_not_called()


@pytest.mark.asyncio
async def test_tick_increments_processed() -> None:
    storage = _fake_storage()
    async with StageProgress(
        storage=storage,
        parent_id="src-1",
        stage=StageName.VISION,
        total=10,
    ) as progress:
        await progress.tick(duration_ms=1000)
        await progress.tick(duration_ms=2000)
    assert storage.tick_stage.call_count == 2
    assert storage.tick_stage.call_args_list[0].kwargs["processed"] == 1
    assert storage.tick_stage.call_args_list[1].kwargs["processed"] == 2


@pytest.mark.asyncio
async def test_tick_first_observation_is_avg() -> None:
    """First tick sets avg_ms = duration_ms (no EMA blending)."""
    storage = _fake_storage()
    async with StageProgress(
        storage=storage,
        parent_id="src-1",
        stage=StageName.VISION,
        total=10,
    ) as progress:
        await progress.tick(duration_ms=5000)
    assert storage.tick_stage.call_args.kwargs["avg_ms"] == 5000


@pytest.mark.asyncio
async def test_tick_subsequent_observations_apply_ema() -> None:
    """Second tick: avg = 0.3 * 1000 + 0.7 * 5000 = 300 + 3500 = 3800."""
    storage = _fake_storage()
    async with StageProgress(
        storage=storage,
        parent_id="src-1",
        stage=StageName.VISION,
        total=10,
    ) as progress:
        await progress.tick(duration_ms=5000)
        await progress.tick(duration_ms=1000)
    assert storage.tick_stage.call_args_list[1].kwargs["avg_ms"] == 3800


@pytest.mark.asyncio
async def test_tick_without_explicit_duration_uses_monotonic() -> None:
    """When duration_ms is omitted, the helper measures from monotonic clock."""
    storage = _fake_storage()
    async with StageProgress(
        storage=storage,
        parent_id="src-1",
        stage=StageName.VISION,
        total=10,
    ) as progress:
        await progress.tick()
    assert storage.tick_stage.call_args.kwargs["processed"] == 1
    # Sub-millisecond tick → duration_ms is 0 → EMA update is skipped per the
    # > 0 guard, so avg_ms stays None.
    assert storage.tick_stage.call_args.kwargs["avg_ms"] is None


@pytest.mark.asyncio
async def test_storage_failures_swallowed() -> None:
    """A raising storage doesn't propagate exceptions; the work continues."""
    storage = _fake_storage()
    storage.start_stage.side_effect = RuntimeError("DB blip")
    storage.tick_stage.side_effect = RuntimeError("DB blip")
    storage.complete_stage.side_effect = RuntimeError("DB blip")

    # Should NOT raise.
    async with StageProgress(
        storage=storage,
        parent_id="src-1",
        stage=StageName.VISION,
        total=3,
    ) as progress:
        await progress.tick(duration_ms=100)


@pytest.mark.asyncio
async def test_stage_name_enum_or_string_both_work() -> None:
    """Both StageName.VISION and the raw string 'vision' resolve to the same column value."""
    storage = _fake_storage()
    async with StageProgress(
        storage=storage,
        parent_id="src-1",
        stage="my_custom_stage",
        total=5,
    ) as _progress:
        pass
    assert storage.start_stage.call_args.kwargs["stage_name"] == "my_custom_stage"


def test_stage_name_enum_values() -> None:
    """The known stages have the expected string values."""
    assert StageName.VISION.value == "vision"
    assert StageName.EMBEDDING.value == "embedding"
    assert StageName.MCP_EXTRACTION.value == "mcp_extraction"


@pytest.mark.asyncio
async def test_complete_stage_called_even_on_body_exception() -> None:
    """When the body raises, complete_stage still fires in __aexit__.

    StageProgressStorageProtocol has no fail_stage method; the design
    choice is to mark the stage complete unconditionally rather than
    leave it in-progress forever in the UI. This test documents that.
    """
    storage = _fake_storage()
    with pytest.raises(ValueError, match="body failed"):
        async with StageProgress(
            storage=storage,
            parent_id="src-1",
            stage=StageName.VISION,
            total=5,
        ) as _progress:
            raise ValueError("body failed")
    storage.complete_stage.assert_called_once()
    storage.start_stage.assert_called_once()
