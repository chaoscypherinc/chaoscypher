# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Empty-LLM-output retry contract for chunk extraction.

A chunk handler that gets an empty extraction result on a non-trivial
chunk must treat that as a retryable transient failure, not a silent
success. Otherwise the model occasionally glitching (qwen3 reasoning
mode burning the entire output budget; gemini RECITATION soft-stops;
ollama transient stream resets) lands the chunk in ``status=completed``
with ``raw_entities=[]`` — visually indistinguishable from "no
extractable entities" — and the source quietly under-extracts with no
signal to the user.

Observed concretely on war_and_peace chunk 0 (3,330 chars of real prose,
116s LLM call, 0 output_tokens, empty pass-1 + pass-2 bodies). One in
111 chunks; not rare enough to ignore.

Contract pinned by these tests:

1. When ``output_tokens == 0`` AND ``len(chunk_content) >= 200``, the
   handler raises a retryable exception that the existing retry path
   re-queues for another attempt. (Up to ``retries.extraction_chunk_max``
   tries before failing permanently.)

2. When ``output_tokens == 0`` AND ``len(chunk_content) < 200``, the
   handler accepts the empty result. (Tiny fragments may legitimately
   have nothing to extract.)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest


def _make_adapter(
    chunk_content: str,
    *,
    chunk_task_id: str = "ct-1",
    job_id: str = "job-1",
    source_id: str = "src-1",
) -> MagicMock:
    """Build a MagicMock adapter wired for the chunk-extraction handler.

    The handler reads job/task/source/chunks during normal flow; failures
    on retry path call back into the adapter to update task state.
    """
    adapter = MagicMock()
    adapter.update_source_last_activity = MagicMock()
    adapter.get_extraction_job = MagicMock(
        return_value={
            "id": job_id,
            "status": "running",
            "source_id": source_id,
            "extraction_config": None,
            "completed_chunks": 0,
            "failed_chunks": 0,
            "total_chunks": 1,
        }
    )
    adapter.get_chunk_task = MagicMock(
        return_value={
            "id": chunk_task_id,
            "job_id": job_id,
            "status": "pending",
            "chunk_index": 0,
            "retry_count": 0,
        }
    )
    adapter.get_chunks_by_ids = MagicMock(return_value=[{"content": chunk_content}])
    adapter.get_source = MagicMock(return_value={"id": source_id, "is_paused": False})
    adapter.get_system_state = MagicMock(return_value=None)
    adapter.start_chunk_task_with_input = MagicMock()
    adapter.complete_chunk_task_with_output = MagicMock()
    adapter.update_extraction_job_progress = MagicMock()
    adapter.update_chunk_task = MagicMock()
    adapter.fail_chunk_task = MagicMock()
    adapter.enqueue_chunk_extraction_task = MagicMock()
    # Concrete dict so ``progress["is_terminal"]`` is False — keeps the
    # handler from queueing finalization (which would hit Valkey).
    adapter.increment_job_completed_and_check = MagicMock(
        return_value={"completed": 0, "failed": 0, "total": 1, "is_terminal": False}
    )
    adapter.update_step_progress = MagicMock()
    return adapter


def _make_settings() -> MagicMock:
    settings = MagicMock()
    settings.source_recovery.stream_heartbeat_min_interval_seconds = 5.0
    settings.llm.ai_context_window = 8000
    settings.llm.token_cost_input_per_million = 0.0
    settings.llm.token_cost_output_per_million = 0.0
    settings.llm.chat_provider = "ollama"
    settings.llm.ollama_extraction_model = "qwen3"
    settings.llm.ollama_chat_model = "qwen3"
    # Spend cap inputs must be real values (or None) — MagicMock defaults
    # break the int-vs-attr comparison in services/llm/spend.py.
    settings.llm.max_tokens_per_source = None
    settings.llm.max_tokens_per_day = None
    settings.retries.extraction_chunk_max = 3
    return settings


def _empty_extractor() -> type:
    """Build a fake AIEntityExtractor whose extract_single_chunk returns empty."""

    class _FakeExtractor:
        def __init__(self, *_a: Any, **_kw: Any) -> None:
            pass

        async def extract_single_chunk(
            self, *_a: Any, **_kw: Any
        ) -> tuple[list, list, int, int, dict]:
            # Simulates the qwen3 reasoning-mode glitch: the LLM consumed
            # input tokens but emitted zero output content / tokens.
            return ([], [], 2553, 0, {"raw_llm_response": ""})

    return _FakeExtractor


