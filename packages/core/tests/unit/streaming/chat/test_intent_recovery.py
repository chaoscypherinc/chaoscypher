# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for intent-fragment detection and finalize-time recovery.

Local models routinely end a tool loop on a short "let me / I'll ..."
narration instead of an answer (live failures d319be80 / 22e8683a). Covers:

- ``is_intent_fragment`` — the shared classifier used by both chat loops;
- (finalize-time recovery now lives in the shared loop — see core
  test_loop_protections.py)
  recovery call as an empty response, with the fragment (not the canned
  apology) kept when recovery fails.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from chaoscypher_core.streaming.chat.tools import (
    _retry_unfulfilled_intent,
    contains_leaked_tool_call,
    is_intent_fragment,
)


_FRAGMENT = "Now let me get the connections for all four characters."

# Verbatim from live failure cb4c1618: qwen3:30b hallucinated Anthropic-style
# tool-call XML as plain text instead of a native structured call.
_LEAKED_TOOL_CALL = (
    '<tool_calls>\n<invoke name="search_nodes">\n'
    '<parameter name="query">Napoleon</parameter>\n'
    '<parameter name="template_ids">["character"]</parameter>\n'
    "</invoke>\n</invoke>"
)


# ---------------------------------------------------------------------------
# is_intent_fragment
# ---------------------------------------------------------------------------


def test_intent_fragment_detects_short_narration():
    assert is_intent_fragment(_FRAGMENT) is True
    assert is_intent_fragment("I'll search for Pierre next.") is True


def test_intent_fragment_rejects_substantive_answer_with_phrase():
    long_answer = (
        "Based on the knowledge graph, let me summarize: Pierre and Natasha "
        "are married, Prince Andrew was engaged to Natasha before his death, "
        "and Napoleon connects to Pierre through the failed assassination "
        "plan and the occupation of Moscow described in the source text."
    )
    assert len(long_answer) > 200
    assert is_intent_fragment(long_answer) is False


def test_intent_fragment_rejects_empty_and_plain_text():
    assert is_intent_fragment("") is False
    assert is_intent_fragment(None) is False
    assert is_intent_fragment("   ") is False
    assert is_intent_fragment("Pierre and Natasha are married.") is False


# ---------------------------------------------------------------------------
# contains_leaked_tool_call
# ---------------------------------------------------------------------------


def test_leak_detects_live_anthropic_style_xml():
    assert contains_leaked_tool_call(_LEAKED_TOOL_CALL) is True


def test_leak_detects_common_template_formats():
    assert contains_leaked_tool_call('<tool_call>\n{"name": "search_nodes"}\n</tool_call>') is True
    assert contains_leaked_tool_call('[TOOL_CALLS] search_nodes {"query": "x"}') is True
    assert contains_leaked_tool_call("<|tool_call|>search_nodes") is True


def test_leak_ignores_normal_answers_and_reference_markup():
    assert contains_leaked_tool_call("") is False
    assert contains_leaked_tool_call(None) is False
    assert contains_leaked_tool_call("Pierre and Natasha are married.") is False
    # The app's own node/citation markup must not false-positive.
    assert (
        contains_leaked_tool_call(
            "Top nodes are [[node:d79dc42f7417|Moreau]] (0.086) [[cite:C0:S1|graph_stats.json]]."
        )
        is False
    )


def test_leak_immune_to_length_threshold():
    long_leak = _LEAKED_TOOL_CALL + '\n<invoke name="get_node_edges">' * 20
    assert len(long_leak) > 200
    assert contains_leaked_tool_call(long_leak) is True
    # is_intent_fragment alone would miss it (no intent phrase, too long).
    assert is_intent_fragment(long_leak) is False


# ---------------------------------------------------------------------------
# _retry_unfulfilled_intent — fires on leaked tool calls
# ---------------------------------------------------------------------------


async def test_retry_fires_on_leaked_tool_call_without_intent_phrase():
    """A leaked tool call triggers the retry even with no 'let me' phrasing."""
    retry_call = {"id": "c9", "function": {"name": "search_nodes", "arguments": "{}"}}

    async def _gen(**kwargs):
        yield {"type": "done", "content": "", "tool_calls": [retry_call]}

    provider = MagicMock()
    provider.chat = AsyncMock(side_effect=lambda **kwargs: _gen(**kwargs))
    settings = MagicMock()
    settings.llm.thinking_for_tools = False

    result = await _retry_unfulfilled_intent(
        followup_content=_LEAKED_TOOL_CALL,
        iteration=1,
        messages_for_llm=[{"role": "user", "content": "q"}],
        chat_provider=provider,
        available_tools=[],
        chat_id="chat-leak",
        settings=settings,
    )

    assert result == [retry_call]
    provider.chat.assert_awaited_once()
