# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Stream consumer flushes final partial line and surfaces finish_reason.

Workstream 8 (2026-05-07) extends ``_consume_extraction_stream`` with
two observability signals (``finish_reason`` and ``aborted``) and adds
a trailing partial-line flush so the last entity / relationship line is
not silently dropped when the model tops out mid-token.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock

import pytest


async def _stream(chunks: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
    for chunk in chunks:
        yield chunk


def _make_detector() -> Any:
    """Build a real ``_StreamLoopDetector`` with non-tripping thresholds."""
    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        _StreamLoopDetector,
    )

    cfg = MagicMock(
        loop_max_out_of_bounds=10,
        loop_max_source_type_repeat=10,
        loop_max_property_repeat=10,
        loop_max_entity_count=10000,
        loop_invalid_relationship_rate_warmup=10000,
        loop_invalid_relationship_rate_threshold=1.0,
    )
    return _StreamLoopDetector(extraction_cfg=cfg)


@pytest.mark.asyncio
async def test_final_line_without_newline_is_parsed_via_detector() -> None:
    """Last entity emitted without a trailing newline still feeds the detector.

    Before Workstream 8 the stream consumer let the line buffer drop on
    the floor when the stream ended without a closing newline. The
    detector therefore never saw the partial entity / relationship line
    and the structural-loop path missed it. The flush guarantees the
    detector observes every complete line, including the trailing one.
    """
    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        _consume_extraction_stream,
    )

    detector = _make_detector()
    seen: list[str] = []
    real_check_line = detector.check_line

    def _spy(line: str, content_length: int) -> bool:
        seen.append(line)
        return real_check_line(line, content_length)

    detector.check_line = _spy  # type: ignore[method-assign]

    stream = _stream(
        [
            {"type": "content", "delta": "E|0|Alice|Person|||\n"},
            {"type": "content", "delta": "E|1|Bob|Person|||"},  # no trailing \n
            {
                "type": "done",
                "content": "E|0|Alice|Person|||\nE|1|Bob|Person|||",
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
                "finish_reason": "stop",
            },
        ]
    )
    content, _, _, finish_reason, aborted = await _consume_extraction_stream(stream, detector)

    assert "E|1|Bob" in content
    assert finish_reason == "stop"
    assert aborted is False
    # Both lines (the one with \n and the partial one without) reached the detector.
    assert "E|0|Alice|Person|||" in seen
    assert "E|1|Bob|Person|||" in seen


@pytest.mark.asyncio
async def test_finish_reason_length_propagated_on_truncation() -> None:
    """``finish_reason='length'`` from the done chunk bubbles up unchanged."""
    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        _consume_extraction_stream,
    )

    detector = _make_detector()
    stream = _stream(
        [
            {"type": "content", "delta": "E|0|Alice|Person|||\nE|1|Bob|Per"},
            {
                "type": "done",
                "content": "E|0|Alice|Person|||\nE|1|Bob|Per",
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
                "finish_reason": "length",
            },
        ]
    )
    _content, _, _, finish_reason, aborted = await _consume_extraction_stream(stream, detector)

    assert finish_reason == "length"
    assert aborted is False


@pytest.mark.asyncio
async def test_finish_reason_defaults_to_unknown_when_done_omits_it() -> None:
    """Stream that ends without a done chunk surfaces ``finish_reason='unknown'``."""
    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        _consume_extraction_stream,
    )

    detector = _make_detector()
    stream = _stream(
        [
            {"type": "content", "delta": "E|0|Alice|Person|||\n"},
            # No done chunk.
        ]
    )
    _content, _, _, finish_reason, aborted = await _consume_extraction_stream(stream, detector)

    assert finish_reason == "unknown"
    assert aborted is False


@pytest.mark.asyncio
async def test_aborted_flag_propagated_from_detector() -> None:
    """When the detector aborts, the consumer surfaces ``aborted=True``."""
    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        _consume_extraction_stream,
        _StreamLoopDetector,
    )

    cfg = MagicMock(
        loop_max_out_of_bounds=1,
        loop_max_source_type_repeat=10,
        loop_max_property_repeat=10,
        loop_max_entity_count=2,  # trip the entity-count cap immediately
        loop_invalid_relationship_rate_warmup=10000,
        loop_invalid_relationship_rate_threshold=1.0,
    )
    detector = _StreamLoopDetector(extraction_cfg=cfg)

    stream = _stream(
        [
            {"type": "content", "delta": "E|0|A|T|||\nE|1|B|T|||\nE|2|C|T|||\n"},
            {"type": "done", "content": "...", "usage": {}, "finish_reason": "stop"},
        ]
    )
    _content, _, _, _finish, aborted = await _consume_extraction_stream(stream, detector)

    assert aborted is True


@pytest.mark.asyncio
async def test_call_llm_returns_call_llm_result_dataclass() -> None:
    """``AIEntityExtractor.call_llm`` returns a typed result, not a tuple."""
    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        AIEntityExtractor,
        CallLLMResult,
    )

    extractor = AIEntityExtractor.__new__(AIEntityExtractor)
    extractor.settings = MagicMock(
        llm=MagicMock(extraction_temperature=0.1, extraction_max_tokens=1024),
        extraction=MagicMock(
            loop_max_out_of_bounds=10,
            loop_max_source_type_repeat=10,
            loop_max_property_repeat=10,
            loop_max_entity_count=1000,
            loop_invalid_relationship_rate_warmup=10000,
            loop_invalid_relationship_rate_threshold=1.0,
        ),
    )

    class _FakeProvider:
        async def chat(self, **_kwargs: Any) -> AsyncIterator[dict[str, Any]]:
            return _stream(
                [
                    {"type": "content", "delta": "E|0|Alice|Person|||\n"},
                    {
                        "type": "done",
                        "content": "E|0|Alice|Person|||",
                        "usage": {"prompt_tokens": 5, "completion_tokens": 10},
                        "finish_reason": "length",
                    },
                ]
            )

    extractor._llm_provider = _FakeProvider()  # type: ignore[assignment]

    result = await extractor.call_llm("hello")
    assert isinstance(result, CallLLMResult)
    assert result.finish_reason == "length"
    assert result.aborted_by_loop is False
    assert result.input_tokens > 0
    assert result.output_tokens > 0
