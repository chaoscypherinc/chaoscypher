# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Regression: per-chunk completion is atomic across three writes.

Audit fix #H/core (complete_chunk transaction). If the third write
(complete_chunk_task_with_output) raises, the prior two writes
(persist_chunk_metrics, _store_job_prompts) must roll back so the
chunk-retry path replays from a clean slate without double-counting.

Three properties verified:
  A — All three SQLite writes happen inside one adapter.transaction() context.
  B — Stub: full rollback verification requires real SqliteAdapter fixtures;
      deferred to integration tests (DONE_WITH_CONCERNS note).
  C — track_tokens (Valkey) is NOT called when the SQLite transaction rolls back.
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


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_MODULE = "chaoscypher_core.operations.extraction.chunk_extraction_service"


def _make_settings() -> Any:
    """Minimal settings namespace that satisfies _extract_chunk_handler."""
    llm = SimpleNamespace(
        chat_provider="openai",
        openai_extraction_model="gpt-4o",
        openai_chat_model="gpt-4o",
        token_cost_input_per_million=0.0,
        token_cost_output_per_million=0.0,
        ai_context_window=8192,
        max_tokens_per_source=None,
        max_tokens_per_day=None,
    )
    retries = SimpleNamespace(extraction_chunk_max=2)
    priorities = SimpleNamespace(background=10)
    source_recovery = SimpleNamespace(stream_heartbeat_min_interval_seconds=5.0)
    return SimpleNamespace(
        llm=llm, retries=retries, priorities=priorities, source_recovery=source_recovery
    )


def _make_adapter(*, transaction_cm=None, complete_chunk_side_effect=None) -> MagicMock:
    """Build a MagicMock adapter for _extract_chunk_handler.

    Configures the minimal return values the handler reads before
    reaching the success block.
    """
    adapter = MagicMock()

    # Stale-task check: return a task in "queued" (not skipped)
    adapter.get_chunk_task.return_value = {
        "status": "queued",
        "retry_count": 0,
        "job_id": "job1",
        "chunk_index": 0,
    }

    # Job record
    adapter.get_extraction_job.return_value = {
        "id": "job1",
        "source_id": "src1",
        "status": "running",
        "extraction_config": None,
    }

    # Chunk text rehydration
    adapter.get_chunks_by_ids.return_value = [{"content": "test chunk content"}]

    # Pause guard: unpause
    adapter.get_source.return_value = None

    # Source activity update (heartbeat inside LLM call — sync method)
    adapter.update_source_last_activity = MagicMock()

    # complete_chunk_task_with_output: success or inject a failure
    if complete_chunk_side_effect is not None:
        adapter.complete_chunk_task_with_output = MagicMock(side_effect=complete_chunk_side_effect)
    else:
        adapter.complete_chunk_task_with_output = MagicMock(return_value=None)

    # transaction() context manager
    if transaction_cm is not None:
        adapter.transaction = transaction_cm
    else:
        # Default: plain no-op context manager
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


def _make_handler_data() -> dict[str, Any]:
    return {
        "chunk_task_id": "ct1",
        "job_id": "job1",
        "database_name": "db",
        "chunk_index": 0,
        "small_chunk_ids": ["sc1"],
    }


def _fake_extractor_result() -> tuple[list, list, int, int, dict]:
    """Return (entities, relationships, input_tokens, output_tokens, metrics).

    Shape matches what ``parse_extraction_output`` actually produces (see
    F47 / ``RawEntity`` / ``RawRelationship``) so the on-write Pydantic
    validator accepts it. A divergence here would land as a
    DataIntegrityError before the third-write atomicity check runs.
    """
    return (
        [
            {
                "name": "Entity One",
                "type": "Person",
                "description": "fake entity",
                "aliases": [],
                "confidence": 0.9,
                "sent_ref": "S1",
            }
        ],
        [
            {
                "source": 0,
                "target": 0,
                "type": "self_ref",
                "confidence": 0.5,
                "justification": "fake",
                "sent_ref": "S1",
            }
        ],
        100,
        50,
        {
            "raw_llm_response": '{"test": true}',
            "_prompt_data": {},
            "sentences": [],
            "filtering_log": None,
            "invalid_relationship_count": 0,
        },
    )


