# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Loop-protection tests for the shared chat tool loop.

Ported from the queued worker's truncation-parity suite when the loop moved
into core (Phase 1a): every scenario here pins a protection earned from a
live failure — unfulfilled-intent retry (d75198c8), the forced final answer
(d319be80/22e8683a), leaked tool-call XML (cb4c1618), duplicate-call
guidance, prompt-budget compaction before follow-ups, truncation-warning
collection, and empty-answer recovery.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.app_config import Settings
from chaoscypher_core.streaming.chat import loop as chat_loop
from chaoscypher_core.streaming.chat.loop import ChatLoopDeps, consume_llm_stream
from chaoscypher_core.streaming.chat.messages import TOOL_RESULT_COMPACTION_NOTICE
from chaoscypher_core.streaming.chat.sinks import CollectingSink


async def _stream(*chunks: dict[str, Any]) -> Any:
    """An async iterator over LLM stream chunks (what provider.chat returns)."""
    for chunk in chunks:
        yield chunk


_TC1 = {"function": {"name": "graphrag_search", "arguments": "{}"}, "id": "tc-1"}
_TC2 = {"function": {"name": "get_node_edges", "arguments": "{}"}, "id": "tc-2"}


def _tc(name: str, args: str = "{}", call_id: str = "tc-1") -> dict[str, Any]:
    return {"function": {"name": name, "arguments": args}, "id": call_id}


_INTENT_FRAGMENT = "Let me now analyze the graph structure to find the most central characters."

# Verbatim from live failure cb4c1618 (qwen3:30b): tool-call XML leaked as text.
_LEAKED_TOOL_CALL = (
    '<tool_calls>\n<invoke name="search_nodes">\n'
    '<parameter name="query">Napoleon</parameter>\n'
    "</invoke>\n</invoke>"
)


def _make_real_settings() -> Settings:
    """Real Settings tuned to a tiny window so budget/overflow paths trigger."""
    settings = Settings()
    settings.llm.chat_provider = "ollama"
    settings.llm.ollama_num_ctx = 2000
    settings.chat_context.response_token_reserve = 500
    settings.chat_context.compacted_tool_result_max_chars = 400
    settings.chat_context.context_overflow_warning_margin = 100
    settings.chat_context.tools_token_estimate = 100
    return settings


def _mock_settings() -> MagicMock:
    settings = MagicMock()
    settings.llm.thinking_for_tools = False
    settings.llm.thinking_for_chat = False
    return settings


def _deps(
    chat_provider: Any,
    tool_executor: Any,
    settings: Any,
) -> tuple[ChatLoopDeps, CollectingSink]:
    sink = CollectingSink()
    chat_service = MagicMock()
    chat_service.build_message.side_effect = lambda chat_id, role, body, meta: {
        "chat_id": chat_id,
        "role": role,
        "content": body,
        "extra_metadata": meta,
    }
    deps = ChatLoopDeps(
        chat_id="c1",
        provider=chat_provider,
        tool_executor=tool_executor,
        chat_service=chat_service,
        settings=settings,
        sink=sink,
        tools=[],
    )
    return deps, sink


async def _run_loop(
    *,
    tool_calls: list[Any],
    chat_provider: Any,
    tool_executor: Any,
    settings: Any,
    messages_for_llm: list[Any] | None = None,
    warnings: list[dict[str, str]] | None = None,
) -> tuple[str, str | None, int, bool, CollectingSink]:
    deps, sink = _deps(chat_provider, tool_executor, settings)
    content, thinking, total, error, _cancelled = await chat_loop._handle_tool_loop(
        tool_calls=tool_calls,
        content="",
        thinking=None,
        messages_for_llm=messages_for_llm if messages_for_llm is not None else [],
        deps=deps,
        pending_messages=[],
        warnings=warnings if warnings is not None else [],
    )
    return content, thinking, total, error, sink


def _provider(*streams: Any) -> MagicMock:
    provider = MagicMock()
    provider.chat = AsyncMock(side_effect=list(streams))
    return provider


