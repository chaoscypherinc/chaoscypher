# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for the shared chat tool loop (``streaming/chat/loop.py``).

The loop is THE single chat implementation (extracted from the queued worker
in Phase 1a of the bulletproofing campaign); these tests drive it with
scripted provider streams and a :class:`CollectingSink`, pinning the loop
protections that previously lived in the worker test suites:

- stream consumption (content / thinking / error / done, ``aclose``)
- first-call and follow-up truncation warnings (deduped by kind)
- duplicate-call filtering + all-duplicates guidance
- the total-tool-call ceiling
- unfulfilled-intent retry and leaked/empty final-content recovery
- tool execution events + ``pending_messages`` buffering
- the spend guard running before the first LLM call
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.app_config import Settings
from chaoscypher_core.streaming.chat.loop import (
    ChatLoopDeps,
    consume_llm_stream,
    run_chat_tool_loop,
)
from chaoscypher_core.streaming.chat.sinks import CollectingSink


async def _stream(*chunks: dict[str, Any]) -> Any:
    """An async iterator over LLM stream chunks (what provider.chat returns)."""
    for chunk in chunks:
        yield chunk


class _ClosableStream:
    """Async iterator over scripted chunks that records ``aclose``."""

    def __init__(self, *chunks: dict[str, Any]) -> None:
        self._chunks = list(chunks)
        self.closed = False

    def __aiter__(self) -> _ClosableStream:
        self._it = iter(self._chunks)
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            return next(self._it)
        except StopIteration:  # pragma: no cover - loop terminator
            raise StopAsyncIteration from None

    async def aclose(self) -> None:
        self.closed = True


def _tc(name: str, args: str = "{}", call_id: str = "tc-1") -> dict[str, Any]:
    return {"function": {"name": name, "arguments": args}, "id": call_id}


def _settings(num_ctx: int = 32768) -> Settings:
    s = Settings()
    s.llm.chat_provider = "ollama"
    s.llm.ollama_num_ctx = num_ctx
    return s


def _chat_service() -> MagicMock:
    svc = MagicMock()
    svc.build_message.side_effect = lambda chat_id, role, body, meta: {
        "chat_id": chat_id,
        "role": role,
        "content": body,
        "extra_metadata": meta,
    }
    return svc


def _deps(
    provider_streams: list[Any],
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_result: dict[str, Any] | None = None,
    spend_guard: Any = None,
) -> tuple[ChatLoopDeps, CollectingSink]:
    """Build loop deps with a provider that returns the scripted streams in order."""
    sink = CollectingSink()
    provider = MagicMock()
    provider.chat = AsyncMock(side_effect=provider_streams)
    executor = MagicMock()
    executor.execute_tool = AsyncMock(return_value=tool_result or {"success": True})
    deps = ChatLoopDeps(
        chat_id="chat-1",
        provider=provider,
        tool_executor=executor,
        chat_service=_chat_service(),
        settings=_settings(),
        sink=sink,
        spend_guard=spend_guard,
        tools=tools,
    )
    return deps, sink


_DONE_NO_TOOLS = {
    "type": "done",
    "content": "Final answer about the graph with plenty of substantive detail to avoid "
    "the intent-fragment heuristic firing on a short reply during these tests "
    "of the shared loop behavior across iterations and recovery paths.",
    "thinking": None,
    "tool_calls": None,
}


# ---------------------------------------------------------------------------
# consume_llm_stream
# ---------------------------------------------------------------------------


