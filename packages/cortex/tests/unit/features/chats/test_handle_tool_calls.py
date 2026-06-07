# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Characterization tests for streaming._handle_tool_calls.

These tests describe the *current* behavior of the function so a future
refactor (extracting state into a dataclass, etc.) cannot silently change
it. If a test fails on first run against unmodified streaming.py, the
test is wrong, not the code — re-read the function before editing either.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from chaoscypher_core.streaming.chat.handler import _handle_tool_calls

from ._streaming_fakes import (
    make_chat_provider,
    make_chat_service,
    make_chat_stream,
    make_settings,
    make_tool_call,
    make_tool_executor,
)


# Explicit async marker: pytest-asyncio runs in strict mode when invoked
# against the cortex package's config (no pytest-asyncio auto mode set there),
# so declare it here at module level instead of relying on `asyncio_mode = auto`
# from tests/pytest.ini.
pytestmark = pytest.mark.asyncio


async def _collect(stream) -> list[bytes]:
    """Drain an async byte stream into a list."""
    return [chunk async for chunk in stream]


def _event_types(chunks: list[bytes]) -> list[str]:
    r"""Extract the event type from each yielded SSE chunk.

    ``format_sse_event`` formats chunks as ``data: {json}\n\n`` and embeds
    the event type inside the JSON payload as a ``"type"`` field, so we parse
    the JSON to recover it (there is no ``event:`` line).
    """
    types: list[str] = []
    for chunk in chunks:
        text = chunk.decode("utf-8")
        for line in text.splitlines():
            if line.startswith("data:"):
                payload = line.removeprefix("data:").strip()
                if not payload:
                    continue
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                event_type = obj.get("type")
                if isinstance(event_type, str):
                    types.append(event_type)
    return types


class TestHandleToolCallsSingleIteration:
    """Pin down the path where one tool batch finishes everything."""

    async def test_single_batch_then_final_response(self) -> None:
        # Arrange
        tool_calls = [make_tool_call("search_nodes", {"query": "Pierre"})]
        followup_stream = make_chat_stream(
            content="Found Pierre.",
            tool_calls=None,  # signals "no more tools — finalize"
        )
        provider = make_chat_provider([followup_stream])
        executor = make_tool_executor({"search_nodes": {"results": [{"id": "n1"}]}})
        chat_service = make_chat_service()
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": "find Pierre"},
        ]

        # Act
        chunks = await _collect(
            _handle_tool_calls(
                tool_calls=tool_calls,
                accumulated_content="",
                thinking=None,
                chat_id="chat-1",
                chat_service=chat_service,
                chat_provider=provider,
                tool_executor=executor,
                available_tools=[],
                messages_for_llm=messages,
                llm_debug=None,
                settings=make_settings(),
            )
        )

        # Assert: SSE event sequence
        events = _event_types(chunks)
        # iteration_progress fires before _execute_tool_batch.
        assert events[0] == "iteration_progress"
        # _execute_tool_batch emits tool_calls then tool_start/tool_result per tool.
        assert "tool_calls" in events
        assert "tool_start" in events
        assert "tool_result" in events
        # _execute_followup_call emits at least one content event.
        assert "content" in events
        # _finalize_tool_response emits a terminal done event.
        assert events[-1] == "done"

        # Assert: messages_for_llm was mutated with the assistant tool call
        # message and the tool result message in that order.
        assistant_msgs = [m for m in messages if m.get("role") == "assistant"]
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        assert len(assistant_msgs) >= 1
        assert assistant_msgs[0]["tool_calls"] == tool_calls
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["name"] == "search_nodes"

        # Assert: chat_service.add_message was called for the tool result.
        assert chat_service.add_message.called
        call_args = chat_service.add_message.call_args_list
        assert any(c.args[1] == "tool" for c in call_args)


class TestHandleToolCallsInitialThinking:
    """Pin down initial ``thinking`` argument emission.

    The initial ``thinking`` argument is emitted as the very first SSE event.
    """

    async def test_initial_thinking_emitted_first(self) -> None:
        tool_calls = [make_tool_call("search_nodes", {"q": "x"})]
        followup_stream = make_chat_stream(content="ok", tool_calls=None)
        provider = make_chat_provider([followup_stream])
        executor = make_tool_executor({"search_nodes": {}})

        chunks = await _collect(
            _handle_tool_calls(
                tool_calls=tool_calls,
                accumulated_content="",
                thinking="reasoning step 1",
                chat_id="chat-2",
                chat_service=make_chat_service(),
                chat_provider=provider,
                tool_executor=executor,
                available_tools=[],
                messages_for_llm=[{"role": "user", "content": "go"}],
                llm_debug=None,
                settings=make_settings(),
            )
        )

        events = _event_types(chunks)
        # The initial thinking event is yielded BEFORE the loop body's
        # first iteration_progress.
        assert events[0] == "thinking"
        thinking_idx = events.index("thinking")
        progress_idx = events.index("iteration_progress")
        assert thinking_idx < progress_idx


