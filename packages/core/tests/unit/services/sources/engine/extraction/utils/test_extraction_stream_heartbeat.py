# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Stream-activity heartbeat for chunk extraction.

Pins the contract that ``_consume_extraction_stream`` invokes an
optional ``on_chunk`` callback on every received content chunk so the
caller can use stream activity (not wall-clock time) as the
liveness signal for SourceRecovery's stall threshold.

The wall-clock SourceHeartbeat context manager wraps every other
long-running handler, but the chunk extraction LLM call streams
pipe-delimited rows token-by-token (E|name|type|...) and the most
accurate liveness signal is "tokens are still arriving" — a hung TCP
connection where the asyncio task is alive but the LLM has stopped
emitting tokens correctly stops firing this callback, which a
wall-clock heartbeat would mask.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
    _consume_extraction_stream,
    _StreamLoopDetector,
)
from chaoscypher_core.settings import ExtractionSettings


def _detector() -> _StreamLoopDetector:
    return _StreamLoopDetector(extraction_cfg=ExtractionSettings())


async def _content_stream(
    deltas: list[str], *, include_done: bool = True
) -> AsyncIterator[dict[str, Any]]:
    """Yield content chunks then a done chunk, mirroring the provider shape."""
    for d in deltas:
        yield {"type": "content", "delta": d}
    if include_done:
        yield {
            "type": "done",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "content": "".join(deltas),
        }


@pytest.mark.asyncio
async def test_on_chunk_fires_for_every_content_chunk() -> None:
    """Six content chunks → six callback invocations."""
    deltas = [
        "E|Alice|Character|||S1|first\n",
        "E|Bob|Character|||S1|second\n",
        "E|Carol|Character|||S2|third\n",
        "R|0|1|knows|0.9|S1|alice knows bob\n",
        "R|1|2|knows|0.9|S2|bob knows carol\n",
        "P|0|nick|al\n",
    ]
    invocations = 0

    def _on_chunk() -> None:
        nonlocal invocations
        invocations += 1

    content, _, _, _, _ = await _consume_extraction_stream(
        _content_stream(deltas), _detector(), on_chunk=_on_chunk
    )

    assert invocations == len(deltas)
    assert content == "".join(deltas)


@pytest.mark.asyncio
async def test_on_chunk_optional_back_compat() -> None:
    """Omitting on_chunk leaves the stream consumer unchanged."""
    deltas = ["E|X|Type|||S1|x\n"]
    content, _, _, _, _ = await _consume_extraction_stream(_content_stream(deltas), _detector())
    assert content == "E|X|Type|||S1|x\n"


@pytest.mark.asyncio
async def test_on_chunk_errors_do_not_crash_stream() -> None:
    """Heartbeat is best-effort; a callback exception must not abort the stream."""
    deltas = [
        "E|Alice|Character|||S1|first\n",
        "E|Bob|Character|||S1|second\n",
    ]

    def _flaky() -> None:
        raise RuntimeError("DB write transient failure")

    # Stream completes despite the callback raising on every chunk.
    content, _, _, _, _ = await _consume_extraction_stream(
        _content_stream(deltas), _detector(), on_chunk=_flaky
    )
    assert content == "".join(deltas)


@pytest.mark.asyncio
async def test_on_chunk_does_not_fire_for_done_chunk() -> None:
    """The 'done' chunk is metadata, not progress — should not trigger heartbeat.

    Otherwise a stream that emits zero content but a final done chunk
    would falsely look 'alive' to the caller.
    """
    invocations = 0

    def _on_chunk() -> None:
        nonlocal invocations
        invocations += 1

    # Empty content: only a done chunk yielded.
    async def _only_done() -> AsyncIterator[dict[str, Any]]:
        yield {
            "type": "done",
            "usage": {"prompt_tokens": 5, "completion_tokens": 0},
            "content": "",
        }

    await _consume_extraction_stream(_only_done(), _detector(), on_chunk=_on_chunk)
    assert invocations == 0


@pytest.mark.asyncio
async def test_on_chunk_fires_even_when_stream_aborts_via_loop_detector() -> None:
    """Up to the abort point, every received chunk fires the callback.

    Verifies the callback is invoked on each chunk before the loop
    detector decides to abort, so SourceRecovery sees stream activity
    even on degenerate streams.
    """
    deltas = [
        "E|A|Char|||S1|d\n",
        "E|B|Char|||S1|d\n",
        "E|C|Char|||S1|d\n",
    ]
    invocations = 0

    def _on_chunk() -> None:
        nonlocal invocations
        invocations += 1

    await _consume_extraction_stream(
        _content_stream(deltas, include_done=False), _detector(), on_chunk=_on_chunk
    )
    # All three content deltas fire before the (un-aborted) stream ends.
    assert invocations == 3


@pytest.mark.asyncio
async def test_extract_relationships_forwards_on_stream_progress() -> None:
    """Pass-2 must accept ``on_stream_progress`` and forward it to ``call_llm``.

    Regression: ``_extract_relationships`` was extracted from
    ``extract_single_chunk`` in a refactor; a later heartbeat fix added
    ``on_stream_progress=on_stream_progress`` inside the body without adding
    the parameter to the new method's signature, so every chunk that
    reached pass-2 raised ``NameError: name 'on_stream_progress' is not
    defined``.
    """
    from unittest.mock import MagicMock

    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        AIEntityExtractor,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
        FilteringLog,
    )

    extractor = AIEntityExtractor.__new__(AIEntityExtractor)

    captured: dict[str, object] = {}

    async def _fake_call_llm(
        prompt: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        max_entity_count_override: int | None = None,
        on_stream_progress: object = None,
    ) -> Any:
        from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
            CallLLMResult,
        )

        captured["on_stream_progress"] = on_stream_progress
        return CallLLMResult(
            content="",
            input_tokens=0,
            output_tokens=0,
            finish_reason="stop",
            aborted_by_loop=False,
        )

    extractor.call_llm = _fake_call_llm  # type: ignore[method-assign]

    sentinel_calls: list[None] = []

    def _sentinel() -> None:
        sentinel_calls.append(None)

    extraction_cfg = MagicMock(
        loop_max_out_of_bounds=10,
        loop_max_source_type_repeat=5,
    )
    filtering_config = MagicMock(
        enable_type_constraints=False,
        enable_evidence_filter=False,
        enable_implausible_filter=False,
        enable_relationship_evidence_filter=False,
    )

    await extractor._extract_relationships(
        entities=[{"name": "Alice", "type": "Person", "aliases": []}],
        numbered_text="S1: Alice met Bob.",
        sentences=["Alice met Bob."],
        edge_templates_formatted="",
        relationship_guidance=None,
        relationship_examples=None,
        extraction_cfg=extraction_cfg,
        filtering_config=filtering_config,
        evidence_stats={},
        filtering_log=FilteringLog(),
        chunk_content="Alice met Bob.",
        _max_entity_count_override=None,
        _min_alias_len=2,
        on_stream_progress=_sentinel,
    )

    assert captured["on_stream_progress"] is _sentinel
