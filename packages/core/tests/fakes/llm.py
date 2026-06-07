# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""FakeLLMProvider — canned, deterministic LLM stand-in for pipeline tests.

Implements both the non-streaming and the streaming interfaces the
extraction pipeline relies on:

- ``async chat(messages, stream=False)`` returns an
  ``LLMChatResponse`` (non-streaming, for callers like chat completions).
- ``async chat(messages, stream=True)`` returns an async-iterable object
  yielding stream chunks (``{type: "content", delta: ...}`` …
  ``{type: "done", usage: ..., finish_reason: ...}``). This is what
  ``AIEntityExtractor.call_llm`` consumes via
  ``_consume_extraction_stream``.

Per-pass distinction: the extraction pipeline calls the LLM twice per
chunk (Pass 1 = entities, Pass 2 = relationships referencing the
filtered entity list). The fake tracks an internal call counter and
alternates pass-1 / pass-2 content so a single fake instance can serve
multiple chunks (call_count 1,2 = chunk 1; 3,4 = chunk 2; etc.).

See the mocked pipeline test fixtures.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Any

from chaoscypher_core.models import LLMChatResponse, TokenUsage


__all__ = ["FakeLLMProvider", "LLMResponseStrategy"]


class LLMResponseStrategy(StrEnum):
    """Canned response shape served by ``FakeLLMProvider.chat``."""

    DEFAULT = "default"
    EMPTY = "empty"
    TRUNCATED = "truncated"
    MALFORMED = "malformed"


# V2 pipe-delimited line format (see line_parser.py docstring):
#   E|name|type|aliases|confidence|sent_ref|description
#   R|src_idx|dst_idx|type|confidence|sent_ref|justification

# Pass 1 (entities) for the DEFAULT strategy — two entities the parser
# accepts cleanly, used by happy-path + chunk-rerun journeys.
_DEFAULT_PASS1 = (
    "E|Alice|Person|alice|0.9|S1|A character in the test fixture\n"
    "E|Bob|Person|bob|0.9|S2|Another character\n"
)

# Pass 2 (relationships) for the DEFAULT strategy — references the
# Pass 1 entities by index.
_DEFAULT_PASS2 = "R|0|1|knows|0.9|S1-S2|They meet in the chunk\n"

_EMPTY_PAYLOAD = ""

# Mid-record cut — second entity stops mid-field. Real LLMs hit this
# when output_tokens budget runs out.
_TRUNCATED_PASS1 = "E|Alice|Person|alice|0.9|S1|A character\nE|Bob|Pers"

# Records that fail parser validation: too few fields, bad sent_ref,
# non-integer src index.
_MALFORMED_PASS1 = (
    "E|TooFewFields|Person|alice\nE|Alice|Person|alice|notanumber|notasentref|missing fields\n"
)
_MALFORMED_PASS2 = "R|0|notanint|knows|0.9|S1-S2|invalid src index\n"


_EMPTY_TRIPLE: tuple[str, str, int] = (_EMPTY_PAYLOAD, "stop", 0)

# (pass1, pass2) per strategy. TRUNCATED only meaningfully fires on pass 1 —
# by pass 2 the chunk handler would have surfaced the truncation; we still
# return empty content for pass 2 so the parser has nothing to disagree with.
_PAYLOAD_TABLE: dict[LLMResponseStrategy, tuple[tuple[str, str, int], tuple[str, str, int]]] = {
    LLMResponseStrategy.DEFAULT: ((_DEFAULT_PASS1, "stop", 60), (_DEFAULT_PASS2, "stop", 20)),
    LLMResponseStrategy.EMPTY: (_EMPTY_TRIPLE, _EMPTY_TRIPLE),
    LLMResponseStrategy.TRUNCATED: ((_TRUNCATED_PASS1, "length", 4096), _EMPTY_TRIPLE),
    LLMResponseStrategy.MALFORMED: ((_MALFORMED_PASS1, "stop", 40), (_MALFORMED_PASS2, "stop", 20)),
}


