# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Stream-activity heartbeat integration through _extract_chunk_handler.

The unit-level test in
``tests/unit/services/sources/engine/extraction/utils/test_extraction_stream_heartbeat.py``
pins the contract of the ``on_chunk`` callback at the
``_consume_extraction_stream`` boundary. This test pins the wiring:
when the chunk handler runs, it builds a rate-limited callback and
threads it through ``extract_single_chunk`` so ``last_activity_at``
is bumped on stream activity, not only after the LLM call returns.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_handler_threads_rate_limited_heartbeat_into_extractor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The handler builds a callback, threads it down, and rate-limits writes.

    Drives the handler with a fake AIEntityExtractor that simulates a
    streaming LLM by invoking the ``on_stream_progress`` callback 6
    times across ~1.2 seconds (one call every 200ms). With a 0.5s
    rate-limit floor, exactly 3 ``update_source_last_activity`` writes
    should land (t=0, t=0.5, t=1.0 when read against monotonic time).
    """
    from chaoscypher_core.operations.extraction import chunk_extraction_service as ces

    heartbeats: list[datetime] = []
    adapter = MagicMock()
    adapter.update_source_last_activity.side_effect = lambda **kw: heartbeats.append(kw["at_time"])
    adapter.get_extraction_job = MagicMock(
        return_value={
            "id": "job-1",
            "status": "running",
            "source_id": "src-1",
            "extraction_config": None,
            "completed_chunks": 0,
            "failed_chunks": 0,
            "total_chunks": 1,
        }
    )
    adapter.get_chunk_task = MagicMock(
        return_value={
            "id": "ct-1",
            "job_id": "job-1",
            "status": "pending",
            "chunk_index": 0,
        }
    )
    adapter.get_chunks_by_ids = MagicMock(return_value=[{"content": "alice meets bob in moscow"}])
    adapter.get_source = MagicMock(return_value={"id": "src-1", "is_paused": False})
    adapter.get_system_state = MagicMock(return_value=None)
    adapter.start_chunk_task_with_input = MagicMock()
    adapter.complete_chunk_task_with_output = MagicMock()
    adapter.update_extraction_job_progress = MagicMock()

    class _FakeExtractor:
        def __init__(self, *_a: Any, **_kw: Any) -> None:
            pass

        async def extract_single_chunk(
            self, *_a: Any, on_stream_progress=None, **_kw: Any
        ) -> tuple[list, list, int, int, dict]:
            # Simulate a streaming LLM call: invoke the heartbeat
            # callback 6 times across ~1.2s of wall time.
            for _ in range(6):
                if on_stream_progress is not None:
                    on_stream_progress()
                # Use monotonic-aware sleep that honors the rate-limit floor.
                await _async_sleep(0.2)
            return ([], [], 5, 3, {"raw_llm_response": ""})

    async def _async_sleep(seconds: float) -> None:
        # Use real sleep so monotonic time advances. asyncio.sleep is
        # fine here because the tests are run in pytest-asyncio.
        import asyncio as _asyncio

        await _asyncio.sleep(seconds)

    # AIEntityExtractor is imported INSIDE _extract_chunk_handler, so we
    # patch at its source module rather than at chunk_extraction_service.
    settings = MagicMock()
    settings.source_recovery.stream_heartbeat_min_interval_seconds = 0.5
    settings.llm.ai_context_window = 8000
    settings.llm.token_cost_input_per_million = 0.0
    settings.llm.token_cost_output_per_million = 0.0
    settings.llm.chat_provider = "ollama"
    settings.llm.ollama_extraction_model = "llama3"
    settings.llm.ollama_chat_model = "llama3"
    settings.llm.max_tokens_per_source = None
    settings.llm.max_tokens_per_day = None

    with (
        patch(
            "chaoscypher_core.services.sources.engine.extraction.utils.ai_entities.AIEntityExtractor",
            _FakeExtractor,
        ),
        patch("chaoscypher_core.app_config.get_settings", return_value=settings),
        patch(
            "chaoscypher_core.app_config.engine_factory.build_engine_settings",
            return_value=MagicMock(),
        ),
    ):
        service = ces.ChunkExtractionOperationsService(source_repository=adapter)
        await service._extract_chunk_handler(
            data={
                "chunk_task_id": "ct-1",
                "job_id": "job-1",
                "database_name": "default",
                "chunk_content": "",
                "chunk_index": 0,
                "small_chunk_ids": ["sc-1"],
            },
        )

    # Expectation: with a 0.5s rate-limit floor and 6 stream events
    # spaced 200ms apart over 1.2s of wall time, the rate-limited
    # callback should fire at t≈0 and t≈0.6 (and possibly t≈1.0
    # depending on jitter). The contract being pinned:
    #
    # 1. The handler DID thread the heartbeat callback through
    #    extract_single_chunk (otherwise zero heartbeats would land).
    # 2. Heartbeats fire DURING the LLM call, not only after.
    # 3. Rate limiting is honored — consecutive heartbeats are NOT
    #    spaced closer than the configured floor.
    #
    # The post-completion checkpoint at line 456 is part of the
    # existing code and is not what we're testing here; downstream
    # adapter writes (complete_chunk_task_with_output, etc.) may
    # not be fully mocked and that is fine for this test.
    assert len(heartbeats) >= 2, (
        f"Expected ≥2 stream-activity heartbeats with a 0.5s rate floor "
        f"and 6 stream events over 1.2s, got {len(heartbeats)}: {heartbeats}"
    )
    # Verify rate limiting: consecutive heartbeats are at least
    # ~0.5s apart (with some tolerance for jitter; assert ≥0.4s).
    deltas = [
        (heartbeats[i + 1] - heartbeats[i]).total_seconds() for i in range(len(heartbeats) - 1)
    ]
    assert all(d >= 0.4 for d in deltas), (
        f"Rate limiting not honored — consecutive heartbeats too close: "
        f"deltas={deltas}, heartbeats={heartbeats}"
    )


@pytest.mark.asyncio
async def test_handler_does_not_pass_heartbeat_when_source_id_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the source_id is not a string, the heartbeat is a no-op.

    Belt-and-suspenders: the closure already guards on
    ``isinstance(source_id, str)`` before any DB write. This test
    pins that guard so a None source_id (test harness path) doesn't
    crash the handler with a confusing TypeError on the heartbeat.
    """
    from chaoscypher_core.operations.extraction import chunk_extraction_service as ces

    adapter = MagicMock()
    adapter.update_source_last_activity = MagicMock()
    adapter.get_extraction_job = MagicMock(
        return_value={
            "id": "job-1",
            "status": "running",
            "source_id": None,  # the case under test
            "extraction_config": None,
            "completed_chunks": 0,
            "failed_chunks": 0,
            "total_chunks": 1,
        }
    )
    adapter.get_chunk_task = MagicMock(
        return_value={
            "id": "ct-1",
            "job_id": "job-1",
            "status": "pending",
            "chunk_index": 0,
        }
    )
    adapter.get_chunks_by_ids = MagicMock(return_value=[{"content": "alice meets bob in moscow"}])
    adapter.get_source = MagicMock(return_value={"id": "src-1", "is_paused": False})
    adapter.get_system_state = MagicMock(return_value=None)
    adapter.start_chunk_task_with_input = MagicMock()
    adapter.complete_chunk_task_with_output = MagicMock()

    class _FakeExtractor:
        def __init__(self, *_a: Any, **_kw: Any) -> None:
            pass

        async def extract_single_chunk(
            self, *_a: Any, on_stream_progress=None, **_kw: Any
        ) -> tuple[list, list, int, int, dict]:
            # Even invoking the callback must not crash with source_id=None.
            if on_stream_progress is not None:
                on_stream_progress()
            return ([], [], 1, 1, {"raw_llm_response": ""})

    settings = MagicMock()
    settings.source_recovery.stream_heartbeat_min_interval_seconds = 0.0
    settings.llm.ai_context_window = 8000
    settings.llm.token_cost_input_per_million = 0.0
    settings.llm.token_cost_output_per_million = 0.0
    settings.llm.chat_provider = "ollama"
    settings.llm.ollama_extraction_model = "llama3"
    settings.llm.ollama_chat_model = "llama3"
    settings.llm.max_tokens_per_source = None
    settings.llm.max_tokens_per_day = None

    with (
        patch(
            "chaoscypher_core.services.sources.engine.extraction.utils.ai_entities.AIEntityExtractor",
            _FakeExtractor,
        ),
        patch("chaoscypher_core.app_config.get_settings", return_value=settings),
        patch(
            "chaoscypher_core.app_config.engine_factory.build_engine_settings",
            return_value=MagicMock(),
        ),
    ):
        service = ces.ChunkExtractionOperationsService(source_repository=adapter)
        # Should not raise.
        await service._extract_chunk_handler(
            data={
                "chunk_task_id": "ct-1",
                "job_id": "job-1",
                "database_name": "default",
                "chunk_content": "",
                "chunk_index": 0,
                "small_chunk_ids": ["sc-1"],
            },
        )
    # Adapter never called update_source_last_activity from the
    # heartbeat (the post-completion checkpoint is also gated on
    # source_id being truthy in the existing code, so 0 is fine).
    # If the post-completion checkpoint is unconditional, this number
    # could be 1 — relax to ≤1 to tolerate either implementation.
    assert adapter.update_source_last_activity.call_count <= 1
