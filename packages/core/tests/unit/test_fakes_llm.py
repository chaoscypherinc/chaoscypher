# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for FakeLLMProvider.

Critical invariants:

1. DEFAULT pass-1 content parses to 2 entities (via real line_parser).
2. DEFAULT pass-2 content parses to 1 relationship referencing those entities.
3. Streaming responses yield the chunk shape ``_consume_extraction_stream``
   consumes (``{type: content, delta: ...}`` and ``{type: done, usage,
   content, finish_reason}``).
4. Calls alternate pass-1 / pass-2 content based on call_count parity so a
   single fake instance can serve a chunk's two-pass extraction (and
   multiple chunks across journeys).
"""

from __future__ import annotations

import pytest

from chaoscypher_core.services.sources.engine.extraction.utils.line_parser import (
    parse_extraction_output,
)
from tests.fakes.llm import FakeLLMProvider, LLMResponseStrategy


@pytest.mark.asyncio
async def test_default_pass1_parses_to_2_entities() -> None:
    fake = FakeLLMProvider(strategy=LLMResponseStrategy.DEFAULT)
    resp = await fake.chat("entity prompt")

    assert resp.finish_reason == "stop"
    entities, relationships, _p = parse_extraction_output(
        entities_str=resp.content,
    )
    assert len(entities) == 2
    assert {e["name"] for e in entities} == {"Alice", "Bob"}
    assert relationships == []


@pytest.mark.asyncio
async def test_default_pass2_parses_to_1_relationship() -> None:
    fake = FakeLLMProvider(strategy=LLMResponseStrategy.DEFAULT)
    await fake.chat("pass 1 prompt")  # consume pass 1
    resp = await fake.chat("pass 2 prompt")

    entities, relationships, _p = parse_extraction_output(
        entities_str=resp.content,
    )
    assert entities == []  # pass 2 emits R| only
    assert len(relationships) == 1
    assert relationships[0]["type"] == "knows"


@pytest.mark.asyncio
async def test_call_count_increments() -> None:
    fake = FakeLLMProvider()
    assert fake.call_count == 0
    await fake.chat("a")
    await fake.chat("b")
    assert fake.call_count == 2


@pytest.mark.asyncio
async def test_empty_strategy_returns_empty_content() -> None:
    fake = FakeLLMProvider(strategy=LLMResponseStrategy.EMPTY)
    resp = await fake.chat("dummy")
    assert resp.content == ""
    assert resp.usage.output_tokens == 0
    assert resp.finish_reason == "stop"


@pytest.mark.asyncio
async def test_truncated_pass1_reports_length_finish_reason() -> None:
    fake = FakeLLMProvider(strategy=LLMResponseStrategy.TRUNCATED)
    resp = await fake.chat("entity prompt")
    assert resp.finish_reason == "length"


@pytest.mark.asyncio
async def test_malformed_strategy_yields_no_valid_records() -> None:
    fake = FakeLLMProvider(strategy=LLMResponseStrategy.MALFORMED)
    pass1 = await fake.chat("entity prompt")
    pass2 = await fake.chat("rel prompt")

    e1, r1, _ = parse_extraction_output(entities_str=pass1.content)
    e2, r2, _ = parse_extraction_output(entities_str=pass2.content)

    assert e1 == []
    assert r1 == []
    assert e2 == []
    assert r2 == []


@pytest.mark.asyncio
async def test_streaming_yields_content_then_done_chunks() -> None:
    fake = FakeLLMProvider(strategy=LLMResponseStrategy.DEFAULT)
    response = await fake.chat("prompt", stream=True)

    chunks = [chunk async for chunk in response]

    # At least two content chunks (text is split mid-string) + one done.
    content_chunks = [c for c in chunks if c["type"] == "content"]
    done_chunks = [c for c in chunks if c["type"] == "done"]
    assert len(content_chunks) >= 1
    assert len(done_chunks) == 1
    assert done_chunks[0]["finish_reason"] == "stop"
    assert done_chunks[0]["usage"]["completion_tokens"] == 60  # default pass 1


@pytest.mark.asyncio
async def test_streaming_empty_strategy_emits_done_only() -> None:
    fake = FakeLLMProvider(strategy=LLMResponseStrategy.EMPTY)
    response = await fake.chat("prompt", stream=True)

    chunks = [chunk async for chunk in response]

    # No content chunks — empty payload yields done directly.
    content_chunks = [c for c in chunks if c["type"] == "content"]
    assert content_chunks == []
    assert chunks[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_streaming_reassembled_content_matches_payload() -> None:
    fake = FakeLLMProvider(strategy=LLMResponseStrategy.DEFAULT)
    response = await fake.chat("prompt", stream=True)

    parts = []
    done_content = None
    async for chunk in response:
        if chunk["type"] == "content":
            parts.append(chunk["delta"])
        elif chunk["type"] == "done":
            done_content = chunk["content"]

    assert "".join(parts) == done_content
    # And the reassembled content parses correctly.
    entities, _r, _p = parse_extraction_output(entities_str=done_content)
    assert {e["name"] for e in entities} == {"Alice", "Bob"}


@pytest.mark.asyncio
async def test_chat_accepts_kwargs_for_signature_compat() -> None:
    fake = FakeLLMProvider()
    resp = await fake.chat(
        "prompt",
        tools=[{"name": "foo"}],
        stream=False,
        temperature=0.5,
        max_tokens=2048,
    )
    assert resp.content  # default strategy returns non-empty content


@pytest.mark.asyncio
async def test_chat_accepts_message_list_input() -> None:
    fake = FakeLLMProvider()
    resp = await fake.chat([{"role": "user", "content": "hi"}])
    assert resp.content


def test_unknown_strategy_raises() -> None:
    from tests.fakes.llm import _payload_for_pass

    with pytest.raises(ValueError, match="unsupported strategy"):
        _payload_for_pass("not-a-real-strategy", 1)  # type: ignore[arg-type]
