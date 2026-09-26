# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""StageProgress helper unit tests."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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
async def test_tick_n_records_a_whole_batch_in_one_write() -> None:
    """``tick(n=k)`` advances processed by k with a SINGLE storage write.

    Batch stages (the embedding wave) complete k units per storage round
    trip; ticking k times issued k UPDATEs + k COMMITs for one wave.
    """
    storage = _fake_storage()
    async with StageProgress(
        storage=storage,
        parent_id="src-1",
        stage=StageName.EMBEDDING,
        total=10,
    ) as progress:
        await progress.tick(n=4, duration_ms=8000)
        await progress.tick(n=6, duration_ms=12000)

    assert storage.tick_stage.call_count == 2
    assert storage.tick_stage.call_args_list[0].kwargs["processed"] == 4
    assert storage.tick_stage.call_args_list[1].kwargs["processed"] == 10


@pytest.mark.asyncio
async def test_tick_n_keeps_avg_ms_per_unit() -> None:
    """``avg_ms`` stays a PER-UNIT figure when a tick carries n units.

    Consumers multiply it by the remaining count for the ETA
    (``(total - processed) * avg_ms``), so charging a whole batch's
    wall-clock to one unit would inflate every estimate n-fold.
    """
    storage = _fake_storage()
    async with StageProgress(
        storage=storage,
        parent_id="src-1",
        stage=StageName.EMBEDDING,
        total=10,
    ) as progress:
        await progress.tick(n=4, duration_ms=8000)
    assert storage.tick_stage.call_args.kwargs["avg_ms"] == 2000


@pytest.mark.asyncio
async def test_tick_n_zero_or_negative_is_a_no_op() -> None:
    """A wave that embedded nothing writes nothing."""
    storage = _fake_storage()
    async with StageProgress(
        storage=storage,
        parent_id="src-1",
        stage=StageName.EMBEDDING,
        total=10,
    ) as progress:
        await progress.tick(n=0)
    storage.tick_stage.assert_not_called()


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
    # Drive the helper's clock so the elapsed span is a fixed 250 ms. Reading
    # the real clock would make the gap sub-millisecond, which collapses to
    # avg_ms=None — indistinguishable from the monotonic path not existing.
    ticks = iter([100.0, 100.25])
    fake_time = SimpleNamespace(monotonic=lambda: next(ticks))
    with patch("chaoscypher_core.services.stage_progress.service.time", fake_time):
        async with StageProgress(
            storage=storage,
            parent_id="src-1",
            stage=StageName.VISION,
            total=10,
        ) as progress:
            await progress.tick()
    assert storage.tick_stage.call_args.kwargs["processed"] == 1
    # 250 ms measured since stage start, and it IS the first EMA observation.
    assert storage.tick_stage.call_args.kwargs["avg_ms"] == 250


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
async def test_body_exception_skips_complete_stage() -> None:
    """When the body raises, __aexit__ must NOT stamp complete_stage.

    A failed stage recorded as cleanly completed lies to every consumer
    (UI progress, recovery heuristics). The 2026-07-23 decision: guard
    on ``exc_type is None`` — partial processed/total ticks persist, but
    the completed_at stamp only lands on a clean exit.
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
    storage.complete_stage.assert_not_called()
    storage.start_stage.assert_called_once()


@pytest.mark.asyncio
async def test_partial_ticks_persist_when_body_raises() -> None:
    """Ticks before the failure still reach storage — only completion is skipped."""
    storage = _fake_storage()

    async def _work_that_dies_mid_stage() -> None:
        async with StageProgress(
            storage=storage,
            parent_id="src-1",
            stage=StageName.VISION,
            total=5,
        ) as progress:
            await progress.tick(duration_ms=100)
            await progress.tick(duration_ms=100)
            raise RuntimeError("LLM died")

    with pytest.raises(RuntimeError, match="LLM died"):
        await _work_that_dies_mid_stage()

    assert storage.tick_stage.call_count == 2
    assert storage.tick_stage.call_args_list[1].kwargs["processed"] == 2
    storage.complete_stage.assert_not_called()
