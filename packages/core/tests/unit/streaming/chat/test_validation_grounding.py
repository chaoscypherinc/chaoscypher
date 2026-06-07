# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for streaming/chat/validation.py response-grounding helpers.

Covers the deterministic (no-LLM) grounding validation path:
- _extract_search_chunks (match by name / extra_metadata.name; skip malformed JSON)
- _normalize_for_matching (dash / quote / whitespace normalization)
- _extract_blockquote_citation_pairs (multi-line blockquote + min-20-char gate)
- _validate_quote_against_chunks (cited-chunk hit, all-chunks fallback,
  progressive key lengths, miss)
- validate_response_grounding (skipped when no chunks / no blockquotes;
  correct / partial / wrong verdict math; exception -> error)
"""

from __future__ import annotations

import json

import pytest

from chaoscypher_core.streaming.chat.validation import (
    _extract_blockquote_citation_pairs,
    _extract_search_chunks,
    _normalize_for_matching,
    _validate_quote_against_chunks,
    validate_response_grounding,
)


# ---------------------------------------------------------------------------
# _extract_search_chunks
# ---------------------------------------------------------------------------


def test_extract_search_chunks_by_top_level_name():
    """A tool result named search_chunks contributes its chunks list."""
    payload = {"chunks": [{"id": "c1", "original_content": "hello"}]}
    tool_results = [{"name": "search_chunks", "content": json.dumps(payload)}]
    chunks = _extract_search_chunks(tool_results)
    assert chunks == [{"id": "c1", "original_content": "hello"}]


def test_extract_search_chunks_name_in_extra_metadata():
    """The tool name may live under extra_metadata instead of the top level."""
    payload = {"related_chunks": [{"id": "c2"}]}
    tool_results = [
        {
            "extra_metadata": {"name": "search_chunks"},
            "content": json.dumps(payload),
        }
    ]
    chunks = _extract_search_chunks(tool_results)
    assert chunks == [{"id": "c2"}]


def test_extract_search_chunks_skips_wrong_name():
    """Results from other tools are ignored."""
    payload = {"chunks": [{"id": "c1"}]}
    tool_results = [{"name": "search_nodes", "content": json.dumps(payload)}]
    assert _extract_search_chunks(tool_results) == []


def test_extract_search_chunks_skips_malformed_json():
    """Invalid JSON content is skipped rather than raising."""
    tool_results = [
        {"name": "search_chunks", "content": "{not valid json"},
        {"name": "search_chunks", "content": None},  # falsy content -> skipped
    ]
    assert _extract_search_chunks(tool_results) == []


def test_extract_search_chunks_accepts_dict_content():
    """When content is already a dict, no JSON parsing is required."""
    tool_results = [
        {
            "name": "search_chunks",
            "content": {"chunks": [{"id": "c9"}], "related_chunks": [{"id": "c10"}]},
        }
    ]
    chunks = _extract_search_chunks(tool_results)
    ids = {c["id"] for c in chunks}
    assert ids == {"c9", "c10"}


# ---------------------------------------------------------------------------
# _normalize_for_matching
# ---------------------------------------------------------------------------


def test_normalize_dashes_quotes_whitespace():
    """Dashes, smart quotes and runs of whitespace are normalized."""
    raw = "  the—quick–brown   “fox” ‘jumps’\n over  "  # noqa: RUF001 - intentional unicode test input
    out = _normalize_for_matching(raw)
    assert out == "the-quick-brown \"fox\" 'jumps' over"


def test_normalize_collapses_tabs_and_newlines():
    """Tabs and newlines collapse to single spaces and ends are stripped."""
    assert _normalize_for_matching("a\t\tb\n\nc") == "a b c"


# ---------------------------------------------------------------------------
# _extract_blockquote_citation_pairs
# ---------------------------------------------------------------------------


def test_extract_blockquote_single_line_pair():
    """A single blockquote line ending in a citation yields one pair."""
    content = (
        "Intro paragraph.\n"
        "> This is a sufficiently long quoted sentence here [[cite:chunk-1:S1]]\n"
        "Outro."
    )
    pairs = _extract_blockquote_citation_pairs(content)
    assert len(pairs) == 1
    text, chunk_id = pairs[0]
    assert chunk_id == "chunk-1"
    assert "[[cite:" not in text
    assert text.startswith("This is a sufficiently long quoted sentence")


def test_extract_blockquote_multiline_joined():
    """A multi-line blockquote run is joined into a single quote text."""
    content = (
        "> first line of the quote that is long\n"
        "> second line of the quote with cite [[cite:chunk-2:S1]]\n"
    )
    pairs = _extract_blockquote_citation_pairs(content)
    assert len(pairs) == 1
    text, chunk_id = pairs[0]
    assert chunk_id == "chunk-2"
    assert "first line of the quote" in text
    assert "second line of the quote" in text


def test_extract_blockquote_too_short_is_dropped():
    """Quotes under 20 chars after cleaning are not emitted."""
    content = "> hi [[cite:chunk-3:S1]]\n"
    assert _extract_blockquote_citation_pairs(content) == []


def test_extract_blockquote_resets_run_on_non_bq_line():
    """A non-blockquote line resets the accumulated blockquote run."""
    content = (
        "> orphan blockquote line without any citation marker at all\n"
        "plain interrupting line\n"
        "> the actual cited long blockquote line goes here [[cite:chunk-4:S1]]\n"
    )
    pairs = _extract_blockquote_citation_pairs(content)
    assert len(pairs) == 1
    text, chunk_id = pairs[0]
    assert chunk_id == "chunk-4"
    # The orphan line (reset by the plain line) must not be part of the quote.
    assert "orphan blockquote line" not in text


# ---------------------------------------------------------------------------
# _validate_quote_against_chunks
# ---------------------------------------------------------------------------


def test_validate_quote_hits_cited_chunk():
    """A quote found in the cited chunk's text returns correct."""
    quote = "the quick brown fox jumps over the lazy dog repeatedly today"
    normalized = _normalize_for_matching(quote)
    chunk_map = {"c1": [normalized + " plus extra trailing context here"]}
    result = _validate_quote_against_chunks(quote, "c1", chunk_map, [chunk_map["c1"][0]])
    assert result["verdict"] == "correct"
    assert "found in source" in result["reason"]


