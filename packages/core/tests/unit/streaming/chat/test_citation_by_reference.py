# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for citation-by-reference validation and blockquote stripping.

Covers ``validate_citation_references`` (bounds-checking sentence refs
against chunk metadata) and ``_strip_blockquotes_before_citations``
(removing redundant blockquotes that contain citation markers).
"""

import json

import pytest

from chaoscypher_core.streaming.chat import (
    _strip_blockquotes_before_citations,
    validate_citation_references,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool_results(chunks: list[dict]) -> list[dict]:
    """Wrap chunks into the tool_results format expected by validation."""
    return [{"content": json.dumps({"chunks": chunks})}]


def _make_chunk(
    chunk_id: str,
    original_content: str,
    sentence_offsets: list[dict] | None = "AUTO",
) -> dict:
    """Build a chunk dict with optional sentence_offsets.

    When *sentence_offsets* is the sentinel string ``"AUTO"``, offsets are
    derived by splitting *original_content* on ``". "`` boundaries (good
    enough for test data).  Pass ``None`` explicitly to omit offsets
    entirely (legacy-chunk scenario).
    """
    chunk: dict = {
        "chunk_id": chunk_id,
        "original_content": original_content,
        "chunk_metadata": {},
    }
    if sentence_offsets == "AUTO":
        # Simple sentence splitting for test purposes
        offsets = []
        start = 0
        for part in original_content.split(". "):
            start + len(part)
            # Account for the ". " separator except after the last part
            if not original_content[start:].startswith(part + "."):
                end_actual = start + len(part)
            else:
                end_actual = start + len(part) + 1  # include the dot
            offsets.append({"start": start, "end": end_actual})
            start = end_actual + 2 if end_actual < len(original_content) else end_actual
        chunk["chunk_metadata"]["sentence_offsets"] = offsets
    elif sentence_offsets is not None:
        chunk["chunk_metadata"]["sentence_offsets"] = sentence_offsets
    # If None, leave chunk_metadata without sentence_offsets (legacy)
    return chunk


# ---------------------------------------------------------------------------
# validate_citation_references
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.cortex
class TestValidateCitationReferences:
    """Tests for validate_citation_references."""

    @pytest.mark.asyncio
    async def test_all_valid(self):
        """Valid sentence refs (S1, S2) with a chunk that has 3 sentences."""
        tool_results = [
            {
                "content": json.dumps(
                    {
                        "chunks": [
                            {
                                "chunk_id": "abc-123",
                                "original_content": ("Sentence one. Sentence two. Sentence three."),
                                "chunk_metadata": {
                                    "sentence_offsets": [
                                        {"start": 0, "end": 13},
                                        {"start": 14, "end": 28},
                                        {"start": 29, "end": 45},
                                    ]
                                },
                            }
                        ]
                    }
                ),
            }
        ]
        citations = {
            "abc-123": {
                "chunk_id": "abc-123",
                "sentence_refs": "S1,S2",
                "label": "test.txt",
            }
        }
        result = await validate_citation_references(tool_results, citations, "test-chat")
        assert result["verdict"] == "correct"
        assert result["per_citation"]["abc-123"]["verdict"] == "correct"

    @pytest.mark.asyncio
    async def test_out_of_bounds(self):
        """S5 ref on a chunk with only 1 sentence gives 'wrong' verdict."""
        tool_results = _make_tool_results(
            [
                _make_chunk(
                    "chunk-1",
                    "Only sentence.",
                    sentence_offsets=[{"start": 0, "end": 14}],
                )
            ]
        )
        citations = {
            "chunk-1": {
                "chunk_id": "chunk-1",
                "sentence_refs": "S5",
                "label": "doc.txt",
            }
        }
        result = await validate_citation_references(tool_results, citations, "test-chat")
        assert result["verdict"] == "wrong"
        assert "out-of-bounds" in result["per_citation"]["chunk-1"]["reason"].lower()

    @pytest.mark.asyncio
    async def test_chunk_not_found(self):
        """Citation references a chunk_id not present in tool_results."""
        tool_results = _make_tool_results([_make_chunk("existing-chunk", "Some text.")])
        citations = {
            "missing-chunk": {
                "chunk_id": "missing-chunk",
                "sentence_refs": "S1",
                "label": "ghost.txt",
            }
        }
        result = await validate_citation_references(tool_results, citations, "test-chat")
        assert result["verdict"] == "wrong"
        assert "not found" in result["per_citation"]["missing-chunk"]["reason"].lower()

    @pytest.mark.asyncio
    async def test_no_citations(self):
        """Empty citations dict gives 'skipped' verdict."""
        result = await validate_citation_references([], {}, "test-chat")
        assert result["verdict"] == "skipped"

    @pytest.mark.asyncio
    async def test_legacy_chunk_no_offsets(self):
        """Chunk exists but has no sentence_offsets in metadata (legacy tolerance)."""
        tool_results = _make_tool_results(
            [_make_chunk("legacy-chunk", "Old content here.", sentence_offsets=None)]
        )
        citations = {
            "legacy-chunk": {
                "chunk_id": "legacy-chunk",
                "sentence_refs": "S1",
                "label": "legacy.txt",
            }
        }
        result = await validate_citation_references(tool_results, citations, "test-chat")
        assert result["verdict"] == "correct"
        assert result["per_citation"]["legacy-chunk"]["verdict"] == "correct"

    @pytest.mark.asyncio
    async def test_partial(self):
        """Two citations: one valid, one out-of-bounds gives 'partial' verdict."""
        tool_results = _make_tool_results(
            [
                _make_chunk(
                    "good-chunk",
                    "First. Second. Third.",
                    sentence_offsets=[
                        {"start": 0, "end": 6},
                        {"start": 7, "end": 14},
                        {"start": 15, "end": 21},
                    ],
                ),
                _make_chunk(
                    "bad-chunk",
                    "Only one.",
                    sentence_offsets=[{"start": 0, "end": 9}],
                ),
            ]
        )
        citations = {
            "good-chunk": {
                "chunk_id": "good-chunk",
                "sentence_refs": "S1,S2",
                "label": "ok.txt",
            },
            "bad-chunk": {
                "chunk_id": "bad-chunk",
                "sentence_refs": "S5",
                "label": "fail.txt",
            },
        }
        result = await validate_citation_references(tool_results, citations, "test-chat")
        assert result["verdict"] == "partial"
        assert result["per_citation"]["good-chunk"]["verdict"] == "correct"
        assert result["per_citation"]["bad-chunk"]["verdict"] == "wrong"


# ---------------------------------------------------------------------------
# _strip_blockquotes_before_citations
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.cortex
class TestStripBlockquotesBeforeCitations:
    """Tests for _strip_blockquotes_before_citations."""

    def test_strips_blockquote_with_citation(self):
        """Blockquote containing a citation marker is removed; citation kept."""
        content = (
            "Here is what she says:\n\n"
            '> "The Emperor Alexander has declared..." '
            "[[cite:abc-123:S1|war.txt]]\n\n"
            "More text."
        )
        result = _strip_blockquotes_before_citations(content)
        # The blockquote line (starting with "> ") should be removed
        for line in result.split("\n"):
            assert not line.startswith("> "), f"Blockquote line should have been stripped: {line!r}"
        # The citation marker itself must survive
        assert "[[cite:abc-123:S1|war.txt]]" in result
        # Surrounding text is preserved
        assert "More text." in result

    def test_preserves_regular_blockquote(self):
        """Regular blockquote without citation is preserved as-is."""
        content = "> This is a regular blockquote\n\nSome text."
        result = _strip_blockquotes_before_citations(content)
        assert "> This is a regular blockquote" in result
