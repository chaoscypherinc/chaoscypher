# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Spend cap is enforced at the chunk extraction provider boundary.

P0 2026-05-21. Validates the integration between
``chaoscypher_core.operations.extraction.chunk_extraction_service
._extract_chunk_handler`` and
``chaoscypher_core.services.llm.spend.LLMSpendTracker``:

- When the per-source cap is reached the handler raises
  ``LLMSpendCapExceededError`` BEFORE ``AIEntityExtractor.extract_single_chunk``
  is called. The chunk is marked failed (no further billing).
- When the daily cap is reached the same pre-check fires; the
  source-scoped error is still raised but the scope is "day".
- When both caps are ``None`` (default) the handler proceeds normally
  and the extractor IS invoked. Default behavior is preserved.
- The post-call ``spend_tracker.record(...)`` runs only on success
  (so a rolled-back transaction does not double-count). Verified by
  asserting the tracker counter is zero when the pre-check rejects.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.operations.extraction.chunk_extraction_service import (
    ChunkExtractionOperationsService,
)
from chaoscypher_core.services.llm.spend import _reset_tracker_for_tests, get_llm_spend_tracker


_MODULE = "chaoscypher_core.operations.extraction.chunk_extraction_service"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_tracker():
    """Each test starts with a fresh process-wide tracker."""
    _reset_tracker_for_tests()
    yield
    _reset_tracker_for_tests()


def _make_settings(*, per_source: int | None = None, per_day: int | None = None) -> SimpleNamespace:
    """Minimal settings namespace satisfying _extract_chunk_handler reads."""
    llm = SimpleNamespace(
        chat_provider="openai",
        openai_extraction_model="gpt-4o",
        openai_chat_model="gpt-4o",
        token_cost_input_per_million=0.0,
        token_cost_output_per_million=0.0,
        ai_context_window=8192,
        max_tokens_per_source=per_source,
        max_tokens_per_day=per_day,
    )
    retries = SimpleNamespace(extraction_chunk_max=2)
    priorities = SimpleNamespace(background=10)
    source_recovery = SimpleNamespace(stream_heartbeat_min_interval_seconds=5.0)
    return SimpleNamespace(
        llm=llm,
        retries=retries,
        priorities=priorities,
        source_recovery=source_recovery,
    )


def _make_adapter() -> MagicMock:
    adapter = MagicMock()
    adapter.get_chunk_task.return_value = {
        "status": "queued",
        "retry_count": 0,
        "job_id": "job1",
        "chunk_index": 0,
    }
    adapter.get_extraction_job.return_value = {
        "id": "job1",
        "source_id": "src1",
        "status": "running",
        "extraction_config": None,
    }
    adapter.get_chunks_by_ids.return_value = [{"content": "test chunk content"}]
    adapter.get_source.return_value = None
    adapter.update_source_last_activity = MagicMock()
    adapter.complete_chunk_task_with_output = MagicMock(return_value=None)
    adapter.fail_chunk_task = MagicMock(return_value=None)

    # Dict-backed daily-spend store so the persisted daily cap path is real:
    # tracker.record() increments it and the handler's check reads it back.
    _daily: dict[tuple[str, str], int] = {}

    def _get_daily(*, database_name: str, spend_date: str) -> int:
        return _daily.get((database_name, spend_date), 0)

    def _add_daily(*, database_name: str, spend_date: str, tokens: int) -> None:
        if tokens <= 0:
            return
        key = (database_name, spend_date)
        _daily[key] = _daily.get(key, 0) + tokens

    adapter.get_daily_token_spend = MagicMock(side_effect=_get_daily)
    adapter.add_daily_token_spend = MagicMock(side_effect=_add_daily)

    @contextmanager
    def _noop_transaction():
        yield

    adapter.transaction = _noop_transaction
    return adapter


def _make_service(adapter: MagicMock) -> ChunkExtractionOperationsService:
    service = ChunkExtractionOperationsService.__new__(ChunkExtractionOperationsService)
    service.source_repository = adapter
    service.graph_repository = MagicMock()
    service.config_manager = MagicMock()
    service.llm_service = MagicMock()
    service._update_chunk_progress = AsyncMock(return_value=None)
    service.queue_extract_chunk = AsyncMock(return_value="task_requeued")
    return service


def _handler_data() -> dict[str, Any]:
    return {
        "chunk_task_id": "ct1",
        "job_id": "job1",
        "database_name": "db",
        "chunk_index": 0,
        "small_chunk_ids": ["sc1"],
    }