def _executor(result: dict[str, Any] | None = None) -> MagicMock:
    executor = MagicMock()
    executor.execute_tool = AsyncMock(return_value=result or {"ok": True})
    return executor


# ===========================================================================
# consume_llm_stream — done chunk passthrough
# ===========================================================================


async def test_consume_stream_returns_done_chunk_with_finish_reason() -> None:
    """The raw done chunk (finish_reason + usage) is returned for detection."""
    done = {
        "type": "done",
        "content": "hi",
        "thinking": None,
        "tool_calls": None,
        "usage": {"prompt_tokens": 1999, "completion_tokens": 5},
        "finish_reason": "length",
    }
    content, _thinking, _tool_calls, stream_error, done_chunk = await consume_llm_stream(
        _stream({"type": "content", "delta": "hi", "accumulated": "hi"}, done),
        CollectingSink(),
        "c1",
    )
    assert content == "hi"
    assert stream_error is False
    assert done_chunk is not None
    assert done_chunk["finish_reason"] == "length"
    assert done_chunk["usage"]["prompt_tokens"] == 1999


# ===========================================================================
# Unfulfilled-intent retry (live d75198c8 failure)
# ===========================================================================


async def test_tool_loop_retries_unfulfilled_intent_fragment() -> None:
    """A 'Let me now analyze...' follow-up with no tool calls triggers a retry."""
    chat_provider = _provider(
        _stream({"type": "done", "content": _INTENT_FRAGMENT, "tool_calls": None}),
        _stream({"type": "done", "content": "", "tool_calls": [_TC2]}),
        _stream({"type": "done", "content": "Pierre and Natasha are married.", "tool_calls": None}),
    )
    final_content, _thinking, total, error, _sink = await _run_loop(
        tool_calls=[_TC1],
        chat_provider=chat_provider,
        tool_executor=_executor(),
        settings=_mock_settings(),
    )
    assert error is False
    assert final_content == "Pierre and Natasha are married."
    assert total == 2  # tc-1 + the retried tc-2
    assert chat_provider.chat.await_count == 3


async def test_forced_final_answer_after_failed_retry() -> None:
    """A failed retry triggers one forced no-tools call that produces the answer."""
    chat_provider = _provider(
        _stream({"type": "done", "content": _INTENT_FRAGMENT, "tool_calls": None}),
        _stream({"type": "done", "content": _INTENT_FRAGMENT, "tool_calls": None}),
        _stream({"type": "done", "content": "Pierre and Natasha are married.", "tool_calls": None}),
    )
    final_content, _thinking, total, error, _sink = await _run_loop(
        tool_calls=[_TC1],
        chat_provider=chat_provider,
        tool_executor=_executor(),
        settings=_mock_settings(),
    )
    assert error is False
    assert final_content == "Pierre and Natasha are married."
    assert total == 1
    assert chat_provider.chat.await_count == 3
    # The forced call must not offer tools (forces a textual answer).
    assert chat_provider.chat.await_args_list[-1].kwargs["tools"] is None


async def test_fragment_kept_when_forced_final_returns_nothing() -> None:
    """If the forced final call yields nothing, the fragment is kept (not lost)."""
    chat_provider = _provider(
        _stream({"type": "done", "content": _INTENT_FRAGMENT, "tool_calls": None}),
        _stream({"type": "done", "content": _INTENT_FRAGMENT, "tool_calls": None}),
        _stream({"type": "done", "content": "", "tool_calls": None}),
    )
    final_content, _thinking, _total, error, _sink = await _run_loop(
        tool_calls=[_TC1],
        chat_provider=chat_provider,
        tool_executor=_executor(),
        settings=_mock_settings(),
    )
    assert error is False
    assert final_content == _INTENT_FRAGMENT


# ===========================================================================
# Leaked tool-call XML (live cb4c1618 failure)
# ===========================================================================