async def test_consume_stream_content_thinking_then_done() -> None:
    sink = CollectingSink()
    content, thinking, tool_calls, err, done = await consume_llm_stream(
        _stream(
            {"type": "content", "delta": "He", "accumulated": "He"},
            {"type": "content", "delta": "llo", "accumulated": "Hello"},
            {"type": "thinking_delta", "accumulated": "hmm"},
            {"type": "done", "content": "Hello", "thinking": "hmm", "tool_calls": None},
        ),
        sink,
        "chat-1",
    )
    assert (content, thinking, tool_calls, err) == ("Hello", "hmm", None, False)
    assert done is not None and done["content"] == "Hello"
    assert [d["accumulated"] for d in sink.of_type("content")] == ["He", "Hello"]
    assert sink.of_type("thinking_delta") == [{"thinking": "hmm"}]


async def test_consume_stream_error_chunk_sets_flag_and_emits() -> None:
    sink = CollectingSink()
    _, _, _, err, done = await consume_llm_stream(
        _stream({"type": "error", "error": "boom", "error_code": "LLM_ERROR"}),
        sink,
        "chat-1",
    )
    assert err is True
    assert done is None
    assert sink.of_type("error") == [{"error": "boom", "error_code": "LLM_ERROR"}]


async def test_consume_stream_closes_closable_streams() -> None:
    stream = _ClosableStream({"type": "done", "content": "x", "tool_calls": None})
    await consume_llm_stream(stream, CollectingSink(), "chat-1")
    assert stream.closed is True


# ---------------------------------------------------------------------------
# run_chat_tool_loop — no tools
# ---------------------------------------------------------------------------


async def test_loop_no_tools_returns_content() -> None:
    deps, sink = _deps([_stream(_DONE_NO_TOOLS)])
    result = await run_chat_tool_loop([{"role": "user", "content": "q"}], deps)
    assert result.content == _DONE_NO_TOOLS["content"]
    assert result.total_tool_calls == 0
    assert result.error_occurred is False
    assert result.pending_messages == []
    assert sink.of_type("tool_calls") == []


async def test_loop_initial_stream_error_short_circuits() -> None:
    deps, _ = _deps([_stream({"type": "error", "error": "down", "error_code": "LLM_ERROR"})])
    result = await run_chat_tool_loop([{"role": "user", "content": "q"}], deps)
    assert result.error_occurred is True
    assert result.error_stage == "initial_stream"


async def test_loop_spend_guard_runs_before_first_call() -> None:
    calls: list[str] = []

    async def guard() -> None:
        calls.append("guard")
        raise RuntimeError("cap reached")

    deps, _ = _deps([_stream(_DONE_NO_TOOLS)], spend_guard=guard)
    with pytest.raises(RuntimeError, match="cap reached"):
        await run_chat_tool_loop([{"role": "user", "content": "q"}], deps)
    assert calls == ["guard"]
    deps.provider.chat.assert_not_awaited()


# ---------------------------------------------------------------------------
# run_chat_tool_loop — tool execution
# ---------------------------------------------------------------------------


async def test_loop_executes_tool_and_buffers_result() -> None:
    deps, sink = _deps(
        [
            _stream({"type": "done", "content": "", "tool_calls": [_tc("search_nodes")]}),
            _stream(_DONE_NO_TOOLS),
        ],
        tool_result={"nodes": [{"id": "n1"}]},
    )
    messages = [{"role": "user", "content": "q"}]
    result = await run_chat_tool_loop(messages, deps)

    assert result.total_tool_calls == 1
    assert result.content == _DONE_NO_TOOLS["content"]
    # tool events emitted in order
    assert sink.of_type("tool_calls")[0]["iteration"] == 1
    assert sink.of_type("tool_start")[0]["tool"] == "search_nodes"
    assert sink.of_type("tool_result")[0]["tool"] == "search_nodes"
    assert "duration_ms" in sink.of_type("tool_result")[0]
    # result buffered for the caller, NOT persisted by the loop
    assert len(result.pending_messages) == 1
    assert result.pending_messages[0]["role"] == "tool"
    deps.chat_service.persist_messages.assert_not_called()
    # history got assistant tool_calls + tool result
    roles = [m["role"] for m in messages]
    assert roles == ["user", "assistant", "tool"]


