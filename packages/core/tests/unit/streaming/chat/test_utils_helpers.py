# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for streaming/chat/utils.py pure helpers.

Covers (leaving the heavy ``setup_chat_providers`` factory wiring alone):
- select_tools (essential-first ordering + MAX_TOOLS cap + dedup)
- parse_tool_arguments (valid JSON / invalid -> {} / dict passthrough)
- create_fallback_response (all four branches)
- extract_thinking_from_tags / strip_thinking_tags (multiple blocks, empty)
- get_model_name / get_context_window_for_provider (patched get_provider_config)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from chaoscypher_core.llm_queue.provider_utils import ProviderConfig
from chaoscypher_core.services.chat.engine.constants import ESSENTIAL_TOOL_NAMES, MAX_TOOLS
from chaoscypher_core.streaming.chat.utils import (
    create_fallback_response,
    extract_thinking_from_tags,
    get_context_window_for_provider,
    get_model_name,
    parse_tool_arguments,
    select_tools,
    strip_thinking_tags,
)


def _tool(name: str) -> dict:
    """Build a minimal tool schema dict with the given function name."""
    return {"type": "function", "function": {"name": name, "description": name}}


# ---------------------------------------------------------------------------
# select_tools
# ---------------------------------------------------------------------------


def test_select_tools_essential_first_ordering():
    """Essential tools are placed first, before non-essential ones."""
    essential = ESSENTIAL_TOOL_NAMES[0]
    tools = [_tool("non_essential_tool"), _tool(essential)]
    selected = select_tools(tools, "chat-1")
    names = [t["function"]["name"] for t in selected]
    # The essential tool comes first even though it was second in the input.
    assert names[0] == essential
    assert "non_essential_tool" in names


def test_select_tools_respects_max_tools_cap():
    """No more than MAX_TOOLS tools are returned."""
    # Build more non-essential tools than MAX_TOOLS so the cap engages.
    tools = [_tool(f"extra_{i}") for i in range(MAX_TOOLS + 10)]
    selected = select_tools(tools, "chat-2")
    assert len(selected) <= MAX_TOOLS


def test_select_tools_dedup_no_duplicates():
    """A tool already added as essential is not re-added in the second pass."""
    essential = ESSENTIAL_TOOL_NAMES[0]
    tools = [_tool(essential), _tool("other")]
    selected = select_tools(tools, "chat-3")
    # The essential tool appears exactly once.
    essential_count = sum(1 for t in selected if t["function"]["name"] == essential)
    assert essential_count == 1


# ---------------------------------------------------------------------------
# parse_tool_arguments
# ---------------------------------------------------------------------------


def test_parse_tool_arguments_valid_json_string():
    """A valid JSON string is parsed into a dict."""
    out = parse_tool_arguments('{"query": "hi", "limit": 3}', "search", "chat-1")
    assert out == {"query": "hi", "limit": 3}


def test_parse_tool_arguments_invalid_json_returns_empty():
    """Invalid JSON yields an empty dict (and logs a warning)."""
    out = parse_tool_arguments("{not json", "search", "chat-2")
    assert out == {}


def test_parse_tool_arguments_dict_passthrough():
    """A dict argument is returned unchanged."""
    arg = {"already": "parsed"}
    assert parse_tool_arguments(arg, "search", "chat-3") is arg


# ---------------------------------------------------------------------------
# create_fallback_response
# ---------------------------------------------------------------------------


def test_fallback_after_tools_branch():
    """after_tools takes priority and mentions executed tools."""
    out = create_fallback_response(has_thinking=True, after_tools=True)
    assert "executed the tool(s)" in out


def test_fallback_has_thinking_branch():
    """has_thinking (not after tools) asks the user to rephrase with more detail."""
    out = create_fallback_response(has_thinking=True, after_tools=False)
    assert "thought about your question" in out


def test_fallback_tools_available_branch():
    """No thinking but tools available -> tool-calling model recommendation."""
    out = create_fallback_response(has_thinking=False, after_tools=False, tools_were_available=True)
    assert "tool calling" in out
    assert "Recommended models" in out


def test_fallback_default_branch():
    """No thinking and no tools -> generic apology (the fallback-error marker)."""
    out = create_fallback_response(
        has_thinking=False, after_tools=False, tools_were_available=False
    )
    assert out.startswith("I apologize, but I didn't generate a response")


# ---------------------------------------------------------------------------
# extract_thinking_from_tags / strip_thinking_tags
# ---------------------------------------------------------------------------


def test_extract_thinking_multiple_blocks_joined():
    """Multiple <think> blocks are joined with blank lines."""
    content = "<think>first</think>answer<think>second</think>"
    out = extract_thinking_from_tags(content)
    assert out == "first\n\nsecond"


def test_extract_thinking_none_when_no_tags():
    """No tags -> None."""
    assert extract_thinking_from_tags("just an answer") is None


def test_extract_thinking_empty_content_returns_none():
    """Empty content short-circuits to None."""
    assert extract_thinking_from_tags("") is None


def test_extract_thinking_only_whitespace_blocks_returns_none():
    """Blocks that are only whitespace are dropped, leaving None."""
    assert extract_thinking_from_tags("<think>   </think>answer") is None


def test_strip_thinking_removes_blocks_and_dangling_close():
    """Both full blocks and a dangling </think> close tag are removed."""
    content = "<think>reasoning</think>Visible answer</think>"
    out = strip_thinking_tags(content)
    assert "reasoning" not in out
    assert "</think>" not in out
    assert out == "Visible answer"


def test_strip_thinking_empty_passthrough():
    """Empty content is returned as-is (no stripping)."""
    assert strip_thinking_tags("") == ""


# ---------------------------------------------------------------------------
# get_model_name / get_context_window_for_provider (patched provider config)
# ---------------------------------------------------------------------------


def test_get_model_name_uses_provider_config():
    """get_model_name returns the model from the provider config."""
    cfg = ProviderConfig(provider="openai", model="gpt-test", context_window=128000)
    with patch("chaoscypher_core.streaming.chat.utils.get_provider_config", return_value=cfg):
        assert get_model_name(MagicMock()) == "gpt-test"


def test_get_context_window_for_provider_returns_tuple():
    """get_context_window_for_provider returns (context_window, provider, model)."""
    cfg = ProviderConfig(provider="anthropic", model="claude-x", context_window=200000)
    with patch("chaoscypher_core.streaming.chat.utils.get_provider_config", return_value=cfg):
        window, provider, model = get_context_window_for_provider(MagicMock())
    assert window == 200000
    assert provider == "anthropic"
    assert model == "claude-x"