# ---------------------------------------------------------------------------
# Common patch stack: all heavy deps that _extract_chunk_handler imports
# lazily or at the top of its body.
# ---------------------------------------------------------------------------


def _common_patches(settings):
    """Return dict of patch target → mock for all heavy lazy imports in the handler.

    All imports inside _extract_chunk_handler are lazy (inside the function body),
    so we patch at the source module, not at chunk_extraction_service.
    """
    fake_engine_settings = MagicMock()

    fake_extractor = MagicMock()
    fake_extractor.extract_single_chunk = AsyncMock(return_value=_fake_extractor_result())
    fake_extractor_cls = MagicMock(return_value=fake_extractor)

    return {
        # get_settings is imported from chaoscypher_core.app_config inside the handler
        "chaoscypher_core.app_config.get_settings": MagicMock(return_value=settings),
        "chaoscypher_core.app_config.engine_factory.build_engine_settings": MagicMock(
            return_value=fake_engine_settings
        ),
        "chaoscypher_core.services.sources.engine.extraction.utils.ai_entities.AIEntityExtractor": fake_extractor_cls,
        "chaoscypher_core.services.sources.engine.extraction.utils.text_preparation.prepare_text_for_extraction": MagicMock(
            side_effect=lambda x: x
        ),
        "chaoscypher_core.operations.pause_guard.check_paused": MagicMock(
            return_value=SimpleNamespace(paused=False)
        ),
        # LLMMetricsCollector is also lazily imported
        "chaoscypher_core.analytics.llm_metrics.LLMMetricsCollector": MagicMock(
            return_value=MagicMock()
        ),
    }


# ---------------------------------------------------------------------------
# Property A: all three SQLite writes happen inside adapter.transaction()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_chunk_writes_run_inside_transaction() -> None:
    """All three SQLite writes happen inside one adapter.transaction() context.

    Property A: the recorded call order must be
    transaction_enter → persist_metrics → (index assignment) → store_prompts
    → complete_chunk → transaction_exit_commit.
    """
    call_order: list[str] = []

    @contextmanager
    def fake_transaction():
        call_order.append("transaction_enter")
        try:
            yield
            call_order.append("transaction_exit_commit")
        except Exception:
            call_order.append("transaction_exit_rollback")
            raise

    settings = _make_settings()
    adapter = _make_adapter(transaction_cm=fake_transaction)

    # Wrap persist_chunk_metrics to record calls
    adapter.complete_chunk_task_with_output = MagicMock(
        side_effect=lambda **kw: call_order.append("complete_chunk")
    )

    service = _make_service(adapter)
    data = _make_handler_data()

    def _record_persist(*args, **kwargs):
        call_order.append("persist_metrics")

    def _record_store_prompts(a, job_id, metrics):
        call_order.append("store_prompts")
        # Pop _prompt_data the same way the real function does
        metrics.pop("_prompt_data", None)

    p = _common_patches(settings)
    with (
        patch(f"{_MODULE}.persist_chunk_metrics", side_effect=_record_persist),
        patch(f"{_MODULE}._store_job_prompts", side_effect=_record_store_prompts),
        patch(
            "chaoscypher_core.app_config.get_settings",
            p["chaoscypher_core.app_config.get_settings"],
        ),
        patch(
            "chaoscypher_core.app_config.engine_factory.build_engine_settings",
            p["chaoscypher_core.app_config.engine_factory.build_engine_settings"],
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.utils.ai_entities.AIEntityExtractor",
            p[
                "chaoscypher_core.services.sources.engine.extraction.utils.ai_entities.AIEntityExtractor"
            ],
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.utils.text_preparation.prepare_text_for_extraction",
            p[
                "chaoscypher_core.services.sources.engine.extraction.utils.text_preparation.prepare_text_for_extraction"
            ],
        ),
        patch(
            "chaoscypher_core.operations.pause_guard.check_paused",
            p["chaoscypher_core.operations.pause_guard.check_paused"],
        ),
        patch(
            "chaoscypher_core.analytics.llm_metrics.LLMMetricsCollector",
            p["chaoscypher_core.analytics.llm_metrics.LLMMetricsCollector"],
        ),
    ):
        # Mirror the dispatcher's calling convention so signature drift
        # surfaces here too — see chaoscypher_core.queue.service._execute_handler.
        result = await service._extract_chunk_handler(
            data, metadata=None, task_id="test-tx-success"
        )

    assert result["success"] is True, f"Handler returned non-success: {result}"

    # Core assertion: the three writes are bracketed by the transaction
    assert "transaction_enter" in call_order, "transaction() was never entered"
    assert "transaction_exit_commit" in call_order, "transaction() was never committed"
    assert "transaction_exit_rollback" not in call_order, "transaction rolled back unexpectedly"

    tx_enter_idx = call_order.index("transaction_enter")
    tx_commit_idx = call_order.index("transaction_exit_commit")
    persist_idx = call_order.index("persist_metrics")
    store_idx = call_order.index("store_prompts")
    chunk_idx = call_order.index("complete_chunk")

    assert tx_enter_idx < persist_idx, "persist_metrics before transaction_enter"
    assert tx_enter_idx < store_idx, "store_prompts before transaction_enter"
    assert tx_enter_idx < chunk_idx, "complete_chunk before transaction_enter"
    assert persist_idx < tx_commit_idx, "persist_metrics after transaction_exit_commit"
    assert store_idx < tx_commit_idx, "store_prompts after transaction_exit_commit"
    assert chunk_idx < tx_commit_idx, "complete_chunk after transaction_exit_commit"