def _payload_for_pass(strategy: LLMResponseStrategy, pass_number: int) -> tuple[str, str, int]:
    """Return ``(text, finish_reason, output_tokens)`` for one (strategy, pass).

    ``pass_number`` is 1 (entities) or 2 (relationships).

    Defensive guard: the dict lookup raises ``KeyError`` for non-enum inputs,
    which ``test_unknown_strategy_raises`` exercises by calling with a string.
    """
    try:
        passes = _PAYLOAD_TABLE[strategy]
    except KeyError as exc:
        msg = f"unsupported strategy: {strategy!r}"
        raise ValueError(msg) from exc
    return passes[0] if pass_number == 1 else passes[1]


class _StreamingResponse:
    """Async-iterable streaming response.

    ``_consume_extraction_stream`` calls ``async for chunk in response``
    directly on the value ``provider.chat(stream=True)`` returns. This
    class yields the same chunk shape the real provider yields:

      - ``{"type": "content", "delta": "<text fragment>"}``
      - ``{"type": "done", "usage": {...}, "content": "<full>",
        "finish_reason": "<reason>"}``

    The whole content is yielded in two slices so the streaming consumer
    has to glue them back together — exercising the line-buffer code
    path. A real LLM would yield many more tokens; two is enough for the
    consumer's invariants.
    """

    def __init__(
        self,
        *,
        text: str,
        finish_reason: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        self._text = text
        self._finish_reason = finish_reason
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[dict[str, Any]]:
        # Split content into two slices so the consumer's line-buffer
        # code path is exercised. For empty content, yield no content
        # chunks at all — go straight to done.
        if self._text:
            mid = len(self._text) // 2
            yield {"type": "content", "delta": self._text[:mid]}
            yield {"type": "content", "delta": self._text[mid:]}
        yield {
            "type": "done",
            "usage": {
                "prompt_tokens": self._input_tokens,
                "completion_tokens": self._output_tokens,
            },
            "content": self._text,
            "finish_reason": self._finish_reason,
        }


class FakeLLMProvider:
    """Drop-in replacement for ``LLMProviderPort``-shaped providers.

    Implements ``async chat`` with canned responses. Tracks ``call_count``
    per instance so tests can assert "the LLM was called N times" without
    an external mocking library. Per-call content alternates between
    Pass 1 (entities) and Pass 2 (relationships) — see module docstring.

    Extra ``kwargs`` (``temperature``, ``max_tokens``, etc.) are accepted
    and ignored so the fake stands in for any caller's signature.
    """

    def __init__(
        self,
        strategy: LLMResponseStrategy = LLMResponseStrategy.DEFAULT,
        *,
        provider_name: str = "fake-llm-test",
        input_tokens: int = 120,
    ) -> None:
        self.strategy = strategy
        self.provider_name = provider_name
        self.input_tokens = input_tokens
        self.call_count = 0

    async def chat(
        self,
        messages: str | list[Any],
        tools: list[Any] | None = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> LLMChatResponse | _StreamingResponse:
        """Return canned content for the configured strategy.

        Pass 1 (odd calls) → entity content.
        Pass 2 (even calls) → relationship content.

        When ``stream=True`` returns a ``_StreamingResponse`` whose
        ``__aiter__`` yields the same chunk shape the real provider
        yields. ``_consume_extraction_stream`` iterates this directly.
        """
        self.call_count += 1
        pass_number = 1 if self.call_count % 2 == 1 else 2
        text, finish_reason, output_tokens = _payload_for_pass(self.strategy, pass_number)

        if stream:
            return _StreamingResponse(
                text=text,
                finish_reason=finish_reason,
                input_tokens=self.input_tokens,
                output_tokens=output_tokens,
            )

        return LLMChatResponse(
            content=text,
            tool_calls=None,
            thinking=None,
            usage=TokenUsage(
                input_tokens=self.input_tokens,
                output_tokens=output_tokens,
                total_tokens=self.input_tokens + output_tokens,
            ),
            provider=self.provider_name,
            is_stream=False,
            stream=None,
            instance_id=None,
            finish_reason=finish_reason,
        )
