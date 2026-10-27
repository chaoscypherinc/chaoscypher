# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A retried extraction group asks with another decoding, and says so (audit wave 5).

Under pinned decoding (temperature 0, seed 42) the same request gives the
same output, so the retry path sent an identical request and a deterministic
empty output repeated three times. The ladder (pipeline recommendations
§ 4b, extraction only): retry 1 asks at the same temperature with the next
seed, retry 2 at temperature 0.3 with a further seed and a repeat penalty
(1.15); every fallback is recorded on the call's metrics rows and on the
stored chunk result (``decode_fallback: {seed, temperature, step}``, plus
``repeat_penalty`` from rung 2 on), and a group kept from a rung counts
``llm_chunks_rescued_by_fallback`` on its source. These tests drive the queue's
real chunk handler and the real extractor against a fake provider that
records what each request sent, and check that a stream error is retried
through the ladder when the shared classifier says so (a 503) and not when
it says the model is missing (a 404).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_core.services.sources.engine.extraction.chunk_failures import (
    FALLBACK_TEMPERATURE,
    REPEAT_PENALTY,
    decode_fallback,
)
from chaoscypher_core.utils.decoding import effective_repeat_penalty, effective_seed
from tests.unit.services.sources.test_chunk_extraction_empty_output_retry import _make_adapter


_CHUNK = "Pierre arrived late at the soirée and stood by the window. " * 8


class _FakeProvider:
    """An extraction provider that records each request's seed and temperature."""

    def __init__(self, script: list[dict[str, Any]]) -> None:
        """``script``: the chunks every call streams (an error chunk, or content then done)."""
        self.script = script
        self.calls: list[tuple[int | None, float | None]] = []
        self.penalties: list[float | None] = []

    async def chat(self, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        """Record the request, then stream the script."""
        self.calls.append((effective_seed(42), kwargs.get("temperature")))
        self.penalties.append(effective_repeat_penalty())
        return self._stream()

    async def _stream(self) -> AsyncIterator[dict[str, Any]]:
        """The scripted chunks."""
        for chunk in self.script:
            yield chunk


def _settings(chat_provider: str = "ollama") -> MagicMock:
    """App settings with the pinned decoding and the spend caps off (``chat_provider``: the provider)."""
    settings = MagicMock()
    settings.source_recovery.stream_heartbeat_min_interval_seconds = 5.0
    settings.llm.ai_context_window = 8000
    settings.llm.chat_provider = chat_provider
    settings.llm.ollama_extraction_model = "qwen3"
    settings.llm.ollama_chat_model = "qwen3"
    settings.llm.max_tokens_per_source = None
    settings.llm.max_tokens_per_day = None
    settings.llm.seed = 42
    settings.llm.extraction_temperature = 0.0
    settings.retries.extraction_chunk_max = 3
    return settings


async def _run(
    provider: _FakeProvider,
    retry_count: int,
    rung: int | None = None,
    *,
    chunk: str = _CHUNK,
    chat_provider: str = "ollama",
) -> MagicMock:
    """One delivery of the chunk task at ``retry_count`` through the real handler and extractor.

    ``rung`` is the ladder step the failure handler stored on the task
    (``decode_fallback``); None (or 0) is the pinned decoding, whatever the
    retry count: only degenerate output climbs (audit wave 6). ``chunk`` is
    the group's text; ``chat_provider`` the provider the settings name.
    """
    from chaoscypher_core.operations.extraction import chunk_extraction_service as ces
    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        AIEntityExtractor,
    )
    from chaoscypher_core.settings import EngineSettings
    from chaoscypher_core.testing.embedding import FakeEmbeddingProvider

    adapter = _make_adapter(chunk)
    adapter.get_chunk_task.return_value = {
        "id": "ct-1",
        "job_id": "job-1",
        "status": "pending",
        "chunk_index": 0,
        "retry_count": retry_count,
        "decode_fallback": (
            decode_fallback(rung, seed=42, temperature=0.0).record() if rung else None  # type: ignore[union-attr]
        ),
    }
    with (
        patch.object(AIEntityExtractor, "_get_llm_provider", lambda self: provider),
        patch("chaoscypher_core.app_config.get_settings", return_value=_settings(chat_provider)),
        patch(
            "chaoscypher_core.app_config.engine_factory.build_engine_settings",
            return_value=EngineSettings(),
        ),
        # a kept chunk's entities are embedded for finalize; the default provider is the local model
        patch(
            "chaoscypher_core.repo_factories.get_embedding_service",
            return_value=FakeEmbeddingProvider(),
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
                "node_templates_formatted": "- Character: a person",
                "edge_templates_formatted": "- knows (Knows): acquainted",
            },
            metadata=None,
            task_id="t-1",
        )
    return adapter