def test_validate_quote_falls_back_to_all_chunks():
    """When the cited chunk lacks the text, the all-chunks fallback still matches."""
    quote = "the quick brown fox jumps over the lazy dog repeatedly today"
    normalized = _normalize_for_matching(quote)
    other_text = normalized + " more context"
    # Cited chunk c1 has nothing; the match lives in the global list.
    result = _validate_quote_against_chunks(quote, "c1", {"c1": ["unrelated text"]}, [other_text])
    assert result["verdict"] == "correct"


def test_validate_quote_miss_returns_wrong():
    """A quote not present anywhere returns wrong."""
    quote = "this exact sentence appears in absolutely none of the source chunks"
    result = _validate_quote_against_chunks(
        quote, "c1", {"c1": ["completely different content"]}, ["completely different content"]
    )
    assert result["verdict"] == "wrong"
    assert "not found" in result["reason"]


def test_validate_quote_short_quote_skips_all_key_lengths_and_misses():
    """A quote shorter than the smallest key length (40) yields wrong."""
    # 30-char normalized quote: shorter than the 40 key threshold, so every
    # progressive key length is skipped and the function returns wrong.
    quote = "short quote under forty chars."
    assert len(_normalize_for_matching(quote)) < 40
    result = _validate_quote_against_chunks(quote, "c1", {"c1": [quote]}, [quote])
    assert result["verdict"] == "wrong"


# ---------------------------------------------------------------------------
# validate_response_grounding (async)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grounding_skipped_when_no_chunks():
    """No search chunks -> skipped verdict."""
    result = await validate_response_grounding([], "some response", "chat-1")
    assert result["verdict"] == "skipped"
    assert "No search results" in result["reason"]


@pytest.mark.asyncio
async def test_grounding_skipped_when_no_blockquotes():
    """Chunks present but no blockquotes -> skipped verdict."""
    payload = {"chunks": [{"id": "c1", "original_content": "anything"}]}
    tool_results = [{"name": "search_chunks", "content": json.dumps(payload)}]
    result = await validate_response_grounding(
        tool_results, "A plain response with no blockquotes.", "chat-2"
    )
    assert result["verdict"] == "skipped"
    assert "No blockquotes" in result["reason"]


@pytest.mark.asyncio
async def test_grounding_correct_all_verified():
    """All blockquotes found in source -> correct."""
    quote = "the quick brown fox jumps over the lazy dog repeatedly every day now"
    payload = {"chunks": [{"id": "c1", "original_content": quote + " extra"}]}
    tool_results = [{"name": "search_chunks", "content": json.dumps(payload)}]
    response = f"> {quote} [[cite:c1:S1]]\n"
    result = await validate_response_grounding(tool_results, response, "chat-3")
    assert result["verdict"] == "correct"
    assert "All 1 citation verified" in result["reason"]
    assert result["per_citation"]["c1"]["verdict"] == "correct"


@pytest.mark.asyncio
async def test_grounding_wrong_none_verified():
    """No blockquote found in source -> wrong."""
    payload = {"chunks": [{"id": "c1", "original_content": "totally different content"}]}
    tool_results = [{"name": "search_chunks", "content": json.dumps(payload)}]
    response = (
        "> a fabricated sentence that does not appear in any chunk at all here [[cite:c1:S1]]\n"
    )
    result = await validate_response_grounding(tool_results, response, "chat-4")
    assert result["verdict"] == "wrong"
    assert "None of 1 citation" in result["reason"]


@pytest.mark.asyncio
async def test_grounding_partial_mixed_verdicts():
    """One verified + one unverified citation -> partial."""
    good_quote = "the quick brown fox jumps over the lazy dog repeatedly every day now"
    payload = {
        "chunks": [
            {"id": "c1", "original_content": good_quote + " trailing"},
            {"id": "c2", "combined_content": "some other unrelated source text body"},
        ]
    }
    tool_results = [{"name": "search_chunks", "content": json.dumps(payload)}]
    response = (
        f"> {good_quote} [[cite:c1:S1]]\n"
        "\n"
        "> this other quoted text is nowhere to be found in the sources at all "
        "[[cite:c2:S1]]\n"
    )
    result = await validate_response_grounding(tool_results, response, "chat-5")
    assert result["verdict"] == "partial"
    assert "1 of 2 citations verified" in result["reason"]
    assert result["per_citation"]["c1"]["verdict"] == "correct"
    assert result["per_citation"]["c2"]["verdict"] == "wrong"


@pytest.mark.asyncio
async def test_grounding_exception_returns_error(monkeypatch):
    """An unexpected exception inside the function is caught -> error verdict."""

    def _boom(_tool_results):
        raise RuntimeError("boom")

    monkeypatch.setattr("chaoscypher_core.streaming.chat.validation._extract_search_chunks", _boom)
    result = await validate_response_grounding(
        [{"name": "search_chunks", "content": "{}"}], "x", "chat-6"
    )
    assert result["verdict"] == "error"
    assert result["reason"] == "Validation failed"
