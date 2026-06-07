# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for FakeChatLLMProvider.

Pins the chunk shape ``_process_llm_stream`` consumes and the per-strategy
behaviour of the chat-flavored fake (separate from the extraction fake
in ``tests.fakes.llm``).
"""

from __future__ import annotations

from typing import Any

import pytest

from chaoscypher_core.exceptions import LLMError
from tests.fakes.chat_llm import ChatResponseStrategy, FakeChatLLMProvider


async def _collect(stream: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    async for chunk in stream:
        out.append(chunk)
    return out


@pytest.mark.asyncio
async def test_content_only_yields_content_chunks_then_done() -> None:
    fake = FakeChatLLMProvider(strategy=ChatResponseStrategy.CONTENT_ONLY)
    stream = await fake.chat(messages=[], stream=True)
    chunks = await _collect(stream)

    # All but the last chunk are content; last is done.
    types = [c["type"] for c in chunks]
    assert types[-1] == "done"
    assert all(t == "content" for t in types[:-1])
    assert len(types) >= 2  # at least one content + done

    # Each content chunk has delta + accumulated; accumulated grows.
    accumulated_lengths = [len(c["accumulated"]) for c in chunks if c["type"] == "content"]
    assert accumulated_lengths == sorted(accumulated_lengths)
    # Done chunk has full content + no tool_calls.
    done = chunks[-1]
    assert done["content"]
    assert done.get("tool_calls") is None


@pytest.mark.asyncio
async def test_raise_mid_stream_yields_some_content_then_raises() -> None:
    fake = FakeChatLLMProvider(strategy=ChatResponseStrategy.RAISE_MID_STREAM)
    stream = await fake.chat(messages=[], stream=True)

    chunks: list[dict[str, Any]] = []

    async def _drain() -> None:
        async for chunk in stream:
            chunks.append(chunk)

    with pytest.raises(LLMError):
        await _drain()

    # At least one content chunk was yielded before the raise.
    assert any(c["type"] == "content" for c in chunks)
    # No done event yet — the raise short-circuited.
    assert not any(c["type"] == "done" for c in chunks)


@pytest.mark.asyncio
async def test_slow_stream_completes_with_done() -> None:
    """SLOW_STREAM sleeps between content chunks but ultimately completes."""
    fake = FakeChatLLMProvider(strategy=ChatResponseStrategy.SLOW_STREAM)
    stream = await fake.chat(messages=[], stream=True)
    chunks = await _collect(stream)

    types = [c["type"] for c in chunks]
    assert types[-1] == "done"
    assert sum(1 for t in types if t == "content") >= 2


@pytest.mark.asyncio
async def test_tool_call_then_content_two_phase() -> None:
    """First chat() yields a done-with-tool_calls; second yields content + done."""
    fake = FakeChatLLMProvider(strategy=ChatResponseStrategy.TOOL_CALL_THEN_CONTENT)

    # Phase 1: tool_call signaled in the done chunk's tool_calls field.
    stream1 = await fake.chat(messages=[], stream=True)
    chunks1 = await _collect(stream1)
    assert chunks1[-1]["type"] == "done"
    assert chunks1[-1].get("tool_calls"), "phase-1 done must carry a non-empty tool_calls list"

    # Phase 2: regular content + done after the handler "executes" the tool.
    stream2 = await fake.chat(messages=[], stream=True)
    chunks2 = await _collect(stream2)
    assert chunks2[-1]["type"] == "done"
    assert chunks2[-1].get("tool_calls") is None
    assert any(c["type"] == "content" for c in chunks2)


@pytest.mark.asyncio
async def test_call_count_increments_per_chat_call() -> None:
    fake = FakeChatLLMProvider(strategy=ChatResponseStrategy.CONTENT_ONLY)
    assert fake.call_count == 0
    await fake.chat(messages=[], stream=True)
    assert fake.call_count == 1
    await fake.chat(messages=[], stream=True)
    assert fake.call_count == 2