@pytest.mark.asyncio
async def test_empty_output_on_substantive_chunk_triggers_retry() -> None:
    """A 200+-char chunk with 0 output_tokens must requeue for retry.

    The contract: handler raises a retryable exception; the existing
    retry path catches it and updates the chunk task to status='queued'
    with retry_count incremented.
    """
    from chaoscypher_core.operations.extraction import chunk_extraction_service as ces

    chunk_content = "Real War-and-Peace prose. " * 20  # 540 chars
    assert len(chunk_content) >= 200
    adapter = _make_adapter(chunk_content)
    settings = _make_settings()

    engine_settings_mock = MagicMock()
    engine_settings_mock.extraction.empty_output_retry_min_chars = 200

    with (
        patch(
            "chaoscypher_core.services.sources.engine.extraction.utils.ai_entities.AIEntityExtractor",
            _empty_extractor(),
        ),
        patch("chaoscypher_core.app_config.get_settings", return_value=settings),
        patch(
            "chaoscypher_core.app_config.engine_factory.build_engine_settings",
            return_value=engine_settings_mock,
        ),
    ):
        service = ces.ChunkExtractionOperationsService(source_repository=adapter)
        # Mirror the dispatcher's calling convention so signature drift surfaces
        # here too — see chaoscypher_core.queue.service._execute_handler.
        result = await service._extract_chunk_handler(
            data={
                "chunk_task_id": "ct-1",
                "job_id": "job-1",
                "database_name": "default",
                "chunk_content": "",
                "chunk_index": 0,
                "small_chunk_ids": ["sc-1"],
            },
            metadata=None,
            task_id="test-empty-output-retry",
        )

    # The retry path requeues via update_chunk_task(status='queued', retry_count=1)
    requeue_calls = [
        call
        for call in adapter.update_chunk_task.call_args_list
        if len(call.args) >= 2
        and isinstance(call.args[1], dict)
        and call.args[1].get("status") == "queued"
    ]
    assert len(requeue_calls) == 1, (
        f"Expected 1 requeue (status='queued') after empty-output retry, "
        f"got {len(requeue_calls)} from update_chunk_task calls: "
        f"{adapter.update_chunk_task.call_args_list}"
    )
    update = requeue_calls[0].args[1]
    assert update["retry_count"] == 1, f"Expected retry_count=1, got {update['retry_count']}"
    # The chunk must NOT have been persisted as a successful completion.
    assert adapter.complete_chunk_task_with_output.call_count == 0, (
        "complete_chunk_task_with_output called despite empty LLM output — "
        "would silently persist 0 entities as a successful chunk."
    )
    # The handler signals failure (not success) for the empty-output case.
    assert result.get("success") is False, (
        f"Expected success=False on empty-output retry, got {result}"
    )


@pytest.mark.asyncio
async def test_empty_output_on_tiny_chunk_accepts_as_completed() -> None:
    """A <200-char chunk with 0 output_tokens is accepted as completed.

    Below the threshold, the LLM legitimately may have nothing to
    extract; we don't waste retries on those.
    """
    from chaoscypher_core.operations.extraction import chunk_extraction_service as ces

    chunk_content = "Tiny fragment."  # 14 chars
    assert len(chunk_content) < 200
    adapter = _make_adapter(chunk_content)
    settings = _make_settings()

    engine_settings_mock = MagicMock()
    engine_settings_mock.extraction.empty_output_retry_min_chars = 200

    with (
        patch(
            "chaoscypher_core.services.sources.engine.extraction.utils.ai_entities.AIEntityExtractor",
            _empty_extractor(),
        ),
        patch("chaoscypher_core.app_config.get_settings", return_value=settings),
        patch(
            "chaoscypher_core.app_config.engine_factory.build_engine_settings",
            return_value=engine_settings_mock,
        ),
    ):
        service = ces.ChunkExtractionOperationsService(source_repository=adapter)
        # Dispatcher-style call.
        await service._extract_chunk_handler(
            data={
                "chunk_task_id": "ct-1",
                "job_id": "job-1",
                "database_name": "default",
                "chunk_content": "",
                "chunk_index": 0,
                "small_chunk_ids": ["sc-1"],
            },
            metadata=None,
            task_id="test-tiny-chunk-no-retry",
        )

    # Handler should NOT have requeued for retry.
    requeue_calls = [
        call
        for call in adapter.update_chunk_task.call_args_list
        if len(call.args) >= 2
        and isinstance(call.args[1], dict)
        and call.args[1].get("status") == "queued"
    ]
    assert len(requeue_calls) == 0, (
        f"Below-threshold empty-output chunk should not retry, but "
        f"observed {len(requeue_calls)} requeue calls."
    )
    # The chunk SHOULD have been persisted as a normal (empty) completion.
    assert adapter.complete_chunk_task_with_output.call_count == 1, (
        "Expected one complete_chunk_task_with_output call for the tiny chunk."
    )