class TestHandleToolCallsMultipleIterations:
    """Pin down multi-iteration tool call handling.

    The follow-up call returns more tool calls and the loop runs another
    iteration before terminating.
    """

    async def test_two_iterations_then_finalize(self) -> None:
        first_batch = [make_tool_call("search_nodes", {"q": "Pierre"}, "c1")]
        second_batch = [make_tool_call("resolve_node", {"id": "n1"}, "c2")]

        # First followup: returns more tool calls (the second batch).
        followup_with_more = make_chat_stream(
            content="found one, looking deeper",
            tool_calls=second_batch,
        )
        # Second followup: no more tools, finalize.
        followup_done = make_chat_stream(content="done.", tool_calls=None)

        provider = make_chat_provider([followup_with_more, followup_done])
        executor = make_tool_executor(
            {
                "search_nodes": {"results": [{"id": "n1", "name": "Pierre"}]},
                "resolve_node": {"id": "n1", "name": "Pierre"},
            }
        )
        messages: list[dict[str, Any]] = [{"role": "user", "content": "find Pierre"}]

        chunks = await _collect(
            _handle_tool_calls(
                tool_calls=first_batch,
                accumulated_content="",
                thinking=None,
                chat_id="chat-3",
                chat_service=make_chat_service(),
                chat_provider=provider,
                tool_executor=executor,
                available_tools=[],
                messages_for_llm=messages,
                llm_debug=None,
                settings=make_settings(),
            )
        )

        events = _event_types(chunks)
        # Two iterations means two iteration_progress events.
        assert events.count("iteration_progress") == 2
        # Two tool batches -> two tool_calls events from _execute_tool_batch.
        assert events.count("tool_calls") == 2
        # Both tools executed -> two tool_result events.
        assert events.count("tool_result") == 2
        # Terminal done is still last.
        assert events[-1] == "done"

        # provider.chat called twice (one per follow-up).
        assert provider.chat.await_count == 2

        # messages_for_llm contains an assistant message per iteration plus
        # one tool result per executed tool call.
        assistant = [m for m in messages if m.get("role") == "assistant"]
        tool = [m for m in messages if m.get("role") == "tool"]
        assert len(assistant) == 2
        assert len(tool) == 2


class TestHandleToolCallsDuplicates:
    """Pin down the duplicate-detection and cached_tool_calls emission."""

    async def test_within_batch_duplicate_filtered_and_cached_event_emitted(
        self,
    ) -> None:
        # Same tool, same arguments, twice in one batch.
        tc1 = make_tool_call("search_nodes", {"q": "Pierre"}, "c1")
        tc2 = make_tool_call("search_nodes", {"q": "Pierre"}, "c2")

        followup = make_chat_stream(content="done", tool_calls=None)
        provider = make_chat_provider([followup])
        executor = make_tool_executor({"search_nodes": {"results": []}})

        chunks = await _collect(
            _handle_tool_calls(
                tool_calls=[tc1, tc2],
                accumulated_content="",
                thinking=None,
                chat_id="chat-4",
                chat_service=make_chat_service(),
                chat_provider=provider,
                tool_executor=executor,
                available_tools=[],
                messages_for_llm=[{"role": "user", "content": "find Pierre"}],
                llm_debug=None,
                settings=make_settings(),
            )
        )

        events = _event_types(chunks)
        # The second call is a within-batch duplicate -> cached_tool_calls
        # event is emitted before the tool_calls / tool_start events.
        assert "cached_tool_calls" in events
        cached_idx = events.index("cached_tool_calls")
        tool_calls_idx = events.index("tool_calls")
        assert cached_idx < tool_calls_idx

        # Only the non-duplicate was actually executed by the tool executor.
        assert executor.execute_tool.await_count == 1


class TestHandleToolCallsLimit:
    """Pin down the MAX_TOTAL_TOOL_CALLS warning + break behavior."""

    async def test_limit_exceeded_emits_warning_and_breaks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force the limit very low so the first iteration trips it.
        # Patch the name in tools.py where _check_tool_call_limit reads it,
        # not the re-export on the streaming package __init__.
        monkeypatch.setattr(
            "chaoscypher_core.streaming.chat.tools.MAX_TOTAL_TOOL_CALLS",
            1,
        )

        # Two tool calls in the very first batch -> total = 2 > limit = 1.
        tc1 = make_tool_call("search_nodes", {"q": "a"}, "c1")
        tc2 = make_tool_call("search_nodes", {"q": "b"}, "c2")

        # NOTE: The plan originally proposed a strict "must not be called"
        # provider mock here. But when the loop breaks with latest_content="",
        # control falls through to _finalize_tool_response, which detects the
        # empty response and invokes _attempt_recovery_call — and that calls
        # chat_provider.chat() exactly once. So we give the provider a real
        # recovery stream, and instead prove the break happened by asserting
        # no tool_calls / tool_start events fired (i.e. _execute_tool_batch
        # and _execute_followup_call never ran) and provider.chat was called
        # at most once (only by recovery, never by the loop's followup).
        recovery_stream = make_chat_stream(content="fallback", tool_calls=None)
        provider = make_chat_provider([recovery_stream])

        executor = make_tool_executor({"search_nodes": {}})

        chunks = await _collect(
            _handle_tool_calls(
                tool_calls=[tc1, tc2],
                accumulated_content="",
                thinking=None,
                chat_id="chat-5",
                chat_service=make_chat_service(),
                chat_provider=provider,
                tool_executor=executor,
                available_tools=[],
                messages_for_llm=[{"role": "user", "content": "x"}],
                llm_debug=None,
                settings=make_settings(),
            )
        )

        events = _event_types(chunks)
        # iteration_progress emitted before the limit check.
        assert events[0] == "iteration_progress"
        # warning event emitted by _check_tool_call_limit.
        assert "warning" in events
        # Loop broke immediately — no tool_calls / tool_start events fired,
        # because the break happens before _execute_tool_batch.
        assert "tool_calls" not in events
        assert "tool_start" not in events
        # Tool executor was never invoked (break before _execute_tool_batch).
        assert executor.execute_tool.await_count == 0
        # Finalize still runs and emits done.
        assert events[-1] == "done"
        # provider.chat was called at most once — only by the recovery path
        # in _finalize_tool_response (NOT by _execute_followup_call).
        assert provider.chat.await_count <= 1
