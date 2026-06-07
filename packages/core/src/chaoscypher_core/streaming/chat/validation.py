# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Response Grounding and Citation Validation.

Validates that LLM responses are grounded in search results using
deterministic text-matching and citation reference bounds-checking.
No LLM calls are needed for validation.
"""

import json
import re
from typing import Any

import structlog

from chaoscypher_core.streaming.chat.citations import (
    CHUNK_CITATION_PATTERN,
    ChunkCitationData,
    _collect_chunk_data_from_tool_results,
)


logger = structlog.get_logger(__name__)


def _extract_search_chunks(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract search_chunks data from tool result messages.

    Args:
        tool_results: Tool result messages from the conversation.

    Returns:
        List of chunk dicts from search_chunks tool results.

    """
    search_chunks: list[dict[str, Any]] = []
    for result in tool_results:
        name = result.get("name") or (result.get("extra_metadata") or {}).get("name")
        if name != "search_chunks":
            continue
        content = result.get("content")
        if not content:
            continue
        try:
            data = json.loads(content) if isinstance(content, str) else content
        except json.JSONDecodeError, TypeError:
            continue
        if isinstance(data, dict):
            for key in ("chunks", "related_chunks"):
                chunk_list = data.get(key)
                if isinstance(chunk_list, list):
                    search_chunks.extend(chunk_list)
    return search_chunks


def _normalize_for_matching(text: str) -> str:
    """Normalize text for fuzzy substring matching.

    Collapses whitespace, normalizes dashes and quotes so that minor
    formatting differences between LLM output and source text don't
    cause false validation failures.

    Args:
        text: Raw text to normalize.

    Returns:
        Normalized text suitable for substring comparison.

    """
    # Normalize dashes (em-dash, en-dash, minus) to plain hyphen
    text = re.sub(r"[\u2014\u2013\u2012\u2015]", "-", text)
    # Normalize quotes (smart/curly -> straight)
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    # Collapse whitespace
    return re.sub(r"\s+", " ", text).strip()


def _extract_blockquote_citation_pairs(
    content: str,
) -> list[tuple[str, str]]:
    """Extract blockquote text paired with the chunk_id from its citation.

    Scans the response for blockquote runs that end with a ``[[cite:ID:...]]``
    marker and returns ``(quote_text, chunk_id)`` pairs.

    Args:
        content: Full response content with citation markers.

    Returns:
        List of (quote_text, chunk_id) tuples.

    """
    pairs: list[tuple[str, str]] = []
    lines = content.split("\n")
    bq_lines: list[str] = []

    for line in lines:
        if line.startswith(">"):
            bq_lines.append(line)
            # Check if this blockquote line contains a citation
            cite_match = CHUNK_CITATION_PATTERN.search(line)
            if cite_match:
                chunk_id = cite_match.group(1)
                # Join blockquote run and clean it
                text = " ".join(
                    re.sub(r"^>\s*", "", bl).strip().strip('"').strip("\u201c\u201d")
                    for bl in bq_lines
                    if re.sub(r"^>\s*", "", bl).strip()
                )
                text = re.sub(r"\[\[cite:[^\]]+\]\]", "", text).strip()
                if len(text) >= 20:
                    pairs.append((text, chunk_id))
                bq_lines = []
        else:
            bq_lines = []

    return pairs


def _validate_quote_against_chunks(
    quote_text: str,
    cited_chunk_id: str,
    chunk_text_map: dict[str, list[str]],
    all_normalized_texts: list[str],
) -> dict[str, str]:
    """Validate a single blockquote against chunk source texts.

    Tries the cited chunk first, then falls back to all chunks.
    Uses progressive key lengths for resilience.

    Args:
        quote_text: Clean blockquote text.
        cited_chunk_id: The chunk_id from the citation marker.
        chunk_text_map: Map of chunk_id to list of normalized texts.
        all_normalized_texts: All normalized chunk texts for fallback.

    Returns:
        Dict with verdict and reason.

    """
    normalized_quote = _normalize_for_matching(quote_text)

    for key_len in (80, 60, 40):
        if len(normalized_quote) < key_len:
            continue
        search_key = normalized_quote[:key_len]

        # Try cited chunk first
        for text in chunk_text_map.get(cited_chunk_id, []):
            if search_key in text:
                return {"verdict": "correct", "reason": "Quoted text found in source"}

        # Fallback: try all chunks
        for text in all_normalized_texts:
            if search_key in text:
                return {"verdict": "correct", "reason": "Quoted text found in source"}

    return {"verdict": "wrong", "reason": "Quoted text not found in any source"}


