# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration tests for ``_process_llm_stream``.

Drives the chunk-consumption inner loop of the chat handler with a
``FakeChatLLMProvider``-shaped async iterable and verifies the SSE
event sequence emitted under three scenarios:

- ``CONTENT_ONLY`` — content deltas then a ``done`` event downstream.
- ``RAISE_MID_STREAM`` — provider raises during iteration; handler logs
  it but does not propagate the exception (pins the existing behaviour
  at ``handler.py:575``).
- ``SLOW_STREAM`` — ``asyncio.sleep`` between chunks; entire stream
  still completes.

Scope deviation from the design spec (`2026-05-16-testing-gaps-design`):
the spec called for driving ``stream_chat_response`` end-to-end with a
real ``chat_service``. In practice ``stream_chat_response`` requires
patching the ``get_provider_factory()`` singleton, building real
``Settings``, wiring tool discovery, and constructing message-prep
fixtures — wiring out of proportion with the surface under test. The
actual chunk-handling logic lives in ``_process_llm_stream`` and is
exercised directly here. A separate Cortex SSE smoke test covers the
wire-format contract end-to-end (see
``packages/cortex/tests/integration/test_chat_stream_endpoint.py``).
The 4th scenario from the spec (tool-call resumption) is deferred —
it tests downstream orchestration in ``_handle_tool_calls`` that this
function delegates to, not the chunk-consumption loop itself.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.streaming.chat.handler import _process_llm_stream
from tests.fakes.chat_llm import ChatResponseStrategy, FakeChatLLMProvider


def _parse_sse(blob: bytes) -> dict[str, Any]:
    r"""Parse a single ``data: {...}\n\n`` SSE frame into the JSON payload."""
    text = blob.decode()
    assert text.startswith("data: ")
    assert text.endswith("\n\n")
    payload: dict[str, Any] = json.loads(text[len("data: ") : -2])
    return payload


def _settings_mock() -> MagicMock:
    """Build a settings stub with the leaves _process_llm_stream's downstream uses."""
    settings = MagicMock()
    settings.llm.thinking_for_chat = False
    settings.llm.thinking_for_tools = False
    settings.llm.token_cost_input_per_million = 0.0
    settings.llm.token_cost_output_per_million = 0.0
    settings.llm.validation.response_grounding = False
    settings.llm.validation.citation_references = False
    return settings


async def _drive(
    llm_result: Any,
    chat_service: MagicMock,
) -> list[bytes]:
    """Collect every SSE-bytes chunk yielded by ``_process_llm_stream``."""
    collected: list[bytes] = []
    async for chunk in _process_llm_stream(
        llm_result=llm_result,
        chat_id="test-chat-id",
        chat_service=chat_service,
        chat_provider=MagicMock(),
        tool_executor=MagicMock(),
        available_tools=[],
        messages_for_llm=[],
        llm_debug=None,
        settings=_settings_mock(),
    ):
        collected.append(chunk)
    return collected


@pytest.mark.asyncio
async def test_content_only_streams_content_events_then_finalizes() -> None:
    """Happy path: content deltas appear on the wire in order; downstream finalize fires."""
    fake = FakeChatLLMProvider(strategy=ChatResponseStrategy.CONTENT_ONLY)
    llm_result = await fake.chat(messages=[], stream=True)
    chat_service = MagicMock()

    sse_frames = await _drive(llm_result, chat_service)

    parsed = [_parse_sse(b) for b in sse_frames]
    types = [p["type"] for p in parsed]
    # _process_llm_stream emits content events; _finalize_response (downstream)
    # emits the terminal done event after saving.
    assert "content" in types
    assert types[0] == "content"
    # Accumulated content grows monotonically.
    contents = [p["accumulated"] for p in parsed if p["type"] == "content"]
    assert all(len(contents[i]) <= len(contents[i + 1]) for i in range(len(contents) - 1))
    # chat_service got the final assistant message saved (via _save_and_emit_done).
    chat_service.add_message.assert_called()


@pytest.mark.asyncio
async def test_provider_raise_does_not_propagate() -> None:
    """Pins existing handler behaviour at handler.py:575 — iterator exceptions are logged, not re-raised, and no error event is yielded."""
    fake = FakeChatLLMProvider(strategy=ChatResponseStrategy.RAISE_MID_STREAM)
    llm_result = await fake.chat(messages=[], stream=True)
    chat_service = MagicMock()

    # The exception must not escape _process_llm_stream.
    sse_frames = await _drive(llm_result, chat_service)

    parsed = [_parse_sse(b) for b in sse_frames]
    types = [p["type"] for p in parsed]
    # At least one content event was emitted before the raise.
    assert "content" in types
    # No error event is emitted for iterator-raised exceptions (current
    # design — only an explicit ``{"type": "error"}`` provider chunk
    # would do that). If this changes in future, update this test.
    assert "error" not in types


@pytest.mark.asyncio
async def test_slow_stream_completes_without_starving() -> None:
    """SLOW_STREAM uses asyncio.sleep between chunks; stream still completes cleanly."""
    fake = FakeChatLLMProvider(strategy=ChatResponseStrategy.SLOW_STREAM)
    llm_result = await fake.chat(messages=[], stream=True)
    chat_service = MagicMock()

    sse_frames = await _drive(llm_result, chat_service)

    parsed = [_parse_sse(b) for b in sse_frames]
    types = [p["type"] for p in parsed]
    # All scheduled content chunks made it through.
    assert types.count("content") >= 2
    # Stream still finalized — chat_service got the save.
    chat_service.add_message.assert_called()