def _patches(settings: SimpleNamespace, *, extractor_call_recorder: list[str]):
    # Real ints/values on attributes the handler compares numerically.
    fake_engine_settings = SimpleNamespace(
        extraction=SimpleNamespace(empty_output_retry_min_chars=10_000),
        llm=SimpleNamespace(),
    )
    fake_engine_settings.model_copy = MagicMock(return_value=fake_engine_settings)
    fake_extractor = MagicMock()

    async def _record_extract_single_chunk(*args, **kwargs):
        extractor_call_recorder.append("extract_single_chunk")
        return (
            [],
            [],
            0,
            0,
            {
                "raw_llm_response": "",
                "_prompt_data": {},
                "sentences": [],
                "filtering_log": None,
                "invalid_relationship_count": 0,
            },
        )

    fake_extractor.extract_single_chunk = AsyncMock(side_effect=_record_extract_single_chunk)
    fake_extractor_cls = MagicMock(return_value=fake_extractor)

    return [
        patch(
            "chaoscypher_core.app_config.get_settings",
            MagicMock(return_value=settings),
        ),
        patch(
            "chaoscypher_core.app_config.engine_factory.build_engine_settings",
            MagicMock(return_value=fake_engine_settings),
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.utils.ai_entities.AIEntityExtractor",
            fake_extractor_cls,
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.utils.text_preparation.prepare_text_for_extraction",
            MagicMock(side_effect=lambda x: x),
        ),
        patch(
            "chaoscypher_core.operations.pause_guard.check_paused",
            MagicMock(return_value=SimpleNamespace(paused=False)),
        ),
        patch(
            "chaoscypher_core.analytics.llm_metrics.LLMMetricsCollector",
            MagicMock(return_value=MagicMock()),
        ),
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_source_cap_exceeded_raises_before_llm_call() -> None:
    """When per-source cap is reached, the extractor must NOT be invoked.

    Mocks the spend tracker so the existing source has already burned
    enough tokens to exceed a low cap. The chunk handler should fail
    the chunk and never call ``extract_single_chunk``.
    """
    settings = _make_settings(per_source=1000)
    adapter = _make_adapter()
    service = _make_service(adapter)
    tracker = get_llm_spend_tracker()
    tracker.record("src1", 5000, adapter=adapter, database_name="db")  # already over cap
    recorder: list[str] = []

    with (
        _patches(settings, extractor_call_recorder=recorder)[0],
        _patches(settings, extractor_call_recorder=recorder)[1],
        _patches(settings, extractor_call_recorder=recorder)[2],
        _patches(settings, extractor_call_recorder=recorder)[3],
        _patches(settings, extractor_call_recorder=recorder)[4],
        _patches(settings, extractor_call_recorder=recorder)[5],
    ):
        result = await service._extract_chunk_handler(_handler_data(), metadata=None, task_id="t1")

    # The handler caught the spend-cap error and routed through the
    # general failure path: fail_chunk_task called, no LLM call.
    assert "extract_single_chunk" not in recorder, "Spend cap pre-check failed to prevent LLM call"
    adapter.fail_chunk_task.assert_called_once()
    fail_kwargs = adapter.fail_chunk_task.call_args.kwargs
    assert fail_kwargs.get("error_type") == "LLMSpendCapExceededError"
    assert result["success"] is False


@pytest.mark.asyncio
async def test_daily_cap_exceeded_raises_before_llm_call() -> None:
    """Daily cap exceeded on an unrelated source still blocks new chunks."""
    settings = _make_settings(per_source=None, per_day=8000)
    adapter = _make_adapter()
    service = _make_service(adapter)
    tracker = get_llm_spend_tracker()
    # Spread spend across two sources so neither hits per-source but
    # the day total does.
    tracker.record("other-src", 4000, adapter=adapter, database_name="db")
    tracker.record("src1", 4000, adapter=adapter, database_name="db")
    recorder: list[str] = []

    patches = _patches(settings, extractor_call_recorder=recorder)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        result = await service._extract_chunk_handler(_handler_data(), metadata=None, task_id="t1")

    assert "extract_single_chunk" not in recorder, (
        "Daily spend cap pre-check failed to prevent LLM call"
    )
    adapter.fail_chunk_task.assert_called_once()
    assert result["success"] is False


@pytest.mark.asyncio
async def test_cap_disabled_passes_through_to_extractor() -> None:
    """Default ``None`` settings preserve the legacy uncapped behavior."""
    settings = _make_settings(per_source=None, per_day=None)
    adapter = _make_adapter()
    service = _make_service(adapter)
    recorder: list[str] = []

    patches = _patches(settings, extractor_call_recorder=recorder)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        result = await service._extract_chunk_handler(_handler_data(), metadata=None, task_id="t1")

    # Extractor was invoked exactly once; the chunk completed normally.
    assert recorder.count("extract_single_chunk") == 1
    adapter.fail_chunk_task.assert_not_called()
    assert result["success"] is True


@pytest.mark.asyncio
async def test_spend_cap_error_is_permanent_no_queue_retry() -> None:
    """The handler returns a dict (no raise) so the queue won't re-invoke.

    The chunk extraction handler catches LLMSpendCapExceededError and
    routes through ``_handle_chunk_failure``. Because
    ``is_retryable=False``, the failure path is direct: ``fail_chunk_task``
    is called and the handler returns a dict. The queue worker sees a
    normal return value — no exception propagates, so ``max_tries`` is
    irrelevant. (See queue/service.py:_execute_handler.)
    """
    settings = _make_settings(per_source=1000)
    adapter = _make_adapter()
    service = _make_service(adapter)
    tracker = get_llm_spend_tracker()
    tracker.record("src1", 5000, adapter=adapter, database_name="db")
    recorder: list[str] = []

    patches = _patches(settings, extractor_call_recorder=recorder)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        # Must NOT raise — the handler absorbs the spend-cap error.
        result = await service._extract_chunk_handler(_handler_data(), metadata=None, task_id="t1")

    assert result["success"] is False
    # And update_chunk_task was NEVER called to mark "queued" again
    # (which would have signalled an inline retry).
    adapter.update_chunk_task.assert_not_called()
    # Nor did we requeue.
    service.queue_extract_chunk.assert_not_awaited()
