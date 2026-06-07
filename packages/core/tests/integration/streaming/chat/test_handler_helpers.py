# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit/integration tests for ``streaming.chat.handler`` helper functions.

Covers the timing helpers, the pure content/citation pipeline, response
validation gating, the recovery / finalize paths, and ``_save_and_emit_done``.

Out of scope (intentionally left uncovered): the top-level
``stream_chat_response`` async generator, which needs full provider-factory,
Settings, tool-discovery, and message-prep wiring disproportionate to the
surface under test (the chunk-handling logic lives in the helpers exercised
here and in ``test_process_llm_stream.py``).
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.streaming.chat import handler as handler_mod
from chaoscypher_core.streaming.chat.handler import (
    LLMDebugInfo,
    _attempt_recovery_call,
    _build_timing_update_event,
    _compute_final_timing,
    _finalize_response,
    _maybe_validate_response,
    _process_response_content,
    _save_and_emit_done,
    _save_done_chunk_timing,
    _save_stream_timing,
)
from tests.fakes.chat_llm import ChatResponseStrategy, FakeChatLLMProvider


# --------------------------------------------------------------------------- #
# Local helpers (copied — no cross-test imports allowed).
# --------------------------------------------------------------------------- #
def _parse_sse(blob: bytes) -> dict[str, Any]:
    r"""Parse a single ``data: {...}\n\n`` SSE frame into the JSON payload."""
    text = blob.decode()
    assert text.startswith("data: ")
    assert text.endswith("\n\n")
    return json.loads(text[len("data: ") : -2])


def _settings_mock() -> MagicMock:
    """Settings stub with the leaves the handler helpers touch."""
    settings = MagicMock()
    settings.llm.thinking_for_chat = False
    settings.llm.thinking_for_tools = False
    settings.chat_context.enable_response_validation = False
    return settings


def _debug() -> LLMDebugInfo:
    return LLMDebugInfo(provider="fake", model="fake-model")


async def _collect(agen: AsyncIterator[bytes]) -> list[bytes]:
    return [chunk async for chunk in agen]


# --------------------------------------------------------------------------- #
# _save_stream_timing
# --------------------------------------------------------------------------- #
def test_save_stream_timing_records_ttft_thinking_and_generation() -> None:
    debug = _debug()
    now = time.monotonic()
    _save_stream_timing(
        debug,
        stream_start=now - 1.0,
        first_token_time=now - 0.5,
        thinking_start=now - 0.9,
        thinking_end=now - 0.6,
        has_tool_calls=False,
    )
    assert debug.timing["time_to_first_token_ms"] >= 400
    assert debug.timing["thinking_ms"] >= 200
    # No tool calls + first token present -> content window recorded.
    assert "content_generation_ms" in debug.timing


def test_save_stream_timing_with_tool_calls_skips_content_window() -> None:
    debug = _debug()
    now = time.monotonic()
    _save_stream_timing(
        debug,
        stream_start=now - 1.0,
        first_token_time=now - 0.5,
        thinking_start=None,
        thinking_end=None,
        has_tool_calls=True,
    )
    assert "time_to_first_token_ms" in debug.timing
    assert "content_generation_ms" not in debug.timing


def test_save_stream_timing_early_returns_without_debug_or_start() -> None:
    # No llm_debug -> no-op (must not raise).
    _save_stream_timing(None, 1.0, 2.0, None, None)
    # llm_debug present but stream_start None -> no-op.
    debug = _debug()
    _save_stream_timing(debug, None, 2.0, None, None)
    assert debug.timing == {}


# --------------------------------------------------------------------------- #
# _save_done_chunk_timing
# --------------------------------------------------------------------------- #
def test_save_done_chunk_timing_captures_provider_timings_and_usage() -> None:
    debug = _debug()
    _save_done_chunk_timing(
        debug,
        {"provider_timings": {"eval_count": 10}, "usage": {"completion_tokens": 7}},
    )
    assert debug.timing["provider_timings"] == {"eval_count": 10}
    assert debug.timing["usage"] == {"completion_tokens": 7}


