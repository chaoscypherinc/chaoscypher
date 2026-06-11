# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for tool-loop context budgeting and truncation detection.

Covers the fix for silently truncated multi-hop GraphRAG chats: the
tool-calling loop grows ``messages_for_llm`` with large JSON tool results
and, before this fix, Ollama silently dropped the HEAD of the oversized
prompt (system prompt + question first) producing degraded 1-2 sentence
answers with no feedback.

- enforce_tool_loop_budget: in-place compaction of older tool results so
  the prompt fits ``context_window - response_token_reserve``.
- detect_truncation_warnings: post-call detection via ``finish_reason ==
  "length"`` (output cut off) and ``usage.prompt_tokens`` pinned at the
  context window (provider silently truncated the prompt).
- _process_iteration_stream: must pass ``finish_reason``/``usage`` through
  its internal done chunk so follow-up calls can run detection.
"""

from __future__ import annotations

from chaoscypher_core.app_config import Settings
from chaoscypher_core.streaming.chat.messages import (
    TOOL_RESULT_COMPACTION_NOTICE,
    _estimate_message_tokens_full,
    detect_truncation_warnings,
    enforce_tool_loop_budget,
)
from chaoscypher_core.streaming.chat.tools import _process_iteration_stream
from chaoscypher_core.utils.tokens import estimate_tokens_dense


def _make_settings(
    num_ctx: int = 2000,
    reserve: int = 500,
    compact_chars: int = 400,
    margin: int = 100,
) -> Settings:
    """Build isolated Settings tuned for small-window budget tests."""
    settings = Settings()
    settings.llm.chat_provider = "ollama"
    settings.llm.ollama_num_ctx = num_ctx
    settings.chat_context.response_token_reserve = reserve
    settings.chat_context.compacted_tool_result_max_chars = compact_chars
    settings.chat_context.context_overflow_warning_margin = margin
    # Keep the tool-schema reserve small so message sizes dominate the math.
    settings.chat_context.tools_token_estimate = 100
    return settings


def _tool_msg(content: str, call_id: str = "c1") -> dict:
    return {"role": "tool", "content": content, "tool_call_id": call_id, "name": "graphrag_search"}


def _assistant_tool_call_msg() -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "c1", "function": {"name": "graphrag_search", "arguments": "{}"}}],
    }


# ---------------------------------------------------------------------------
# enforce_tool_loop_budget
# ---------------------------------------------------------------------------


def test_budget_noop_when_under_budget():
    """A prompt comfortably inside the budget is left untouched."""
    settings = _make_settings(num_ctx=32768)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "question"},
        _assistant_tool_call_msg(),
        _tool_msg("small result"),
    ]
    snapshot = [dict(m) for m in messages]
    assert enforce_tool_loop_budget(messages, settings, "chat-1") is None
    assert messages == snapshot


def test_budget_noop_when_settings_none():
    """Defensive: no settings means no compaction attempt."""
    messages = [{"role": "user", "content": "x" * 100_000}]
    assert enforce_tool_loop_budget(messages, None, "chat-1") is None


def test_budget_compacts_older_tool_results_keeps_current_batch():
    """Older tool results are compacted first; the current batch survives pass 1."""
    settings = _make_settings(num_ctx=2000, reserve=500, compact_chars=400)
    old_result = "OLD" * 2000  # ~1500 tokens
    current_result = "CUR" * 700  # ~525 tokens
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "multi-hop question"},
        _assistant_tool_call_msg(),
        _tool_msg(old_result, "c1"),
        _assistant_tool_call_msg(),
        _tool_msg(current_result, "c2"),
    ]
    summary = enforce_tool_loop_budget(messages, settings, "chat-2")

    assert summary is not None
    assert summary["compacted_count"] == 1
    assert summary["tokens_after"] < summary["tokens_before"]
    assert summary["context_window"] == 2000
    assert summary["still_over_budget"] is False

    # The old tool result is head-truncated with the omission notice.
    assert messages[3]["content"].startswith("OLD")
    assert messages[3]["content"].endswith(TOOL_RESULT_COMPACTION_NOTICE)
    assert len(messages[3]["content"]) <= 400 + len(TOOL_RESULT_COMPACTION_NOTICE)

    # The current batch result and the conversation frame are untouched.
    assert messages[5]["content"] == current_result
    assert messages[0]["content"] == "sys"
    assert messages[1]["content"] == "multi-hop question"


def test_budget_compacts_current_batch_as_last_resort():
    """When older results are not enough, the current batch is compacted too."""
    settings = _make_settings(num_ctx=1000, reserve=400, compact_chars=300)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "q"},
        _assistant_tool_call_msg(),
        _tool_msg("OLD" * 800, "c1"),
        _assistant_tool_call_msg(),
        _tool_msg("CUR" * 800, "c2"),
    ]
    summary = enforce_tool_loop_budget(messages, settings, "chat-3")

    assert summary is not None
    assert summary["compacted_count"] == 2
    assert messages[3]["content"].endswith(TOOL_RESULT_COMPACTION_NOTICE)
    assert messages[5]["content"].endswith(TOOL_RESULT_COMPACTION_NOTICE)


def test_budget_reports_still_over_budget():
    """If even full compaction cannot fit, the summary says so."""
    settings = _make_settings(num_ctx=1000, reserve=900, compact_chars=300)
    # Non-tool content alone exceeds the tiny budget; nothing compactable helps.
    messages = [
        {"role": "system", "content": "S" * 4000},
        {"role": "user", "content": "q"},
        _assistant_tool_call_msg(),
        _tool_msg("T" * 4000, "c1"),
    ]
    summary = enforce_tool_loop_budget(messages, settings, "chat-4")

    assert summary is not None
    assert summary["still_over_budget"] is True


# ---------------------------------------------------------------------------
# Dense (JSON) token estimation
# (live failure 2026-06-10, chat 8790e3c2: a 102,168-char tool-loop prompt
# was estimated at 25,542 tokens by the chars/4 prose heuristic and passed
# the budget check, while Ollama processed >=32,763 real tokens — the full
# 32,768 window — leaving no room to generate; the persisted answer was the
# 2-char string "In". JSON tool results tokenize at ~3 chars/token.)
# ---------------------------------------------------------------------------


def test_estimate_tokens_dense_uses_three_chars_per_token():
    assert estimate_tokens_dense("x" * 3000) == 1000
    assert estimate_tokens_dense("") == 0
    assert estimate_tokens_dense("ab") == 1  # minimum 1 for non-empty


def test_tool_message_estimated_at_dense_ratio():
    """Tool-result content uses the dense ratio; prose roles keep chars/4."""
    tool_est = _estimate_message_tokens_full(
        _tool_msg("j" * 3000), tool_call_overhead=0, message_overhead=0
    )
    user_est = _estimate_message_tokens_full(
        {"role": "user", "content": "j" * 3000}, tool_call_overhead=0, message_overhead=0
    )
    assert tool_est == 1000
    assert user_est == 750


def test_tool_call_arguments_estimated_at_dense_ratio():
    """Tool-call argument JSON is dense content too."""
    msg = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "c1", "function": {"name": "n", "arguments": "a" * 300}}],
    }
    est = _estimate_message_tokens_full(msg, tool_call_overhead=0, message_overhead=0)
    # name "n" -> 1 token (prose), arguments 300 chars -> 100 dense tokens
    assert est == 101


def test_budget_triggers_on_live_json_dense_prompt_shape():
    """The exact message shape from the live 'In' failure must now compact.

    With chars/4 this prompt estimated ~25.5K tokens against a 28.7K budget
    (no compaction, silent overflow); dense estimation pushes it over and
    compaction must bring it back under budget.
    """
    settings = _make_settings(num_ctx=32768, reserve=4096, compact_chars=2000)
    messages = [
        {"role": "system", "content": "S" * 14460},
        {"role": "user", "content": "Which characters are most central to the knowledge graph?"},
        _assistant_tool_call_msg(),
        _tool_msg("J" * 2101, "c1"),
        _assistant_tool_call_msg(),
        _tool_msg("J" * 12196, "c2"),
        _assistant_tool_call_msg(),
        _tool_msg("J" * 70096, "c3"),
    ]
    summary = enforce_tool_loop_budget(messages, settings, "chat-live-in")

    assert summary is not None
    assert summary["still_over_budget"] is False
    # The current-batch 70K result the model is about to reason over must
    # NOT be slashed to the floor — only trimmed by the deficit (if at all).
    assert len(messages[7]["content"]) > 60_000


def test_pass2_right_sizes_current_batch_instead_of_flooring():
    """When the current batch must shrink, trim only the deficit."""
    settings = _make_settings(num_ctx=1000, reserve=400, compact_chars=300)
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "q"},
        _assistant_tool_call_msg(),
        _tool_msg("C" * 2400, "c1"),
    ]
    summary = enforce_tool_loop_budget(messages, settings, "chat-rightsize")

    assert summary is not None
    assert summary["still_over_budget"] is False
    content = messages[3]["content"]
    assert content.endswith(TOOL_RESULT_COMPACTION_NOTICE)
    # Right-sized: keeps more than the floor, less than the original.
    assert 300 + len(TOOL_RESULT_COMPACTION_NOTICE) < len(content) < 2400


def test_pass2_floors_at_compact_chars_when_deficit_huge():
    """An impossible budget still floors the current batch at max_chars."""
    settings = _make_settings(num_ctx=500, reserve=450, compact_chars=300)
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "q"},
        _assistant_tool_call_msg(),
        _tool_msg("C" * 9000, "c1"),
    ]
    summary = enforce_tool_loop_budget(messages, settings, "chat-floor")

    assert summary is not None
    content = messages[3]["content"]
    assert content.endswith(TOOL_RESULT_COMPACTION_NOTICE)
    assert len(content) <= 300 + len(TOOL_RESULT_COMPACTION_NOTICE)


# ---------------------------------------------------------------------------
# detect_truncation_warnings
# ---------------------------------------------------------------------------


def test_detect_no_warnings_on_clean_done_chunk():
    """A normal stop with a roomy window yields no warnings."""
    settings = _make_settings(num_ctx=32768)
    chunk = {"finish_reason": "stop", "usage": {"prompt_tokens": 5000}}
    assert detect_truncation_warnings(chunk, settings, "chat-5") == []


def test_detect_output_truncated_on_length_finish():
    """finish_reason == length produces an output_truncated warning."""
    settings = _make_settings()
    chunk = {"finish_reason": "length", "usage": {}}
    warnings = detect_truncation_warnings(chunk, settings, "chat-6")
    assert [w["kind"] for w in warnings] == ["output_truncated"]
    assert "cut off" in warnings[0]["message"]


def test_detect_context_overflow_when_prompt_tokens_pin_at_window():
    """prompt_tokens within the margin of num_ctx means the prompt was truncated.

    Evidence basis: Ollama logs 'truncating input prompt' and reports
    prompt_eval_count == num_ctx - 1 (e.g. 32767/32768) with done_reason
    'stop' — the count is the ONLY response-level signal.
    """
    settings = _make_settings(num_ctx=2000, margin=100)
    chunk = {"finish_reason": "stop", "usage": {"prompt_tokens": 1999}}
    warnings = detect_truncation_warnings(chunk, settings, "chat-7")
    assert [w["kind"] for w in warnings] == ["context_overflow"]
    # The message must tell the user the numbers and how to work through it.
    assert "1,999" in warnings[0]["message"]
    assert "2,000" in warnings[0]["message"]
    assert "context window" in warnings[0]["message"]
    # Ollama deployments get pointed at the concrete setting to raise.
    assert "Ollama" in warnings[0]["message"]


def test_detect_overflow_respects_margin():
    """prompt_tokens below window - margin does not warn."""
    settings = _make_settings(num_ctx=2000, margin=100)
    chunk = {"finish_reason": "stop", "usage": {"prompt_tokens": 1899}}
    assert detect_truncation_warnings(chunk, settings, "chat-8") == []


def test_detect_handles_missing_fields():
    """Chunks without usage or finish_reason produce no warnings (and no crash)."""
    settings = _make_settings()
    assert detect_truncation_warnings({}, settings, "chat-9") == []
    assert detect_truncation_warnings({"usage": None, "finish_reason": None}, settings, "c") == []
    assert detect_truncation_warnings({"usage": {"prompt_tokens": 1999}}, None, "chat-10") == []


def test_detect_both_signals_together():
    """Length finish + pinned prompt tokens yields both warnings."""
    settings = _make_settings(num_ctx=2000, margin=100)
    chunk = {"finish_reason": "length", "usage": {"prompt_tokens": 2000}}
    kinds = [w["kind"] for w in detect_truncation_warnings(chunk, settings, "chat-11")]
    assert kinds == ["output_truncated", "context_overflow"]


# ---------------------------------------------------------------------------
# _process_iteration_stream finish_reason passthrough
# ---------------------------------------------------------------------------


async def test_iteration_stream_passes_finish_reason_and_usage_through():
    """The internal done chunk must carry finish_reason + usage for detection."""

    async def fake_stream():
        yield {"type": "content", "delta": "hi", "accumulated": "hi"}
        yield {
            "type": "done",
            "content": "hi",
            "thinking": None,
            "tool_calls": None,
            "usage": {"prompt_tokens": 1999, "completion_tokens": 5, "total_tokens": 2004},
            "finish_reason": "length",
        }

    done = None
    async for chunk in _process_iteration_stream(fake_stream(), "chat-12", iteration=1):
        if chunk.get("_internal_type") == "done":
            done = chunk
    assert done is not None
    assert done["finish_reason"] == "length"
    assert done["usage"] == {"prompt_tokens": 1999, "completion_tokens": 5, "total_tokens": 2004}
