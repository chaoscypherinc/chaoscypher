# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for shared chat-answer finalization (``streaming/chat/finalize.py``).

Pins the content pipeline every chat surface shares: apology fallback,
citation salvage/scrub/dedup parity, punctuation reflow, structured
reference extraction (keyed ``referenced_entities`` — the 2026-06-10
audit P1), and the optional-payload builder.
"""

from __future__ import annotations

import json

from chaoscypher_core.streaming.chat.finalize import (
    build_optional_payload,
    finalize_chat_content,
)


def _chunk_tool_msg(chunk_id: str, alias: str, original: str, filename: str = "war.txt") -> dict:
    return {
        "role": "tool",
        "name": "search_chunks",
        "content": json.dumps(
            {
                "chunks": [
                    {
                        "chunk_id": chunk_id,
                        "chunk_alias": alias,
                        "filename": filename,
                        "original_content": original,
                    }
                ]
            }
        ),
    }


def test_empty_content_gets_apology() -> None:
    out = finalize_chat_content("", None, [])
    assert out.content.startswith("I apologize")


def test_thinking_extracted_from_tags() -> None:
    out = finalize_chat_content("<think>reasoning</think>The answer is clear enough.", None, [])
    assert out.thinking == "reasoning"
    assert "reasoning" not in out.content


def test_blockquote_duplicating_citation_stripped() -> None:
    msg = _chunk_tool_msg(
        "aaaa1111-2222-3333-4444-555566667777", "C0", "Quoted sentence from the source text here."
    )
    out = finalize_chat_content(
        '> "Quoted sentence from the source text here." [[cite:C0:S1|war.txt]]', None, [msg]
    )
    assert "Quoted sentence" not in out.content
    assert "[[cite:aaaa1111-2222-3333-4444-555566667777:S1|war.txt]]" in out.content


def test_punctuation_reflows_before_citation() -> None:
    msg = _chunk_tool_msg("bbbb1111-2222-3333-4444-555566667777", "C0", "body")
    out = finalize_chat_content("The result is clear [[cite:C0:S1|war.txt]].", None, [msg])
    assert out.content.index(".") < out.content.index("[[cite:")


def test_mixed_ref_citation_salvaged_and_unknown_alias_scrubbed() -> None:
    msg = _chunk_tool_msg("cccc1111-2222-3333-4444-555566667777", "C1", "body")
    out = finalize_chat_content("Son of Vasíli [[cite:C1:S15,C17|war.txt]] indeed.", None, [msg])
    assert "[[cite:cccc1111-2222-3333-4444-555566667777:S15|war.txt]]" in out.content
    assert "C17" not in out.content


def test_entity_refs_extracted_and_payload_keyed_referenced_entities() -> None:
    node_msg = {
        "role": "tool",
        "name": "search_nodes",
        "content": json.dumps(
            {"nodes": [{"id": "node_abc1", "template_name": "Person", "description": "a count"}]}
        ),
    }
    out = finalize_chat_content(
        "Meet [[node:node_abc1|Pierre]] in this answer with enough detail to stand alone.",
        None,
        [node_msg],
    )
    assert "node_abc1" in out.entity_refs
    payload = build_optional_payload(out, warnings=[{"kind": "x", "message": "y"}])
    # The frontend reads referenced_entities — never entity_references.
    assert "referenced_entities" in payload
    assert "entity_references" not in payload
    assert payload["warnings"] == [{"kind": "x", "message": "y"}]


def test_all_tool_calls_collected_from_history() -> None:
    tc = {"function": {"name": "search", "arguments": "{}"}, "id": "t1"}
    history = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [tc]},
        {"role": "tool", "name": "search", "content": "{}"},
    ]
    out = finalize_chat_content(
        "A sufficiently substantive final answer for the test.", None, history
    )
    assert out.all_tool_calls == [tc]


def test_optional_payload_omits_absent_values() -> None:
    out = finalize_chat_content("A sufficiently substantive final answer for the test.", None, [])
    payload = build_optional_payload(out, warnings=None)
    assert "thinking" not in payload
    assert "chunk_citations" not in payload
    assert "referenced_entities" not in payload
    assert "warnings" not in payload


# ---------------------------------------------------------------------------
# validate_finalized_answer (moved from the SSE handler — web parity)
# ---------------------------------------------------------------------------


async def test_validation_skipped_when_disabled(monkeypatch) -> None:
    from unittest.mock import MagicMock

    from chaoscypher_core.app_config import Settings
    from chaoscypher_core.streaming.chat.finalize import validate_finalized_answer

    settings = Settings()
    settings.chat_context.enable_response_validation = False
    out = finalize_chat_content("A substantive final answer for validation tests.", None, [])
    assert (
        await validate_finalized_answer(out, [{"role": "tool", "content": "{}"}], settings, "c1")
        is None
    )
    # Partially-mocked settings skip rather than crash.
    assert await validate_finalized_answer(out, [], MagicMock(), "c1") is None


async def test_validation_prefers_citation_references(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    from chaoscypher_core.app_config import Settings
    from chaoscypher_core.streaming.chat import finalize as fin

    cite_validator = AsyncMock(return_value={"verdict": "correct", "reason": "ok"})
    ground_validator = AsyncMock(return_value={"verdict": "partial", "reason": "no"})
    monkeypatch.setattr(
        "chaoscypher_core.streaming.chat.validation.validate_citation_references",
        cite_validator,
    )
    monkeypatch.setattr(
        "chaoscypher_core.streaming.chat.validation.validate_response_grounding",
        ground_validator,
    )

    settings = Settings()
    settings.chat_context.enable_response_validation = True
    answer = fin.FinalizedAnswer(content="x", chunk_citations={"c:S1": {"chunk_id": "c"}})
    result = await fin.validate_finalized_answer(
        answer, [{"role": "tool", "content": "{}"}], settings, "c1"
    )
    assert result == {"verdict": "correct", "reason": "ok"}
    cite_validator.assert_awaited_once()
    ground_validator.assert_not_awaited()


async def test_validation_falls_back_to_grounding(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    from chaoscypher_core.app_config import Settings
    from chaoscypher_core.streaming.chat import finalize as fin

    ground_validator = AsyncMock(return_value={"verdict": "correct", "reason": "matched"})
    monkeypatch.setattr(
        "chaoscypher_core.streaming.chat.validation.validate_response_grounding",
        ground_validator,
    )

    settings = Settings()
    settings.chat_context.enable_response_validation = True
    answer = fin.FinalizedAnswer(content="x", chunk_citations={})
    result = await fin.validate_finalized_answer(
        answer, [{"role": "tool", "content": "{}"}], settings, "c1"
    )
    assert result == {"verdict": "correct", "reason": "matched"}
    ground_validator.assert_awaited_once()