def test_save_done_chunk_timing_none_debug_is_noop() -> None:
    _save_done_chunk_timing(None, {"usage": {"x": 1}})  # must not raise


# --------------------------------------------------------------------------- #
# _build_timing_update_event
# --------------------------------------------------------------------------- #
def test_build_timing_update_event_returns_none_without_thinking() -> None:
    assert _build_timing_update_event(None) is None
    assert _build_timing_update_event(_debug()) is None


def test_build_timing_update_event_emits_when_thinking_present() -> None:
    debug = _debug()
    debug.timing["thinking_ms"] = 123
    event = _build_timing_update_event(debug)
    assert event is not None
    payload = _parse_sse(event)
    assert payload["type"] == "timing_update"
    assert payload["thinking_ms"] == 123


# --------------------------------------------------------------------------- #
# _compute_final_timing
# --------------------------------------------------------------------------- #
def test_compute_final_timing_early_return_without_stream_start() -> None:
    debug = _debug()
    _compute_final_timing(debug, "some content")
    # Nothing computed because _stream_start was never set.
    assert "total_ms" not in debug.timing


def test_compute_final_timing_native_eval_duration_path() -> None:
    debug = _debug()
    debug.timing["_stream_start"] = time.monotonic() - 0.5
    debug.timing["provider_timings"] = {
        "eval_duration_ns": 2_000_000_000,  # 2 seconds
        "eval_count": 100,
    }
    _compute_final_timing(debug, "content")
    assert debug.timing["native_timing"] is True
    assert debug.timing["output_tokens"] == 100
    assert debug.timing["generation_ms"] == 2000
    assert debug.timing["tokens_per_sec"] == 50.0


def test_compute_final_timing_followup_window_path() -> None:
    debug = _debug()
    debug.timing["_stream_start"] = time.monotonic() - 0.5
    debug.timing["content_generation_ms"] = 1000
    debug.timing["usage"] = {"completion_tokens": 40}
    _compute_final_timing(debug, "content text here")
    assert debug.timing["native_timing"] is False
    assert debug.timing["generation_ms"] == 1000
    assert debug.timing["output_tokens"] == 40
    assert debug.timing["tokens_per_sec"] == 40.0


def test_compute_final_timing_fallback_subtraction_path() -> None:
    debug = _debug()
    debug.timing["_stream_start"] = time.monotonic() - 2.0
    # No provider timings, no content_generation_ms -> subtraction fallback.
    debug.timing["thinking_ms"] = 100
    debug.timing["time_to_first_token_ms"] = 50
    debug.timing["tool_calls"] = [{"duration_ms": 200}]
    _compute_final_timing(debug, "a fairly long response body for token estimation")
    assert debug.timing["native_timing"] is False
    assert debug.timing["generation_ms"] > 0
    assert debug.timing["tokens_per_sec"] > 0


# --------------------------------------------------------------------------- #
# _process_response_content  (pure)
# --------------------------------------------------------------------------- #
def test_process_response_content_strips_thinking_and_extracts_refs() -> None:
    content = (
        "<think>internal reasoning</think>"
        "The entity [[node:n1|Pierre]] is central "
        "as shown [[cite:chunk7:S1|doc.txt]]."
    )
    clean, entity_refs, chunk_cites = _process_response_content(content, [], "chat-1")
    assert "internal reasoning" not in clean
    assert "<think>" not in clean
    assert "n1" in entity_refs
    assert any(k.startswith("chunk7") for k in chunk_cites)


def test_process_response_content_reflows_trailing_punctuation() -> None:
    # Punctuation after a citation marker is moved before it.
    content = "The result is clear [[cite:c1:S3|file.txt]]."
    clean, _, _ = _process_response_content(content, [], "chat-1")
    # Punctuation now precedes the citation marker.
    assert clean.index(".") < clean.index("[[cite:")


