# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared chat-answer finalization.

Pure content post-processing for a completed chat turn: thinking-tag
stripping, the full citation pipeline (normalize → correct → inject →
dedup-scrubs → malformed scrub → punctuation reflow), and structured
reference extraction/enrichment. No persistence, no event emission — the
caller owns those.

Extracted from the queued worker's finalize (the most feature-complete
pipeline after the 2026-06-10 Phase-0 fixes) so every chat surface
produces identical answers.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from chaoscypher_core.utils.logging.app_config import get_logger


logger = get_logger(__name__)


@dataclass
class FinalizedAnswer:
    """A cleaned chat answer plus the structured references extracted from it."""

    content: str
    thinking: str | None = None
    chunk_citations: dict[str, Any] = field(default_factory=dict)
    entity_refs: dict[str, Any] = field(default_factory=dict)
    all_tool_calls: list[Any] = field(default_factory=list)


DECODING_METADATA_KEY = "decoding"
"""The assistant message's record of the seed it was asked with and its attempt
(:attr:`~chaoscypher_core.streaming.chat.loop.ChatLoopResult.decoding`)."""


def next_regeneration_attempt(messages: list[dict[str, Any]], last_user_id: str) -> int:
    """The decoding attempt a regenerate of the answer after ``last_user_id`` asks with.

    One past the attempt the answer being dropped recorded
    (``extra_metadata["decoding"]["attempt"]``); an answer that recorded none
    (written before audit wave 5, or no answer at all) was attempt 0. Each
    regenerate therefore asks with a new seed
    (:func:`~chaoscypher_core.utils.decoding.resample_decoding`).
    """
    after = False
    attempt = 0
    for message in messages:
        if message.get("id") == last_user_id:
            after = True
            continue
        if after and message.get("role") == "assistant":
            record = (message.get("extra_metadata") or {}).get(DECODING_METADATA_KEY) or {}
            recorded = record.get("attempt")
            if isinstance(recorded, int) and not isinstance(recorded, bool):
                attempt = max(attempt, recorded)
    return attempt + 1