async def validate_response_grounding(
    tool_results: list[dict[str, Any]],
    response_content: str,
    chat_id: str,
) -> dict[str, Any]:
    """Validate whether the LLM response is grounded in the search results.

    Uses deterministic text-matching per citation: checks if each blockquoted
    text can be found in chunk content. No LLM call needed.

    Returns per-citation verdicts alongside an overall verdict:
    - All pass -> "correct", mixed -> "partial", all fail -> "wrong"
    - No blockquotes -> "skipped"

    Args:
        tool_results: Tool result messages from the conversation.
        response_content: The final response content to validate.
        chat_id: Chat ID for logging.

    Returns:
        Validation result dict with verdict, reason, and per_citation map.

    """
    try:
        search_chunks = _extract_search_chunks(tool_results)
        if not search_chunks:
            return {"verdict": "skipped", "reason": "No search results to validate against"}

        # Build chunk_id -> normalized texts map
        chunk_text_map: dict[str, list[str]] = {}
        all_normalized_texts: list[str] = []
        for c in search_chunks:
            cid = c.get("id", "")
            for field in ("original_content", "combined_content"):
                text = c.get(field, "")
                if text:
                    normalized = _normalize_for_matching(text)
                    all_normalized_texts.append(normalized)
                    if cid:
                        chunk_text_map.setdefault(cid, []).append(normalized)

        # Extract blockquote+citation pairs
        pairs = _extract_blockquote_citation_pairs(response_content)
        if not pairs:
            return {"verdict": "skipped", "reason": "No blockquotes to verify"}

        # Validate each pair
        per_citation: dict[str, dict[str, str]] = {}
        for quote_text, chunk_id in pairs:
            # If multiple blockquotes cite the same chunk, keep the last result
            result = _validate_quote_against_chunks(
                quote_text,
                chunk_id,
                chunk_text_map,
                all_normalized_texts,
            )
            per_citation[chunk_id] = result

        # Compute overall verdict
        verdicts = [v["verdict"] for v in per_citation.values()]
        correct_count = verdicts.count("correct")
        total = len(verdicts)

        if correct_count == total:
            overall = "correct"
            reason = f"All {total} citation{'s' if total != 1 else ''} verified"
        elif correct_count == 0:
            overall = "wrong"
            reason = f"None of {total} citation{'s' if total != 1 else ''} verified"
        else:
            overall = "partial"
            reason = f"{correct_count} of {total} citations verified"

        logger.info(
            "validation_result",
            chat_id=chat_id,
            verdict=overall,
            correct=correct_count,
            total=total,
        )
        return {"verdict": overall, "reason": reason, "per_citation": per_citation}

    except Exception as e:
        logger.warning(
            "chat_response_validation_error",
            chat_id=chat_id,
            error_type=type(e).__name__,
            error_message=str(e),
        )
        return {"verdict": "error", "reason": "Validation failed"}


async def validate_citation_references(
    tool_results: list[dict[str, Any]],
    citations: dict[str, ChunkCitationData],
    chat_id: str,
) -> dict[str, Any]:
    """Validate that citation sentence refs point to valid sentence offsets.

    Performs bounds-checking on each citation's sentence references (S1, S2, etc.)
    against the chunk's ``sentence_offsets`` metadata. No fuzzy text matching is
    used -- only index validation.

    Args:
        tool_results: Tool result messages from the conversation.
        citations: Mapping of chunk_id to citation data with sentence_refs.
        chat_id: Chat ID for logging.

    Returns:
        Validation result dict with overall verdict, reason, and per_citation map.

    """
    if not citations:
        return {"verdict": "skipped", "reason": "No citations to validate"}

    try:
        chunk_data_map = _collect_chunk_data_from_tool_results(tool_results)

        per_citation: dict[str, dict[str, str]] = {}

        for cite_key, citation in citations.items():
            chunk_id = citation.get("chunk_id", cite_key)

            # Look up the chunk in search results
            chunk_info = chunk_data_map.get(chunk_id)
            if not chunk_info:
                per_citation[cite_key] = {
                    "verdict": "wrong",
                    "reason": "Chunk not found in search results",
                }
                continue

            # Parse sentence refs (e.g. "S1,S2") into zero-based indices
            sentence_refs = citation.get("sentence_refs", "")
            indices = [int(s) - 1 for s in re.findall(r"S(\d+)", sentence_refs)]

            if not indices:
                per_citation[cite_key] = {
                    "verdict": "wrong",
                    "reason": "No sentence references provided",
                }
                continue

            # Get sentence offsets from chunk metadata
            chunk_meta = chunk_info.get("chunk_metadata")
            offsets: list[Any] | None = None
            if isinstance(chunk_meta, dict):
                raw_offsets = chunk_meta.get("sentence_offsets")
                if isinstance(raw_offsets, list):
                    offsets = raw_offsets

            # Older chunks may lack sentence_offsets -- treat as valid
            if offsets is None:
                per_citation[cite_key] = {
                    "verdict": "correct",
                    "reason": "Chunk has no sentence_offsets (legacy chunk)",
                }
                continue

            # Bounds check: every index must be in [0, len(offsets))
            out_of_bounds = [f"S{idx + 1}" for idx in indices if not (0 <= idx < len(offsets))]

            if not out_of_bounds:
                per_citation[cite_key] = {
                    "verdict": "correct",
                    "reason": "All sentence references are valid",
                }
            else:
                per_citation[cite_key] = {
                    "verdict": "wrong",
                    "reason": (
                        f"Out-of-bounds sentence refs: {', '.join(out_of_bounds)} "
                        f"(chunk has {len(offsets)} sentences)"
                    ),
                }

        # Compute overall verdict
        verdicts = [v["verdict"] for v in per_citation.values()]
        correct_count = verdicts.count("correct")
        total = len(verdicts)

        if correct_count == total:
            overall = "correct"
            reason = f"All {total} citation{'s' if total != 1 else ''} have valid refs"
        elif correct_count == 0:
            overall = "wrong"
            reason = f"None of {total} citation{'s' if total != 1 else ''} have valid refs"
        else:
            overall = "partial"
            reason = f"{correct_count} of {total} citations have valid refs"

        logger.info(
            "citation_reference_validation_result",
            chat_id=chat_id,
            verdict=overall,
            correct=correct_count,
            total=total,
        )
        return {"verdict": overall, "reason": reason, "per_citation": per_citation}

    except Exception as e:
        logger.warning(
            "citation_reference_validation_error",
            chat_id=chat_id,
            error_type=type(e).__name__,
            error_message=str(e),
        )
        return {"verdict": "error", "reason": "Citation validation failed"}


__all__ = [
    "validate_citation_references",
    "validate_response_grounding",
]