# ---------------------------------------------------------------------------
# Property C: track_tokens NOT called when SQLite transaction rolls back
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_track_tokens_skipped_when_sqlite_writes_rollback() -> None:
    """Valkey token bump must not run if the SQLite transaction rolls back.

    Property C: patch track_tokens, force complete_chunk_task_with_output to
    raise, drive the handler, assert track_tokens was never awaited.
    """
    track_tokens_mock = AsyncMock()

    settings = _make_settings()
    adapter = _make_adapter(complete_chunk_side_effect=RuntimeError("simulated sqlite failure"))

    service = _make_service(adapter)
    data = _make_handler_data()

    def _noop_store_prompts(a, job_id, metrics):
        metrics.pop("_prompt_data", None)

    p = _common_patches(settings)
    with (
        patch(f"{_MODULE}.persist_chunk_metrics"),
        patch(f"{_MODULE}._store_job_prompts", side_effect=_noop_store_prompts),
        patch(
            "chaoscypher_core.app_config.get_settings",
            p["chaoscypher_core.app_config.get_settings"],
        ),
        patch(
            "chaoscypher_core.app_config.engine_factory.build_engine_settings",
            p["chaoscypher_core.app_config.engine_factory.build_engine_settings"],
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.utils.ai_entities.AIEntityExtractor",
            p[
                "chaoscypher_core.services.sources.engine.extraction.utils.ai_entities.AIEntityExtractor"
            ],
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.utils.text_preparation.prepare_text_for_extraction",
            p[
                "chaoscypher_core.services.sources.engine.extraction.utils.text_preparation.prepare_text_for_extraction"
            ],
        ),
        patch(
            "chaoscypher_core.operations.pause_guard.check_paused",
            p["chaoscypher_core.operations.pause_guard.check_paused"],
        ),
        patch(
            "chaoscypher_core.analytics.llm_metrics.LLMMetricsCollector",
            p["chaoscypher_core.analytics.llm_metrics.LLMMetricsCollector"],
        ),
        # Patch track_tokens at the module level where it is imported lazily
        patch("chaoscypher_core.queue.queue_client.track_tokens", track_tokens_mock),
    ):
        # Dispatcher-style call — see _execute_handler.
        result = await service._extract_chunk_handler(
            data, metadata=None, task_id="test-tx-rollback"
        )

    # The handler catches Exception and routes to _handle_chunk_failure,
    # so the result should be success=False
    assert result.get("success") is False, f"Expected success=False after rollback, got: {result}"

    # The core invariant: Valkey was NOT bumped for tokens
    track_tokens_mock.assert_not_awaited()