async def test_leaked_tool_call_retried_then_forced_answer() -> None:
    """Tool-call text leaked as content is retried, then forced into an answer."""
    chat_provider = _provider(
        _stream({"type": "done", "content": _LEAKED_TOOL_CALL, "tool_calls": None}),
        _stream({"type": "done", "content": _LEAKED_TOOL_CALL, "tool_calls": None}),
        _stream({"type": "done", "content": "Pierre and Natasha are married.", "tool_calls": None}),
    )
    final_content, _thinking, _total, error, _sink = await _run_loop(
        tool_calls=[_TC1],
        chat_provider=chat_provider,
        tool_executor=_executor(),
        settings=_mock_settings(),
    )
    assert error is False
    assert final_content == "Pierre and Natasha are married."
    assert chat_provider.chat.await_count == 3


async def test_leaked_tool_call_never_shown_when_forced_answer_fails() -> None:
    """If even the forced call leaks, content is blanked (finalize apologizes)."""
    chat_provider = _provider(
        _stream({"type": "done", "content": _LEAKED_TOOL_CALL, "tool_calls": None}),
        _stream({"type": "done", "content": _LEAKED_TOOL_CALL, "tool_calls": None}),
        _stream({"type": "done", "content": _LEAKED_TOOL_CALL, "tool_calls": None}),
    )
    final_content, _thinking, _total, error, _sink = await _run_loop(
        tool_calls=[_TC1],
        chat_provider=chat_provider,
        tool_executor=_executor(),
        settings=_mock_settings(),
    )
    assert error is False
    assert final_content == ""


# ===========================================================================
# Duplicate tool calls
# ===========================================================================


async def test_duplicate_tool_calls_skipped_with_guidance() -> None:
    """A repeated identical tool call is not re-executed; guidance is injected."""
    chat_provider = _provider(
        _stream({"type": "done", "content": "", "tool_calls": [dict(_TC1)]}),
        _stream(
            {
                "type": "done",
                "content": "Based on the knowledge graph, Pierre and Natasha are married.",
                "tool_calls": None,
            }
        ),
    )
    tool_executor = _executor()
    messages: list[dict[str, Any]] = [{"role": "user", "content": "q"}]
    final_content, _thinking, _total, error, sink = await _run_loop(
        tool_calls=[dict(_TC1)],
        chat_provider=chat_provider,
        tool_executor=tool_executor,
        settings=_mock_settings(),
        messages_for_llm=messages,
    )
    assert error is False
    assert final_content == "Based on the knowledge graph, Pierre and Natasha are married."
    assert tool_executor.execute_tool.await_count == 1
    assert len(sink.of_type("cached_tool_calls")) == 1
    guidance = [
        m for m in messages if m.get("role") == "user" and "Do NOT repeat" in m.get("content", "")
    ]
    assert len(guidance) == 1


# ===========================================================================
# Prompt budget enforcement before follow-up calls
# ===========================================================================