def test_process_response_content_no_citations_passes_through() -> None:
    clean, entity_refs, chunk_cites = _process_response_content(
        "Just a plain answer.", [], "chat-1"
    )
    assert clean == "Just a plain answer."
    assert entity_refs == {}
    assert chunk_cites == {}


# --------------------------------------------------------------------------- #
# _maybe_validate_response
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_maybe_validate_response_disabled_returns_none() -> None:
    settings = _settings_mock()  # enable_response_validation False
    result = await _maybe_validate_response(
        settings, [{"role": "tool", "content": "{}"}], "answer", "chat-1"
    )
    assert result is None


@pytest.mark.asyncio
async def test_maybe_validate_response_no_tool_results_returns_none() -> None:
    settings = _settings_mock()
    settings.chat_context.enable_response_validation = True
    result = await _maybe_validate_response(settings, [], "answer", "chat-1")
    assert result is None


@pytest.mark.asyncio
async def test_maybe_validate_response_prefers_citation_path_when_citations() -> None:
    settings = _settings_mock()
    settings.chat_context.enable_response_validation = True
    tool_results = [{"role": "tool", "content": "{}"}]

    with (
        patch.object(
            handler_mod,
            "validate_citation_references",
            new=AsyncMock(return_value={"verdict": "grounded", "reason": "ok"}),
        ) as cite_val,
        patch.object(handler_mod, "validate_response_grounding", new=AsyncMock()) as ground_val,
    ):
        result = await _maybe_validate_response(
            settings, tool_results, "answer", "chat-1", chunk_citations={"c1": {}}
        )

    assert result == {"verdict": "grounded", "reason": "ok"}
    cite_val.assert_awaited_once()
    ground_val.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_validate_response_grounding_path_without_citations() -> None:
    settings = _settings_mock()
    settings.chat_context.enable_response_validation = True
    tool_results = [{"role": "tool", "content": "{}"}]

    with (
        patch.object(
            handler_mod,
            "validate_response_grounding",
            new=AsyncMock(return_value={"verdict": "ungrounded", "reason": "no match"}),
        ) as ground_val,
        patch.object(handler_mod, "validate_citation_references", new=AsyncMock()) as cite_val,
    ):
        result = await _maybe_validate_response(
            settings, tool_results, "answer", "chat-1", chunk_citations=None
        )

    assert result == {"verdict": "ungrounded", "reason": "no match"}
    ground_val.assert_awaited_once()
    cite_val.assert_not_called()


# --------------------------------------------------------------------------- #
# _save_and_emit_done
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_save_and_emit_done_persists_and_emits_done() -> None:
    chat_service = MagicMock()
    debug = _debug()
    debug.timing["_stream_start"] = time.monotonic()

    with patch.object(handler_mod, "_track_streaming_tokens", new=AsyncMock()) as track:
        frames = await _collect(
            _save_and_emit_done(
                content="<think>hidden</think>Final answer.",
                thinking=None,
                chat_id="chat-1",
                chat_service=chat_service,
                messages_for_llm=[{"role": "user", "content": "hi"}],
                iterations=0,
                tool_calls_made=0,
                llm_debug=debug,
                settings=_settings_mock(),
            )
        )

    parsed = [_parse_sse(f) for f in frames]
    types = [p["type"] for p in parsed]
    assert "done" in types
    done = next(p for p in parsed if p["type"] == "done")
    assert done["content"] == "Final answer."
    assert "llm_debug" in done
    # Assistant message saved with cleaned content, status set active.
    chat_service.add_message.assert_called_once()
    assert chat_service.add_message.call_args.args[2] == "Final answer."
    chat_service.update_chat_status.assert_called_once_with("chat-1", "active")
    track.assert_awaited_once()