def _requeued(adapter: MagicMock) -> bool:
    """The handler put the task back on the queue for another attempt."""
    return any(
        len(c.args) >= 2 and isinstance(c.args[1], dict) and c.args[1].get("status") == "queued"
        for c in adapter.update_chunk_task.call_args_list
    )


def test_the_ladder() -> None:
    """Attempt 0 pinned; retry 1 the next seed; retry 2 and later 0.3 and a further seed."""
    assert decode_fallback(0, seed=42, temperature=0.0) is None
    first = decode_fallback(1, seed=42, temperature=0.0)
    second = decode_fallback(2, seed=42, temperature=0.0)
    assert first is not None and first.record() == {"seed": 43, "temperature": 0.0, "step": 1}
    assert second is not None and second.record() == {
        "seed": 44,
        "temperature": 0.3,
        "step": 2,
        "repeat_penalty": 1.15,
    }
    assert first.repeat_penalty is None
    assert FALLBACK_TEMPERATURE == 0.3
    assert REPEAT_PENALTY == 1.15
    unseeded = decode_fallback(1, seed=None, temperature=0.5)
    assert unseeded is not None
    assert unseeded.record() == {"seed": None, "temperature": 0.5, "step": 1}


@pytest.mark.asyncio
async def test_a_deterministic_empty_output_is_asked_again_differently() -> None:
    """Three deliveries of an empty-output group send three different requests."""
    empty = [{"type": "done", "usage": {"prompt_tokens": 900, "completion_tokens": 0}}]
    sent = []
    for retry_count in (0, 1, 2):
        provider = _FakeProvider(empty)
        adapter = await _run(provider, retry_count, rung=retry_count)
        assert _requeued(adapter)
        # the requeue stores the next rung: an empty output is degenerate, so up by one
        stored = adapter.update_chunk_task.call_args.args[1]["decode_fallback"]
        assert stored["step"] == retry_count + 1
        sent.append(provider.calls)
    assert sent == [[(42, 0.0)], [(43, 0.0)], [(44, FALLBACK_TEMPERATURE)]]


_KEPT = "E|Pierre|Character||1.0|S1|A guest at the soirée\n"


def _kept_output() -> list[dict[str, Any]]:
    """A stream whose output parses to one entity (the group completes)."""
    return [
        {"type": "content", "delta": _KEPT},
        {
            "type": "done",
            "content": _KEPT,
            "usage": {"prompt_tokens": 900, "completion_tokens": 20},
            "finish_reason": "stop",
        },
    ]


def _counted(adapter: MagicMock) -> list[str]:
    """The source counter columns the handler incremented, in order."""
    return [c.kwargs["column"] for c in adapter.increment_source_counter.call_args_list]


@pytest.mark.asyncio
async def test_rung_two_sends_the_repeat_penalty_and_records_it() -> None:
    """Rung 1 sends no penalty; rung 2 sends REPEAT_PENALTY, on the task row and every metrics row."""
    first_rung = _FakeProvider(_kept_output())
    await _run(first_rung, retry_count=1, rung=1)
    assert first_rung.penalties and set(first_rung.penalties) == {None}

    second_rung = _FakeProvider(_kept_output())
    adapter = await _run(second_rung, retry_count=2, rung=2)
    # every call of the group (the entity and the relationship pass) sends it
    assert second_rung.penalties and set(second_rung.penalties) == {REPEAT_PENALTY}
    assert set(second_rung.calls) == {(44, FALLBACK_TEMPERATURE)}
    stored = adapter.complete_chunk_task_with_output.call_args.kwargs["decode_fallback"]
    assert stored["repeat_penalty"] == REPEAT_PENALTY
    rows = adapter.create_llm_call_metrics_batch.call_args.args[0]
    assert rows and all(r["decode_fallback"]["repeat_penalty"] == REPEAT_PENALTY for r in rows)

    pinned = _FakeProvider(_kept_output())
    await _run(pinned, retry_count=0)
    assert pinned.penalties and set(pinned.penalties) == {None}
    assert effective_repeat_penalty() is None  # nothing leaks out of the block


@pytest.mark.asyncio
async def test_a_group_kept_from_a_rung_counts_as_rescued() -> None:
    """The queue path counts ``llm_chunks_rescued_by_fallback`` for a rung, never for a pinned try."""
    rescued = await _run(_FakeProvider(_kept_output()), retry_count=1, rung=1)
    assert _counted(rescued).count("llm_chunks_rescued_by_fallback") == 1
    pinned = await _run(_FakeProvider(_kept_output()), retry_count=1)
    assert "llm_chunks_rescued_by_fallback" not in _counted(pinned)