async def test_loop_duplicate_calls_filtered_and_guidance_injected() -> None:
    same = _tc("search_nodes", '{"query": "Natasha"}', "tc-1")
    deps, sink = _deps(
        [
            _stream({"type": "done", "content": "", "tool_calls": [same]}),
            _stream(
                {
                    "type": "done",
                    "content": "",
                    "tool_calls": [_tc("search_nodes", '{"query": "Natasha"}', "tc-2")],
                }
            ),
            _stream(_DONE_NO_TOOLS),
        ]
    )
    messages = [{"role": "user", "content": "q"}]
    result = await run_chat_tool_loop(messages, deps)

    # Second round was all-duplicates: cached event emitted, guidance injected
    assert len(sink.of_type("cached_tool_calls")) == 1
    guidance = [m for m in messages if m["role"] == "user" and "already" in m["content"].lower()]
    assert guidance, "duplicate guidance message expected in history"
    # The duplicate was NOT executed twice
    assert deps.tool_executor.execute_tool.await_count == 1
    assert result.error_occurred is False


async def test_loop_followup_error_sets_tool_loop_stage() -> None:
    deps, _ = _deps(
        [
            _stream({"type": "done", "content": "", "tool_calls": [_tc("search_nodes")]}),
            _stream({"type": "error", "error": "mid-loop", "error_code": "LLM_ERROR"}),
        ]
    )
    result = await run_chat_tool_loop([{"role": "user", "content": "q"}], deps)
    assert result.error_occurred is True
    assert result.error_stage == "tool_loop"


async def test_loop_follow_up_truncation_warning_deduped_by_kind() -> None:
    deps, sink = _deps(
        [
            _stream({"type": "done", "content": "", "tool_calls": [_tc("a", call_id="t1")]}),
            _stream(
                {
                    "type": "done",
                    "content": "",
                    "tool_calls": [_tc("b", '{"x": 1}', "t2")],
                    "finish_reason": "length",
                    "usage": {},
                }
            ),
            _stream(
                {
                    "type": "done",
                    "content": _DONE_NO_TOOLS["content"],
                    "tool_calls": None,
                    "finish_reason": "length",
                    "usage": {},
                }
            ),
        ]
    )
    result = await run_chat_tool_loop([{"role": "user", "content": "q"}], deps)
    kinds = [w["kind"] for w in result.warnings]
    assert kinds == ["output_truncated"]  # one entry per kind per turn
    assert len([p for p in sink.of_type("warning") if p.get("kind") == "output_truncated"]) == 1


# ---------------------------------------------------------------------------
# Final-content recovery
# ---------------------------------------------------------------------------


async def test_loop_forces_final_answer_on_intent_fragment() -> None:
    deps, _ = _deps(
        [
            _stream({"type": "done", "content": "", "tool_calls": [_tc("search_nodes")]}),
            _stream(
                {
                    "type": "done",
                    "content": "Let me now analyze the graph structure.",
                    "tool_calls": None,
                }
            ),
            # _retry_unfulfilled_intent's probe call
            _stream({"type": "done", "content": "still narrating", "tool_calls": None}),
            # forced final answer
            _stream({"type": "done", "content": "The actual final answer.", "tool_calls": None}),
        ]
    )
    result = await run_chat_tool_loop([{"role": "user", "content": "q"}], deps)
    assert result.content == "The actual final answer."


async def test_loop_recovers_empty_content_after_tools() -> None:
    deps, _ = _deps(
        [
            _stream({"type": "done", "content": "", "tool_calls": [_tc("search_nodes")]}),
            _stream({"type": "done", "content": "", "tool_calls": None}),
            _stream(
                {"type": "done", "content": "Recovered from tool results.", "tool_calls": None}
            ),
        ]
    )
    result = await run_chat_tool_loop([{"role": "user", "content": "q"}], deps)
    assert result.content == "Recovered from tool results."