def build_optional_payload(
    answer: FinalizedAnswer,
    warnings: list[dict[str, str]] | None,
    decoding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the optional keys shared by extra_metadata and the done event.

    Only truthy values are included so absent data stays absent on both the
    persisted assistant message and the published done payload.

    Args:
        answer: The finalized answer.
        warnings: Truncation warnings collected across the turn.
        decoding: The seed and attempt the turn asked with (the loop result's
            ``decoding``), which the next regenerate counts from.

    Returns:
        Dict with only the present optional entries.

    """
    payload: dict[str, Any] = {}
    if decoding:
        payload[DECODING_METADATA_KEY] = dict(decoding)
    if answer.thinking:
        payload["thinking"] = answer.thinking
    if answer.all_tool_calls:
        payload["tool_calls"] = answer.all_tool_calls
    if answer.chunk_citations:
        payload["chunk_citations"] = dict(answer.chunk_citations)
    if answer.entity_refs:
        # Keyed referenced_entities — the frontend reads this key; writing
        # entity_references silently dropped entity enrichment on the web
        # path (2026-06-10 audit P1).
        payload["referenced_entities"] = dict(answer.entity_refs)
    if warnings:
        payload["warnings"] = warnings
    return payload


def _tool_call_id(message: dict[str, Any]) -> str:
    """A tool message's call id: top-level in the model's history, in ``extra_metadata`` on a built message."""
    return str(
        message.get("tool_call_id")
        or (message.get("extra_metadata") or {}).get("tool_call_id")
        or ""
    )


def tool_result_messages(
    messages_for_llm: list[dict[str, Any]],
    tool_results: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """The tool messages the citation and entity enrichers read, each as its result's JSON.

    The model's history (``messages_for_llm``) holds what the model read: a
    handler that rendered its result (``graphrag_search``'s ``context_text``)
    is text there, which the enrichers' ``json.loads`` cannot read, and a
    long result may have been compacted. ``tool_results`` are this turn's
    tool messages as the loop buffered them (``ChatLoopResult.pending_messages``,
    ``role == "tool"``: the result's JSON). When given, they stand in for
    the history's messages of the same tool call; an earlier turn's tool
    messages (stored JSON) stay, ahead of this turn's.

    Args:
        messages_for_llm: Full message history.
        tool_results: This turn's tool messages carrying their result JSON;
            None reads the history alone.

    Returns:
        The tool messages, history order, this turn's last.

    """
    history = [m for m in messages_for_llm if m.get("role") == "tool"]
    if tool_results is None:
        return history
    replaced = {_tool_call_id(m) for m in tool_results} - {""}
    return [m for m in history if _tool_call_id(m) not in replaced] + list(tool_results)


def finalize_chat_content(
    content: str,
    thinking: str | None,
    messages_for_llm: list[dict[str, Any]],
    tool_results: list[dict[str, Any]] | None = None,
) -> FinalizedAnswer:
    """Clean a raw chat answer and extract its structured references.

    Args:
        content: Raw accumulated content (may contain <think> tags).
        thinking: Thinking content from native thinking providers (when None,
            extracted from ``<think>`` tags in the content).
        messages_for_llm: Full message history; tool messages carry the chunk
            JSON the citation enrichers match against, and assistant messages
            carry the executed tool calls.
        tool_results: This turn's tool messages with their result JSON (the
            loop's ``pending_messages`` of role ``tool``); they stand in for
            the history's rendered copies (:func:`tool_result_messages`).

    Returns:
        The finalized answer. Empty/blank content becomes the apology
        fallback string.

    """
    # Imported from the package barrel (lazily, to avoid an import cycle with
    # __init__) — the barrel is the established monkeypatch surface for these
    # helpers across the chat test suites.
    from chaoscypher_core.streaming.chat import (
        _strip_blockquotes_before_citations,
        _strip_inline_quotes_before_citations,
        correct_mismatched_citations,
        enrich_chunk_citations_from_tool_results,
        enrich_entity_references_from_tool_results,
        extract_chunk_citations,
        extract_entity_references,
        inject_citations_for_uncited_paragraphs,
        inject_citations_into_blockquotes,
        normalize_chunk_references,
        relocate_grouped_citations,
        strip_duplicated_citation_text,
        strip_malformed_citations,
        strip_thinking_tags,
    )
    from chaoscypher_core.streaming.chat.utils import extract_thinking_from_tags

    clean_content = strip_thinking_tags(content) if content else ""
    if not clean_content.strip():
        clean_content = "I apologize, but I was unable to generate a response. Please try again."

    # Extract thinking from <think> tags if not already provided
    if not thinking:
        thinking = extract_thinking_from_tags(content) if content else None

    # ---- Citation post-processing ------------------------------------------
    # The tool messages as JSON (the chunks and graph entities the model was
    # given); the citation injectors match quoted prose back to them.
    tool_results = tool_result_messages(messages_for_llm, tool_results)

    if tool_results and clean_content:
        clean_content = normalize_chunk_references(clean_content, tool_results)
        clean_content = correct_mismatched_citations(clean_content, tool_results)
        # Move citations the model grouped at the end (or orphaned on their own
        # line) to the paragraph whose quoted phrase they support, so each lands
        # after its quoted section instead of floating at the bottom.
        clean_content = relocate_grouped_citations(clean_content, tool_results)
        clean_content = inject_citations_into_blockquotes(clean_content, tool_results)
        # Fallback: when the LLM forgot the [[cite:...]] marker but quoted
        # chunk text inline, append a marker so the UI can render the
        # supporting blockquote / pill instead of leaving the claim unsourced.
        clean_content = inject_citations_for_uncited_paragraphs(clean_content, tool_results)
        # Strip prose that duplicates what the citation blockquote will
        # render (the marker shows the source text).
        clean_content = _strip_blockquotes_before_citations(clean_content)
        clean_content = _strip_inline_quotes_before_citations(clean_content)
    elif clean_content:
        clean_content = normalize_chunk_references(clean_content, None)

    # Final scrub: any marker still malformed or alias-unresolved would render
    # as raw [[cite:...]] text or a dead chip — drop it instead.
    clean_content = strip_malformed_citations(clean_content)
    # Move trailing punctuation before citation markers so the sentence reads
    # naturally and the punctuation isn't orphaned below the blockquote.
    clean_content = re.sub(
        r"(?<=\S)\s*(\[\[cite:[^\]]+\]\])\s*([.;,!?])",
        r"\2 \1",
        clean_content,
    )

    # Extract structured references so the frontend can hydrate inline
    # citation chips and entity links.
    chunk_citations = extract_chunk_citations(clean_content)
    if tool_results and chunk_citations:
        chunk_citations = enrich_chunk_citations_from_tool_results(chunk_citations, tool_results)
        # Once enriched sentence_text is present, drop any prose that
        # duplicates the cited quote (the UI already renders it once via
        # the citation blockquote).
        clean_content = strip_duplicated_citation_text(clean_content, chunk_citations)

    entity_refs = extract_entity_references(clean_content)
    if tool_results and entity_refs:
        entity_refs = enrich_entity_references_from_tool_results(entity_refs, tool_results)
    # ------------------------------------------------------------------------

    # Collect all tool call objects from the message history
    all_tool_calls = [
        tc
        for msg in messages_for_llm
        if msg.get("role") == "assistant" and msg.get("tool_calls")
        for tc in msg["tool_calls"]
    ]

    return FinalizedAnswer(
        content=clean_content,
        thinking=thinking,
        chunk_citations=dict(chunk_citations),
        entity_refs=dict(entity_refs),
        all_tool_calls=all_tool_calls,
    )


async def validate_finalized_answer(
    answer: FinalizedAnswer,
    messages_for_llm: list[dict[str, Any]],
    settings: Any,
    chat_id: str,
    tool_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Run post-response validation when enabled.

    Prefers reference-based validation when chunk citations are available,
    falling back to deterministic text-matching against search chunks.
    Moved from the SSE handler so every chat surface gets verdicts —
    ``enable_response_validation`` defaulted on but never ran for web chat
    (2026-06-10 audit P2).

    Args:
        answer: The finalized answer (content + extracted citations).
        messages_for_llm: Full message history (tool messages are the
            grounding corpus).
        settings: Application settings; None or partially-mocked objects
            skip validation.
        chat_id: Chat ID for logging.
        tool_results: This turn's tool messages with their result JSON, as
            :func:`finalize_chat_content` takes them.

    Returns:
        Validation result dict, or None when skipped/disabled.

    """
    try:
        enabled = bool(settings and settings.chat_context.enable_response_validation is True)
    except AttributeError:
        return None
    tool_results = tool_result_messages(messages_for_llm, tool_results)
    if not (enabled and tool_results):
        return None

    from chaoscypher_core.streaming.chat.validation import (
        validate_citation_references,
        validate_response_grounding,
    )

    if answer.chunk_citations:
        result = await validate_citation_references(
            tool_results=tool_results,
            citations=answer.chunk_citations,
            chat_id=chat_id,
        )
    else:
        result = await validate_response_grounding(
            tool_results=tool_results,
            response_content=answer.content,
            chat_id=chat_id,
        )

    logger.info(
        "chat_response_validation",
        chat_id=chat_id,
        verdict=result.get("verdict"),
        reason=result.get("reason"),
    )
    return result


__all__ = [
    "DECODING_METADATA_KEY",
    "FinalizedAnswer",
    "build_optional_payload",
    "finalize_chat_content",
    "next_regeneration_attempt",
    "tool_result_messages",
    "validate_finalized_answer",
]