@pytest.mark.asyncio
async def test_a_rescued_group_records_its_fallback() -> None:
    """The stored chunk result and every metrics row carry ``decode_fallback``."""
    content = "E|Pierre|Character||1.0|S1|A guest at the soirée\n"
    provider = _FakeProvider(
        [
            {"type": "content", "delta": content},
            {
                "type": "done",
                "content": content,
                "usage": {"prompt_tokens": 900, "completion_tokens": 20},
                "finish_reason": "stop",
            },
        ]
    )
    adapter = await _run(provider, retry_count=1, rung=1)
    assert adapter.complete_chunk_task_with_output.call_count == 1
    stored = adapter.complete_chunk_task_with_output.call_args.kwargs
    record = {"seed": 43, "temperature": 0.0, "step": 1}
    assert stored["decode_fallback"] == record
    rows = adapter.create_llm_call_metrics_batch.call_args.args[0]
    assert rows and all(r["decode_fallback"] == record for r in rows)
    first = await _run(_FakeProvider(provider.script), retry_count=0)
    assert first.complete_chunk_task_with_output.call_args.kwargs["decode_fallback"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "retried"),
    [(503, True), (404, False)],
)
async def test_a_stream_error_is_retried_only_when_the_classifier_says_so(
    status: int, retried: bool
) -> None:
    """A 503 chunk is requeued at the SAME decoding (a transport error is not degenerate); a 404 fails."""
    error = [{"type": "error", "error": "LLM streaming failed", "status_code": status}]
    provider = _FakeProvider(error)
    adapter = await _run(provider, retry_count=0)
    assert _requeued(adapter) is retried
    assert (adapter.fail_chunk_task.call_count == 1) is not retried
    if retried:
        assert adapter.update_chunk_task.call_args.args[1]["decode_fallback"] is None
        again = _FakeProvider(error)
        await _run(again, retry_count=1)
        assert again.calls == [(42, 0.0)]


# -- wave 6: the ladder climbs on degenerate output only ----------------------------------------


def test_only_degenerate_output_climbs_the_ladder() -> None:
    """A loop abort, an empty output or nothing parsed climbs; a 503 or a dropped stream does not."""
    from chaoscypher_core.exceptions import LLMRateLimitError, LLMServiceError
    from chaoscypher_core.services.sources.engine.extraction.chunk_failures import (
        DegenerateOutputError,
        degenerate_output_check,
        is_degenerate_failure,
        is_retryable_chunk_failure,
        next_ladder_step,
    )
    from chaoscypher_core.settings import EngineSettings

    def _check(tokens: int, metrics: dict[str, Any], *, last: bool = False) -> Any:
        """The one check on an output with no entity and no relationship."""
        return degenerate_output_check(
            output_tokens=tokens,
            entities=[],
            relationships=[],
            chunk_metrics=metrics,
            chunk_content=_CHUNK,
            engine_settings=EngineSettings(),
            provider="ollama",
            model="qwen3",
            last_attempt=last,
        )

    empty = _check(0, {})
    loop = _check(40, {"aborted_by_loop": True})
    unparsed = _check(40, {"parser_lines_dropped": 6})
    assert [e.kind for e in (empty, loop, unparsed)] == [
        "empty_output",
        "loop_abort",
        "unparseable",
    ]
    for degenerate in (empty, loop, unparsed):
        assert isinstance(degenerate, DegenerateOutputError)
        assert is_retryable_chunk_failure(degenerate)
        assert next_ladder_step(1, degenerate) == 2
    transport = [
        LLMServiceError(provider="ollama", reason="503 Service Unavailable"),
        LLMServiceError(provider="ollama", reason="connection dropped", is_timeout=True),
        LLMRateLimitError(provider="ollama"),
    ]
    for error in transport:
        assert not is_degenerate_failure(error)
        assert next_ladder_step(1, error) == 1
    assert next_ladder_step(0, None) == 0
    # an answer the parser read lines from is the model's (its entities were filtered)
    assert _check(40, {"aborted_by_loop": False, "parser_lines_parsed": 3}) is None
    assert _check(40, {"parser_lines_dropped": 0, "parser_lines_parsed": 1}) is None
    # nothing parsed and nothing dropped is unparseable too (JSON, a table: audit wave 8)
    assert _check(40, {"parser_lines_dropped": 0, "parser_lines_parsed": 0}).kind == "unparseable"
    # the last attempt keeps a loop abort's or an unparseable output; an empty one still fails
    assert _check(40, {"aborted_by_loop": True}, last=True) is None
    assert _check(40, {"parser_lines_dropped": 6}, last=True) is None
    assert _check(0, {}, last=True) is not None


def test_the_rung_is_read_from_the_recorded_fallback_not_the_retry_count() -> None:
    """``ladder_step`` reads a task's ``decode_fallback``; no fallback is rung 0."""
    from chaoscypher_core.services.sources.engine.extraction.chunk_failures import ladder_step

    assert ladder_step(None) == 0
    assert ladder_step({}) == 0
    assert ladder_step(decode_fallback(2, seed=42, temperature=0.0).record()) == 2  # type: ignore[union-attr]
    assert ladder_step({"seed": 43, "temperature": 0.0}) == 1  # written before the step was