async def test_tool_loop_compacts_prompt_before_followup() -> None:
    """A fat tool result is compacted before the follow-up provider call."""
    recorded_messages: list[list[dict[str, Any]]] = []

    async def _recording_chat(messages: Any, **kwargs: Any) -> Any:
        recorded_messages.append([dict(m) for m in messages])
        return _stream({"type": "done", "content": "Final answer.", "tool_calls": None})

    chat_provider = MagicMock()
    chat_provider.chat = AsyncMock(side_effect=_recording_chat)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "multi-hop question"},
    ]
    final_content, _thinking, _total, error, _sink = await _run_loop(
        tool_calls=[_TC1],
        chat_provider=chat_provider,
        tool_executor=_executor({"results": ["X" * 8000]}),
        settings=_make_real_settings(),
        messages_for_llm=messages,
    )
    assert error is False
    assert final_content == "Final answer."
    tool_messages = [m for m in recorded_messages[0] if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["content"].endswith(TOOL_RESULT_COMPACTION_NOTICE)


# ===========================================================================
# Truncation warnings collected + emitted
# ===========================================================================


async def test_tool_loop_emits_and_collects_truncation_warnings() -> None:
    """A truncated follow-up emits warning events and fills the list."""
    chat_provider = _provider(
        _stream(
            {
                "type": "done",
                "content": "Confident but cut-off fragment with details and citations.",
                "tool_calls": None,
                "usage": {"prompt_tokens": 1999, "completion_tokens": 40},
                "finish_reason": "length",
            }
        ),
    )
    collected: list[dict[str, str]] = []
    _content, _thinking, _total, error, sink = await _run_loop(
        tool_calls=[_TC1],
        chat_provider=chat_provider,
        tool_executor=_executor(),
        settings=_make_real_settings(),
        warnings=collected,
    )
    assert error is False
    assert {w["kind"] for w in collected} == {"output_truncated", "context_overflow"}
    assert {p.get("kind") for p in sink.of_type("warning")} == {
        "output_truncated",
        "context_overflow",
    }


# ===========================================================================
# Tool-call ceiling + thinking join (ported from the worker pipeline suite)
# ===========================================================================


async def test_tool_loop_exceeds_total_limit_emits_warning_and_breaks() -> None:
    """Crossing MAX_TOTAL_TOOL_CALLS emits a warning and stops the loop."""
    from chaoscypher_core.services.chat.engine.constants import MAX_TOTAL_TOOL_CALLS

    oversized_batch = [
        {"function": {"name": f"t{i}", "arguments": "{}"}, "id": f"tc-{i}"}
        for i in range(MAX_TOTAL_TOOL_CALLS + 1)
    ]
    chat_provider = _provider()  # must never be called
    tool_executor = _executor()
    final_content, _thinking, total, error, sink = await _run_loop(
        tool_calls=oversized_batch,
        chat_provider=chat_provider,
        tool_executor=tool_executor,
        settings=_mock_settings(),
    )
    assert error is False
    assert total == MAX_TOTAL_TOOL_CALLS + 1
    assert any("limit" in p for p in sink.of_type("warning"))
    tool_executor.execute_tool.assert_not_awaited()


async def test_tool_loop_multi_iteration_joins_thinking_parts() -> None:
    """Thinking from each iteration is joined with separators for display."""
    chat_provider = _provider(
        _stream(
            {
                "type": "done",
                "content": "",
                "thinking": "step two reasoning",
                "tool_calls": [_TC2],
            }
        ),
        _stream(
            {
                "type": "done",
                "content": "Based on the knowledge graph, the final answer is substantive.",
                "thinking": "step three reasoning",
                "tool_calls": None,
            }
        ),
    )
    deps, _sink = _deps(chat_provider, _executor(), _mock_settings())
    content, thinking, _total, error, _cancelled = await chat_loop._handle_tool_loop(
        tool_calls=[_TC1],
        content="",
        thinking="step one reasoning",
        messages_for_llm=[],
        deps=deps,
        pending_messages=[],
        warnings=[],
    )
    assert error is False
    assert content == "Based on the knowledge graph, the final answer is substantive."
    assert thinking is not None
    assert thinking.split("\n\n---\n\n") == [
        "step one reasoning",
        "step two reasoning",
        "step three reasoning",
    ]


# ===========================================================================
# Empty-answer recovery (_resolve_final_content)
# ===========================================================================


async def test_resolve_final_content_recovers_empty_after_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty content after a tool loop forces one no-tools answer."""
    forced = AsyncMock(return_value="Recovered answer from tool results.")
    monkeypatch.setattr(chat_loop, "_force_final_answer", forced)
    deps, _ = _deps(MagicMock(), _executor(), _mock_settings())
    out = await chat_loop._resolve_final_content(
        "",
        [{"role": "tool", "content": "{}", "name": "graphrag_search"}],
        deps,
    )
    assert out == "Recovered answer from tool results."
    forced.assert_awaited_once()


async def test_resolve_final_content_empty_without_tools_skips_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No tool results = nothing to answer from; keep the apology path."""
    forced = AsyncMock(return_value="should not be called")
    monkeypatch.setattr(chat_loop, "_force_final_answer", forced)
    deps, _ = _deps(MagicMock(), _executor(), _mock_settings())
    out = await chat_loop._resolve_final_content("", [{"role": "user", "content": "hi"}], deps)
    assert out == ""
    forced.assert_not_awaited()


async def test_resolve_final_content_empty_recovery_failure_keeps_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed recovery returns empty so finalize applies the apology."""
    forced = AsyncMock(return_value="")
    monkeypatch.setattr(chat_loop, "_force_final_answer", forced)
    deps, _ = _deps(MagicMock(), _executor(), _mock_settings())
    out = await chat_loop._resolve_final_content(
        "",
        [{"role": "tool", "content": "{}", "name": "graphrag_search"}],
        deps,
    )
    assert out == ""
    forced.assert_awaited_once()


# ===========================================================================
# Tool-approval gating (ask-on-write / always-ask via ApprovalBroker)
# ===========================================================================


class _ScriptedBroker:
    """ApprovalBroker fake returning a fixed decision."""

    def __init__(self, decision: str) -> None:
        self.decision = decision
        self.requests: list[tuple[str, str]] = []

    async def request(
        self,
        chat_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        iteration: int,
    ) -> None:
        self.requests.append((tool_call_id, tool_name))

    async def wait(self, chat_id: str, tool_call_id: str, timeout_s: float) -> str:
        return self.decision


def _approval_settings(mode: str) -> Settings:
    s = Settings()
    s.llm.chat_provider = "ollama"
    s.chat.tool_approval = mode  # type: ignore[assignment]
    return s


def _mutating_tc(call_id: str = "tc-w") -> dict[str, Any]:
    return {"function": {"name": "create_node", "arguments": '{"label": "X"}'}, "id": call_id}


async def _run_gated(
    *,
    mode: str,
    decision: str,
    tool_call: dict[str, Any],
) -> tuple[MagicMock, _ScriptedBroker, list[Any], list[dict[str, Any]], CollectingSink, Any]:
    chat_provider = _provider(
        _stream({"type": "done", "content": "Done answer.", "tool_calls": None})
    )
    tool_executor = _executor()
    broker = _ScriptedBroker(decision)
    deps, sink = _deps(chat_provider, tool_executor, _approval_settings(mode))
    deps.approval = broker
    messages: list[Any] = [{"role": "user", "content": "q"}]
    pending: list[dict[str, Any]] = []
    result = await chat_loop._handle_tool_loop(
        tool_calls=[tool_call],
        content="",
        thinking=None,
        messages_for_llm=messages,
        deps=deps,
        pending_messages=pending,
        warnings=[],
    )
    return tool_executor, broker, messages, pending, sink, result


async def test_always_ask_approved_tool_executes() -> None:
    executor, broker, _messages, _pending, sink, result = await _run_gated(
        mode="always-ask", decision="approve", tool_call=_tc("graphrag_search")
    )
    _content, _thinking, _total, error, _cancelled = result
    assert error is False
    executor.execute_tool.assert_awaited_once()
    assert broker.requests == [("tc-1", "graphrag_search")]
    required = sink.of_type("tool_approval_required")
    assert len(required) == 1
    assert required[0]["tool_name"] == "graphrag_search"
    assert sink.of_type("tool_rejected") == []


async def test_always_ask_rejected_tool_skipped_with_denial_message() -> None:
    executor, _broker, messages, pending, sink, result = await _run_gated(
        mode="always-ask", decision="reject", tool_call=_mutating_tc()
    )
    _content, _thinking, _total, error, _cancelled = result
    assert error is False
    executor.execute_tool.assert_not_awaited()
    rejected = sink.of_type("tool_rejected")
    assert len(rejected) == 1
    assert rejected[0]["decision"] == "reject"
    # Denial synthesized into the LLM history AND the persistence buffer.
    denials = [m for m in messages if m.get("role") == "tool" and "reject" in m.get("content", "")]
    assert len(denials) == 1
    assert any(p["role"] == "tool" and "reject" in p["content"] for p in pending)


async def test_always_ask_timeout_denies() -> None:
    executor, _broker, _messages, _pending, sink, result = await _run_gated(
        mode="always-ask", decision="timeout", tool_call=_mutating_tc()
    )
    _content, _thinking, _total, error, _cancelled = result
    assert error is False
    executor.execute_tool.assert_not_awaited()
    assert sink.of_type("tool_rejected")[0]["decision"] == "timeout"


async def test_ask_on_write_skips_gate_for_read_tools() -> None:
    executor, broker, _messages, _pending, sink, result = await _run_gated(
        mode="ask-on-write", decision="reject", tool_call=_tc("graphrag_search")
    )
    _content, _thinking, _total, error, _cancelled = result
    assert error is False
    # Read tool runs without ever consulting the broker.
    executor.execute_tool.assert_awaited_once()
    assert broker.requests == []
    assert sink.of_type("tool_approval_required") == []


async def test_ask_on_write_gates_mutating_tools() -> None:
    executor, broker, _messages, _pending, sink, _result = await _run_gated(
        mode="ask-on-write", decision="approve", tool_call=_mutating_tc()
    )
    executor.execute_tool.assert_awaited_once()
    assert broker.requests == [("tc-w", "create_node")]
    assert sink.of_type("tool_approval_required")[0]["tool_name"] == "create_node"


async def test_never_ask_runs_tools_without_broker() -> None:
    executor, broker, _messages, _pending, sink, _result = await _run_gated(
        mode="never-ask", decision="reject", tool_call=_mutating_tc()
    )
    executor.execute_tool.assert_awaited_once()
    assert broker.requests == []
    assert sink.of_type("tool_approval_required") == []


# ===========================================================================
# iteration_progress + tool timings + mid-loop spend re-check (1b)
# ===========================================================================


async def test_loop_emits_iteration_progress_per_round() -> None:
    chat_provider = _provider(
        _stream({"type": "done", "content": "", "tool_calls": [_TC2]}),
        _stream(
            {
                "type": "done",
                "content": "Final substantive answer for this test.",
                "tool_calls": None,
            }
        ),
    )
    _content, _thinking, _total, error, sink = await _run_loop(
        tool_calls=[_TC1],
        chat_provider=chat_provider,
        tool_executor=_executor(),
        settings=_mock_settings(),
    )
    assert error is False
    progress = sink.of_type("iteration_progress")
    assert [p["iteration"] for p in progress] == [1, 2]
    assert all("max_iterations" in p for p in progress)


async def test_loop_collects_tool_timings() -> None:
    from chaoscypher_core.streaming.chat.loop import run_chat_tool_loop

    sink = CollectingSink()
    provider = _provider(
        _stream({"type": "done", "content": "", "tool_calls": [_TC1]}),
        _stream(
            {
                "type": "done",
                "content": "Final substantive answer for this test.",
                "tool_calls": None,
            }
        ),
    )
    deps, sink = _deps(provider, _executor(), _mock_settings())
    result = await run_chat_tool_loop([{"role": "user", "content": "q"}], deps)
    assert len(result.tool_timings) == 1
    assert result.tool_timings[0]["name"] == "graphrag_search"
    assert "duration_ms" in result.tool_timings[0]


async def test_mid_loop_spend_cap_breaks_gracefully() -> None:
    from chaoscypher_core.streaming.chat.loop import run_chat_tool_loop

    calls = {"n": 0}

    async def guard() -> None:
        calls["n"] += 1
        if calls["n"] > 1:  # first call passes; follow-up re-check trips
            msg = "cap"
            raise RuntimeError(msg)

    provider = _provider(
        _stream({"type": "done", "content": "", "tool_calls": [_TC1]}),
        # Never reached: the re-check breaks before the follow-up call.
    )
    deps, sink = _deps(provider, _executor(), _mock_settings())
    deps.spend_guard = guard
    result = await run_chat_tool_loop([{"role": "user", "content": "q"}], deps)

    assert result.error_occurred is False
    # The tool ran; the loop ended gracefully with a spend_cap warning.
    assert any(w["kind"] == "spend_cap" for w in result.warnings)
    assert any(p.get("kind") == "spend_cap" for p in sink.of_type("warning"))
    assert provider.chat.await_count == 1  # no follow-up call after the cap


# ===========================================================================
# Stop/cancel (Phase 2): cancellation lands at step boundaries
# ===========================================================================


def _cancel_after(n_checks: int) -> Any:
    """A cancel_check fake: False for the first ``n_checks`` polls, then True."""
    state = {"calls": 0}

    async def _check() -> bool:
        state["calls"] += 1
        return state["calls"] > n_checks

    return _check


async def test_cancel_before_first_tool_skips_batch_and_recovery() -> None:
    """A cancel landing before tool 1 stops the turn without any LLM call."""
    chat_provider = _provider(
        _stream({"type": "done", "content": "never reached", "tool_calls": None})
    )
    tool_executor = _executor()
    deps, sink = _deps(chat_provider, tool_executor, _mock_settings())
    deps.cancel_check = _cancel_after(0)
    warnings: list[dict[str, str]] = []
    # Empty content + a gathered tool result: the empty-answer recovery WOULD
    # make an LLM call here if cancellation failed to skip it.
    content, _thinking, _total, error, cancelled = await chat_loop._handle_tool_loop(
        tool_calls=[_TC1],
        content="",
        thinking=None,
        messages_for_llm=[{"role": "tool", "content": "{}", "name": "graphrag_search"}],
        deps=deps,
        pending_messages=[],
        warnings=warnings,
    )
    assert cancelled is True
    assert error is False
    tool_executor.execute_tool.assert_not_awaited()
    chat_provider.chat.assert_not_awaited()
    assert content == ""
    assert [w["kind"] for w in warnings] == ["cancelled"]
    emitted = sink.of_type("warning")
    assert len(emitted) == 1
    assert emitted[0]["kind"] == "cancelled"


async def test_cancel_mid_batch_keeps_earlier_tool_results() -> None:
    """Cancel between tool 1 and tool 2: tool 1's result survives for persistence."""
    chat_provider = _provider(
        _stream({"type": "done", "content": "never reached", "tool_calls": None})
    )
    tool_executor = _executor()
    deps, _sink = _deps(chat_provider, tool_executor, _mock_settings())
    deps.cancel_check = _cancel_after(1)  # tool 1 passes; tool 2's check trips
    pending: list[dict[str, Any]] = []
    _content, _thinking, total, error, cancelled = await chat_loop._handle_tool_loop(
        tool_calls=[_TC1, _TC2],
        content="",
        thinking=None,
        messages_for_llm=[],
        deps=deps,
        pending_messages=pending,
        warnings=[],
    )
    assert cancelled is True
    assert error is False
    assert tool_executor.execute_tool.await_count == 1
    assert total == 2  # both were counted as requested
    # Tool 1's buffered result is intact for the caller to persist.
    assert any(m.get("role") == "tool" for m in pending)
    chat_provider.chat.assert_not_awaited()


async def test_cancel_before_followup_llm_call() -> None:
    """A full batch executes, then the cancel lands before the follow-up call."""
    chat_provider = _provider(
        _stream({"type": "done", "content": "never reached", "tool_calls": None})
    )
    tool_executor = _executor()
    deps, _sink = _deps(chat_provider, tool_executor, _mock_settings())
    deps.cancel_check = _cancel_after(2)  # tools 1+2 pass; follow-up boundary trips
    _content, _thinking, _total, error, cancelled = await chat_loop._handle_tool_loop(
        tool_calls=[_TC1, _TC2],
        content="",
        thinking=None,
        messages_for_llm=[],
        deps=deps,
        pending_messages=[],
        warnings=[],
    )
    assert cancelled is True
    assert error is False
    assert tool_executor.execute_tool.await_count == 2
    chat_provider.chat.assert_not_awaited()


async def test_cancel_check_errors_fail_open() -> None:
    """A broken cancel transport never kills the turn — it completes normally."""
    chat_provider = _provider(
        _stream({"type": "done", "content": "Final answer.", "tool_calls": None})
    )
    tool_executor = _executor()
    deps, _sink = _deps(chat_provider, tool_executor, _mock_settings())
    deps.cancel_check = AsyncMock(side_effect=ConnectionError("valkey down"))
    content, _thinking, _total, error, cancelled = await chat_loop._handle_tool_loop(
        tool_calls=[_TC1],
        content="",
        thinking=None,
        messages_for_llm=[],
        deps=deps,
        pending_messages=[],
        warnings=[],
    )
    assert cancelled is False
    assert error is False
    assert content == "Final answer."
    tool_executor.execute_tool.assert_awaited_once()


async def test_run_chat_tool_loop_reports_cancelled() -> None:
    """The public loop result carries the cancelled flag for the caller."""
    from chaoscypher_core.streaming.chat.loop import run_chat_tool_loop

    provider = _provider(
        _stream({"type": "done", "content": "", "tool_calls": [_TC1]}),
    )
    deps, _sink = _deps(provider, _executor(), _mock_settings())
    deps.cancel_check = _cancel_after(0)
    result = await run_chat_tool_loop([{"role": "user", "content": "q"}], deps)
    assert result.cancelled is True
    assert result.error_occurred is False
    assert any(w["kind"] == "cancelled" for w in result.warnings)
    assert provider.chat.await_count == 1  # the initial call only


async def test_default_deps_have_no_cancel_check() -> None:
    """cancel_check defaults to None — hosts without a transport are unchanged."""
    deps, _sink = _deps(MagicMock(), _executor(), _mock_settings())
    assert deps.cancel_check is None


# ===========================================================================
# Live tool limits (Phase 5): settings knobs bind at runtime
# ===========================================================================


def _limit_settings(*, iterations: int | None = None, total: int | None = None) -> Settings:
    """Real Settings with chat tool limits overridden."""
    s = Settings()
    s.llm.chat_provider = "ollama"
    if iterations is not None:
        s.chat.max_tool_iterations = iterations
    if total is not None:
        s.chat.max_total_tool_calls = total
    return s


async def test_live_max_tool_iterations_binds() -> None:
    """chat.max_tool_iterations=1 stops after one round (was import-frozen)."""
    chat_provider = _provider(
        # Follow-up keeps asking for tools; a second round must never run.
        _stream({"type": "done", "content": "", "tool_calls": [_TC2]}),
        _stream({"type": "done", "content": "Final.", "tool_calls": None}),
    )
    tool_executor = _executor()
    _content, _thinking, total, error, _cancelled = await chat_loop._handle_tool_loop(
        tool_calls=[_TC1],
        content="",
        thinking=None,
        messages_for_llm=[],
        deps=_deps(chat_provider, tool_executor, _limit_settings(iterations=1))[0],
        pending_messages=[],
        warnings=[],
    )
    assert error is False
    assert total == 1  # only the first round's call was processed
    assert tool_executor.execute_tool.await_count == 1


async def test_live_max_total_tool_calls_binds() -> None:
    """chat.max_total_tool_calls=1 trips the limit warning on a 2-call batch."""
    chat_provider = _provider(
        _stream({"type": "done", "content": "Answer.", "tool_calls": None}),
    )
    tool_executor = _executor()
    deps, sink = _deps(chat_provider, tool_executor, _limit_settings(total=1))
    _content, _thinking, _total, error, _cancelled = await chat_loop._handle_tool_loop(
        tool_calls=[_TC1, _TC2],
        content="",
        thinking=None,
        messages_for_llm=[],
        deps=deps,
        pending_messages=[],
        warnings=[],
    )
    assert error is False
    tool_executor.execute_tool.assert_not_awaited()
    assert any("limit" in str(p).lower() for p in sink.of_type("warning"))


async def test_mocked_settings_fall_back_to_defaults() -> None:
    """Partially-mocked settings keep the schema defaults (fail-open)."""
    from chaoscypher_core.services.chat.engine.constants import (
        MAX_TOOL_ITERATIONS,
        MAX_TOTAL_TOOL_CALLS,
    )

    iterations, total = chat_loop._tool_limits(_mock_settings())
    assert iterations == MAX_TOOL_ITERATIONS
    assert total == MAX_TOTAL_TOOL_CALLS
