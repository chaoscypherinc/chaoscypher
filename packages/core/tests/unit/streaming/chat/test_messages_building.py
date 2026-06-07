# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for streaming/chat/messages.py.

Covers the pure token-budget / message-building helpers:
- _count_tool_call_tokens (str vs dict arguments + overhead)
- _estimate_message_tokens_full (content + tool_calls + structure overhead)
- _estimate_context_budget (with real settings vs default ChatContextSettings,
  plus the min-budget clamp)
- _collect_messages_within_budget (newest-to-oldest truncation +
  first_in_context_index)
- _convert_message_to_llm_format (tool / assistant+tool_calls+thinking-strip /
  user)
- build_messages_for_llm (system prompt, source-scope augmentation,
  fallback-error filtering, ContextInfo math)
- log_messages_debug (smoke -- must not raise)
"""

from __future__ import annotations

from chaoscypher_core.app_config import ChatContextSettings, get_settings
from chaoscypher_core.services.chat.engine.constants import SYSTEM_PROMPT
from chaoscypher_core.streaming.chat import messages as msg_mod
from chaoscypher_core.streaming.chat.messages import (
    ContextInfo,
    MessageBuildResult,
    _collect_messages_within_budget,
    _convert_message_to_llm_format,
    _count_tool_call_tokens,
    _estimate_context_budget,
    _estimate_message_tokens_full,
    build_messages_for_llm,
    log_messages_debug,
)
from chaoscypher_core.utils.tokens import estimate_tokens


# ---------------------------------------------------------------------------
# _count_tool_call_tokens
# ---------------------------------------------------------------------------


def test_count_tool_call_tokens_string_args_with_overhead():
    """String arguments are token-counted directly and each call adds overhead."""
    tool_calls = [
        {"function": {"name": "search", "arguments": '{"query": "hello world"}'}},
    ]
    overhead = 10
    expected = estimate_tokens("search") + estimate_tokens('{"query": "hello world"}') + overhead
    assert _count_tool_call_tokens(tool_calls, overhead) == expected


def test_count_tool_call_tokens_dict_args_are_stringified():
    """Dict arguments are JSON-stringified before estimating tokens."""
    import json

    args = {"query": "hello world", "limit": 5}
    tool_calls = [{"function": {"name": "search", "arguments": args}}]
    overhead = 10
    expected = estimate_tokens("search") + estimate_tokens(json.dumps(args)) + overhead
    assert _count_tool_call_tokens(tool_calls, overhead) == expected


def test_count_tool_call_tokens_missing_name_and_args_only_overhead():
    """A tool call with no name and no arguments contributes only the overhead."""
    tool_calls = [{"function": {}}, {}]
    overhead = 7
    # Two calls, neither has name/args -> 2 * overhead
    assert _count_tool_call_tokens(tool_calls, overhead) == 2 * overhead


# ---------------------------------------------------------------------------
# _estimate_message_tokens_full
# ---------------------------------------------------------------------------


def test_estimate_message_tokens_content_only():
    """Plain content message: content tokens + message overhead."""
    m = {"role": "user", "content": "Hello there friend"}
    tc_overhead, msg_overhead = 10, 4
    expected = estimate_tokens("Hello there friend") + msg_overhead
    assert _estimate_message_tokens_full(m, tc_overhead, msg_overhead) == expected


def test_estimate_message_tokens_with_tool_calls_from_extra_metadata():
    """tool_calls in extra_metadata are counted on top of content + overhead."""
    m = {
        "role": "assistant",
        "content": "Let me search",
        "extra_metadata": {"tool_calls": [{"function": {"name": "search", "arguments": "{}"}}]},
    }
    tc_overhead, msg_overhead = 10, 4
    expected = (
        estimate_tokens("Let me search")
        + (estimate_tokens("search") + estimate_tokens("{}") + tc_overhead)
        + msg_overhead
    )
    assert _estimate_message_tokens_full(m, tc_overhead, msg_overhead) == expected


def test_estimate_message_tokens_tool_calls_top_level_fallback():
    """When extra_metadata lacks tool_calls, the top-level tool_calls key is used."""
    m = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": "x", "arguments": ""}}],
    }
    tc_overhead, msg_overhead = 3, 4
    # No content (empty -> 0), one tool call name 'x' (1 token) + overhead, + msg overhead
    expected = estimate_tokens("x") + tc_overhead + msg_overhead
    assert _estimate_message_tokens_full(m, tc_overhead, msg_overhead) == expected


# ---------------------------------------------------------------------------
# _estimate_context_budget
# ---------------------------------------------------------------------------


def test_estimate_context_budget_default_when_no_settings():
    """With no settings, ChatContextSettings defaults drive the budget."""
    cc = ChatContextSettings()
    system_prompt_tokens = 100
    budget, ctx_window, provider, model, returned_cc = _estimate_context_budget(
        None, system_prompt_tokens
    )
    assert ctx_window == cc.default_context_window
    assert provider == "unknown"
    assert model == "unknown"
    expected_budget = int(
        (cc.default_context_window * cc.history_allocation_percent)
        - system_prompt_tokens
        - cc.tools_token_estimate
    )
    assert budget == max(expected_budget, cc.min_history_budget_tokens)
    assert isinstance(returned_cc, ChatContextSettings)


def test_estimate_context_budget_min_clamp():
    """A huge system prompt clamps the budget to min_history_budget_tokens."""
    cc = ChatContextSettings()
    # Make system_prompt_tokens absurdly large so the raw budget goes negative.
    budget, _, _, _, _ = _estimate_context_budget(None, 10_000_000)
    assert budget == cc.min_history_budget_tokens


def test_estimate_context_budget_with_real_settings():
    """With real settings, provider/model/context come from get_context_window_for_provider."""
    settings = get_settings()
    budget, ctx_window, provider, model, cc = _estimate_context_budget(settings, 100)
    assert ctx_window > 0
    assert provider  # non-empty provider name
    assert model
    assert budget >= cc.min_history_budget_tokens


# ---------------------------------------------------------------------------
# _collect_messages_within_budget
# ---------------------------------------------------------------------------


def test_collect_messages_within_budget_truncates_oldest():
    """Newest messages are kept; oldest dropped when the budget is exhausted."""
    messages = [{"content": f"msg{i}"} for i in range(5)]
    # Each message estimates to 10 tokens via this fake estimator.
    budget = 25  # fits 2 messages (2*10=20 <= 25, third would be 30 > 25)
    selected, tokens_used, first_idx = _collect_messages_within_budget(
        messages, budget, lambda _m: 10
    )
    assert len(selected) == 2
    assert tokens_used == 20
    # The last two messages (indices 3, 4) are kept; first_in_context_index == 3
    assert first_idx == 3
    assert selected == [messages[3], messages[4]]


def test_collect_messages_within_budget_all_fit():
    """When everything fits, all messages are returned in chronological order."""
    messages = [{"content": "a"}, {"content": "b"}, {"content": "c"}]
    selected, tokens_used, first_idx = _collect_messages_within_budget(messages, 1000, lambda _m: 5)
    assert selected == messages
    assert tokens_used == 15
    assert first_idx == 0


def test_collect_messages_within_budget_none_fit():
    """A single message larger than the budget yields an empty selection."""
    messages = [{"content": "huge"}]
    selected, tokens_used, first_idx = _collect_messages_within_budget(messages, 5, lambda _m: 100)
    assert selected == []
    assert tokens_used == 0
    # first_in_context_index defaults to len(messages) when nothing is included.
    assert first_idx == len(messages)


# ---------------------------------------------------------------------------
# _convert_message_to_llm_format
# ---------------------------------------------------------------------------


def test_convert_tool_message():
    """Tool messages carry their tool_call_id from extra_metadata."""
    m = {
        "role": "tool",
        "content": "result data",
        "extra_metadata": {"tool_call_id": "call_123"},
    }
    out = _convert_message_to_llm_format(m)
    assert out == {
        "role": "tool",
        "content": "result data",
        "tool_call_id": "call_123",
    }


def test_convert_tool_message_missing_id_defaults_empty():
    """A tool message without a tool_call_id gets an empty string id."""
    m = {"role": "tool", "content": "x"}
    out = _convert_message_to_llm_format(m)
    assert out["tool_call_id"] == ""


def test_convert_assistant_strips_thinking_and_keeps_tool_calls():
    """Assistant content has <think> stripped and tool_calls preserved."""
    tool_calls = [{"function": {"name": "search", "arguments": "{}"}}]
    m = {
        "role": "assistant",
        "content": "<think>reasoning</think>Here is my answer",
        "extra_metadata": {"tool_calls": tool_calls},
    }
    out = _convert_message_to_llm_format(m)
    assert out["role"] == "assistant"
    assert out["content"] == "Here is my answer"
    assert "reasoning" not in out["content"]
    assert out["tool_calls"] == tool_calls


def test_convert_assistant_without_tool_calls_has_no_key():
    """Assistant messages without tool_calls omit the tool_calls key entirely."""
    m = {"role": "assistant", "content": "plain answer"}
    out = _convert_message_to_llm_format(m)
    assert "tool_calls" not in out
    assert out == {"role": "assistant", "content": "plain answer"}


def test_convert_user_message_strips_thinking():
    """User messages also strip thinking tags."""
    m = {"role": "user", "content": "<think>x</think>Question?"}
    out = _convert_message_to_llm_format(m)
    assert out == {"role": "user", "content": "Question?"}


# ---------------------------------------------------------------------------
# build_messages_for_llm
# ---------------------------------------------------------------------------


def test_build_messages_basic_system_and_history():
    """Result begins with the system prompt and includes converted history."""
    chat = {
        "messages": [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
        ]
    }
    result = build_messages_for_llm(chat, "chat-1")
    assert isinstance(result, MessageBuildResult)
    assert result.messages_for_llm[0]["role"] == "system"
    assert result.messages_for_llm[0]["content"] == SYSTEM_PROMPT
    roles = [m["role"] for m in result.messages_for_llm]
    assert roles == ["system", "user", "assistant"]
    info = result.context_info
    assert isinstance(info, ContextInfo)
    assert info.total_messages == 2
    assert info.messages_in_context == 2
    assert info.first_in_context_index == 0


def test_build_messages_filters_fallback_errors():
    """Messages starting with the fallback-apology string are filtered out."""
    chat = {
        "messages": [
            {"role": "user", "content": "Hi"},
            {
                "role": "assistant",
                "content": "I apologize, but I didn't generate a response. Try again.",
            },
            {"role": "assistant", "content": "Real answer"},
        ]
    }
    result = build_messages_for_llm(chat, "chat-2")
    contents = [m["content"] for m in result.messages_for_llm[1:]]
    assert "Real answer" in contents
    assert all(not c.startswith("I apologize, but I didn't generate") for c in contents)
    # Only 2 of the 3 messages survive filtering.
    assert result.context_info.total_messages == 2


def test_build_messages_with_source_scope_augments_system_prompt():
    """Source metadata appends a SOURCE SCOPE block to the system prompt."""
    chat = {"messages": [{"role": "user", "content": "Hi"}]}
    source_metadata = [{"title": "Doc A", "id": "src-1"}]
    result = build_messages_for_llm(chat, "chat-3", source_metadata=source_metadata)
    system_content = result.messages_for_llm[0]["content"]
    assert system_content.startswith(SYSTEM_PROMPT)
    assert "--- SOURCE SCOPE ---" in system_content
    assert '"Doc A" (src-1)' in system_content


def test_build_messages_context_info_token_math():
    """tokens_used in ContextInfo includes the system prompt tokens."""
    chat = {"messages": [{"role": "user", "content": "Hello world"}]}
    result = build_messages_for_llm(chat, "chat-4")
    system_prompt_tokens = estimate_tokens(SYSTEM_PROMPT)
    # tokens_used == history tokens + system prompt tokens; must exceed system alone.
    assert result.context_info.tokens_used > system_prompt_tokens


def test_build_messages_empty_chat():
    """An empty chat yields only the system message."""
    result = build_messages_for_llm({"messages": []}, "chat-5")
    assert len(result.messages_for_llm) == 1
    assert result.messages_for_llm[0]["role"] == "system"
    assert result.context_info.total_messages == 0
    assert result.context_info.messages_in_context == 0


# ---------------------------------------------------------------------------
# log_messages_debug (smoke)
# ---------------------------------------------------------------------------


def test_log_messages_debug_smoke_does_not_raise():
    """log_messages_debug exercises every metadata branch without raising."""
    messages_for_llm = [
        {"role": "system", "content": "sys"},
        {
            "role": "assistant",
            "content": "x" * 500,  # exceeds preview chars -> truncation branch
            "thinking": "some thinking",
            "tool_calls": [{"function": {"name": "search"}}],
        },
        {"role": "tool", "content": "result", "tool_call_id": "c1", "name": "search"},
        {"role": "user", "content": ""},  # empty-content branch
    ]
    chat = {"messages": [{"role": "user", "content": "Hi"}]}
    # Should complete without raising.
    log_messages_debug(messages_for_llm, chat, "chat-6")


def test_module_exports():
    """Public API surface is exported."""
    assert set(msg_mod.__all__) >= {
        "ContextInfo",
        "MessageBuildResult",
        "build_messages_for_llm",
        "log_messages_debug",
    }