@pytest.mark.asyncio
async def test_save_and_emit_done_emits_thinking_event_when_present() -> None:
    chat_service = MagicMock()

    with patch.object(handler_mod, "_track_streaming_tokens", new=AsyncMock()):
        frames = await _collect(
            _save_and_emit_done(
                content="Answer body.",
                thinking="my reasoning",
                chat_id="chat-1",
                chat_service=chat_service,
                messages_for_llm=[],
                iterations=2,
                tool_calls_made=1,
                llm_debug=None,
                settings=_settings_mock(),
            )
        )

    parsed = [_parse_sse(f) for f in frames]
    types = [p["type"] for p in parsed]
    assert types[0] == "thinking"
    done = next(p for p in parsed if p["type"] == "done")
    assert done["thinking"] == "my reasoning"
    assert done["iterations"] == 2


# --------------------------------------------------------------------------- #
# _attempt_recovery_call
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_attempt_recovery_call_success_returns_content() -> None:
    # CONTENT_ONLY fake yields real content -> recovery succeeds.
    provider = FakeChatLLMProvider(strategy=ChatResponseStrategy.CONTENT_ONLY)
    result = await _attempt_recovery_call(
        chat_provider=provider,
        messages_for_llm=[{"role": "user", "content": "hi"}],
        chat_id="chat-1",
        settings=_settings_mock(),
    )
    assert result == "Hello from the fake assistant."


@pytest.mark.asyncio
async def test_attempt_recovery_call_empty_falls_back() -> None:
    # Provider returns a stream whose done chunk has empty content.
    class _EmptyStream:
        def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
            return self._iter()

        async def _iter(self) -> AsyncIterator[dict[str, Any]]:
            yield {"type": "done", "content": ""}

        async def aclose(self) -> None:
            pass

    provider = MagicMock()
    provider.chat = AsyncMock(return_value=_EmptyStream())

    result = await _attempt_recovery_call(
        chat_provider=provider,
        messages_for_llm=[],
        chat_id="chat-1",
        settings=_settings_mock(),
    )
    # Falls back to the after-tools canned message.
    assert "executed the tool(s)" in result


@pytest.mark.asyncio
async def test_attempt_recovery_call_exception_falls_back() -> None:
    provider = MagicMock()
    provider.chat = AsyncMock(side_effect=RuntimeError("provider down"))

    result = await _attempt_recovery_call(
        chat_provider=provider,
        messages_for_llm=[],
        chat_id="chat-1",
        settings=_settings_mock(),
    )
    assert "executed the tool(s)" in result


# --------------------------------------------------------------------------- #
# _finalize_response
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_finalize_response_empty_content_emits_fallback() -> None:
    chat_service = MagicMock()

    with patch.object(handler_mod, "_track_streaming_tokens", new=AsyncMock()):
        frames = await _collect(
            _finalize_response(
                accumulated_content="   ",
                thinking=None,
                chat_id="chat-1",
                chat_service=chat_service,
                messages_for_llm=[],
                llm_debug=None,
                tools_were_available=True,
                settings=_settings_mock(),
            )
        )

    parsed = [_parse_sse(f) for f in frames]
    types = [p["type"] for p in parsed]
    # A synthesized fallback content frame precedes the done event.
    assert types[0] == "content"
    assert "done" in types
    fallback = parsed[0]["accumulated"]
    assert "tool calling" in fallback  # tools_were_available branch
    chat_service.add_message.assert_called_once()


@pytest.mark.asyncio
async def test_finalize_response_with_content_emits_done_only() -> None:
    chat_service = MagicMock()

    with patch.object(handler_mod, "_track_streaming_tokens", new=AsyncMock()):
        frames = await _collect(
            _finalize_response(
                accumulated_content="A real answer.",
                thinking=None,
                chat_id="chat-1",
                chat_service=chat_service,
                messages_for_llm=[],
                llm_debug=None,
                tools_were_available=False,
                settings=_settings_mock(),
            )
        )

    parsed = [_parse_sse(f) for f in frames]
    types = [p["type"] for p in parsed]
    # No synthesized fallback content frame; just the terminal done.
    assert types == ["done"]
    assert parsed[0]["content"] == "A real answer."
