# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""FakeChatLLMProvider — canned chat-streaming LLM stand-in for handler tests.

Separate from the extraction fake in :mod:`tests.fakes.llm`: extraction
streams a 2-pass (entities / relationships) pipe-delimited payload;
chat streaming yields ``content`` / ``thinking_delta`` / ``done`` /
``error`` event chunks of a different shape and supports iterative
tool-calling. Sharing one fake across both concerns muddles the per-call
contract — each fake stays small and focused.

The async-iter chunk shape this fake yields matches what
``chaoscypher_core.streaming.chat.handler._process_llm_stream`` consumes
(see ``handler.py:503``):

- ``{"type": "content", "delta": "...", "accumulated": "..."}``
- ``{"type": "done", "content": "...", "thinking": None,
   "tool_calls": list | None, "usage": {...}, "finish_reason": "stop"}``
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Any

from chaoscypher_core.exceptions import LLMError


__all__ = ["ChatResponseStrategy", "FakeChatLLMProvider"]


class ChatResponseStrategy(StrEnum):
    """Canned scenario shape served by ``FakeChatLLMProvider.chat``."""

    CONTENT_ONLY = "content_only"
    RAISE_MID_STREAM = "raise_mid_stream"
    SLOW_STREAM = "slow_stream"
    TOOL_CALL_THEN_CONTENT = "tool_call_then_content"


_HAPPY_TEXT = "Hello from the fake assistant."
_HAPPY_FINAL_TEXT = "Tool result acknowledged."
_PRE_RAISE_TEXT = "Partial output before "
_SLOW_CHUNK_DELAY_S = 0.01  # Far below heartbeat threshold; deterministic.


def _chunkify(text: str, n: int = 3) -> list[str]:
    """Split ``text`` into ``n`` roughly-equal slices for streamed delivery."""
    if not text:
        return []
    step = max(1, len(text) // n)
    pieces = [text[i : i + step] for i in range(0, len(text), step)]
    # Coalesce a too-small tail back into the previous piece.
    if len(pieces) > n and len(pieces[-1]) < step // 2:
        pieces[-2] += pieces[-1]
        pieces = pieces[:-1]
    return pieces


def _content_chunk(delta: str, accumulated: str) -> dict[str, Any]:
    return {"type": "content", "delta": delta, "accumulated": accumulated}


def _done_chunk(
    *,
    content: str,
    tool_calls: list[dict[str, Any]] | None = None,
    finish_reason: str = "stop",
    input_tokens: int = 20,
    output_tokens: int = 0,
) -> dict[str, Any]:
    return {
        "type": "done",
        "content": content,
        "thinking": None,
        "tool_calls": tool_calls,
        "usage": {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens or len(content),
        },
        "finish_reason": finish_reason,
    }


def _fake_tool_call() -> dict[str, Any]:
    """Shape mirrors what the handler expects in ``done.tool_calls``."""
    return {
        "id": "call_fake_1",
        "name": "search_graph",
        "arguments": {"query": "fake"},
    }


class _ChatStream:
    """Async-iterable streaming response yielding handler-shape chunks."""

    def __init__(self, chunks: list[dict[str, Any]], *, delay_s: float = 0.0) -> None:
        self._chunks = chunks
        self._delay_s = delay_s

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[dict[str, Any]]:
        for chunk in self._chunks:
            if self._delay_s:
                await asyncio.sleep(self._delay_s)
            yield chunk


class _RaisingChatStream:
    """Yields a single content chunk then raises ``LLMError``."""

    def __init__(self, pre_raise_text: str) -> None:
        self._pre_raise_text = pre_raise_text

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[dict[str, Any]]:
        yield _content_chunk(self._pre_raise_text, self._pre_raise_text)
        msg = "Simulated mid-stream provider failure"
        raise LLMError(msg)


def _build_content_only_chunks(text: str) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    accumulated = ""
    for piece in _chunkify(text):
        accumulated += piece
        chunks.append(_content_chunk(piece, accumulated))
    chunks.append(_done_chunk(content=accumulated))
    return chunks


def _build_tool_call_phase1_chunks() -> list[dict[str, Any]]:
    """Phase 1: emit no content, just a done chunk carrying a tool_call."""
    return [_done_chunk(content="", tool_calls=[_fake_tool_call()])]


def _build_tool_call_phase2_chunks(text: str) -> list[dict[str, Any]]:
    """Phase 2: normal content + done. tool_calls is None."""
    return _build_content_only_chunks(text)


class FakeChatLLMProvider:
    """Chat-streaming LLM stand-in with selectable scenario strategies.

    ``call_count`` increments per ``chat()`` invocation so tests can assert
    "the handler called the LLM N times" (e.g. tool-call iterations) and
    the ``TOOL_CALL_THEN_CONTENT`` strategy uses it to switch between
    phase-1 (tool_call announcement) and phase-2 (final content) responses.

    Extra keyword arguments to ``chat`` (``tools``, ``enable_thinking``,
    ``temperature``, …) are accepted and ignored so the fake stands in for
    any production caller's signature.
    """

    def __init__(
        self,
        strategy: ChatResponseStrategy = ChatResponseStrategy.CONTENT_ONLY,
        *,
        provider_name: str = "fake-chat-llm",
    ) -> None:
        self.strategy = strategy
        self.provider_name = provider_name
        self.call_count = 0

    async def chat(
        self,
        messages: Any,
        tools: list[Any] | None = None,
        stream: bool = True,
        **_kwargs: Any,
    ) -> _ChatStream | _RaisingChatStream:
        self.call_count += 1

        if self.strategy is ChatResponseStrategy.CONTENT_ONLY:
            return _ChatStream(_build_content_only_chunks(_HAPPY_TEXT))

        if self.strategy is ChatResponseStrategy.RAISE_MID_STREAM:
            return _RaisingChatStream(_PRE_RAISE_TEXT)

        if self.strategy is ChatResponseStrategy.SLOW_STREAM:
            return _ChatStream(
                _build_content_only_chunks(_HAPPY_TEXT),
                delay_s=_SLOW_CHUNK_DELAY_S,
            )

        if self.strategy is ChatResponseStrategy.TOOL_CALL_THEN_CONTENT:
            if self.call_count == 1:
                return _ChatStream(_build_tool_call_phase1_chunks())
            return _ChatStream(_build_tool_call_phase2_chunks(_HAPPY_FINAL_TEXT))

        # Exhaustiveness guard — mypy considers this unreachable.
        msg = f"unsupported strategy: {self.strategy!r}"  # type: ignore[unreachable]
        raise ValueError(msg)
