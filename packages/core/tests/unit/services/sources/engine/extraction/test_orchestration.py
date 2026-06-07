# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for extraction orchestration filtering and group building functions."""

from __future__ import annotations

import logging
import re
from typing import Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.sources.engine.extraction.content_categories import (
    CategoryMatcher,
)
from chaoscypher_core.services.sources.engine.extraction.orchestration import (
    aggregate_chunk_results,
    build_extraction_groups,
    detect_extraction_domain,
    filter_and_strip_chunks,
    resolve_content_exclusions,
    strip_chunk_content,
)
from chaoscypher_core.services.sources.engine.extraction.safe_user_regex import (
    compile_safe,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_matcher(
    name: str = "test",
    mode: str = "count",
    pattern: str = r"copyright",
    threshold: float = 2,
) -> CategoryMatcher:
    """Create a CategoryMatcher with sensible defaults for tests."""
    return CategoryMatcher(
        name=name,
        description=f"Test matcher: {name}",
        mode=mode,
        pattern=re.compile(pattern, re.IGNORECASE | re.MULTILINE),
        threshold=threshold,
    )


def _make_chunk(
    chunk_id: str = "c1",
    chunk_index: int = 0,
    content: str = "Some meaningful content here.",
) -> dict[str, Any]:
    """Create a chunk dict matching the extraction pipeline format."""
    return {"id": chunk_id, "chunk_index": chunk_index, "content": content}


# ---------------------------------------------------------------------------
# TestStripChunkContent
# ---------------------------------------------------------------------------


class TestStripChunkContent:
    """Tests for strip_chunk_content."""

    def test_line_ratio_strips_matching_lines(self) -> None:
        """Line-ratio matcher strips matching lines and keeps the rest."""
        matcher = _make_matcher(
            name="toc",
            mode="line_ratio",
            pattern=r"^\s*[-*]\s+\S",
            threshold=0.70,
        )
        content = "- item 1\n- item 2\n- item 3\nReal paragraph here.\n- item 4"
        cleaned, categories = strip_chunk_content(content, [matcher])
        assert "Real paragraph here." in cleaned
        assert "- item 1" not in cleaned
        assert categories == ["toc"]

    def test_count_mode_empties_content(self) -> None:
        """Count matcher sets content to empty when threshold is met."""
        matcher = _make_matcher(
            name="legal",
            mode="count",
            pattern=r"copyright|all rights reserved",
            threshold=2,
        )
        content = "Copyright 2024 Acme Corp. All rights reserved."
        cleaned, categories = strip_chunk_content(content, [matcher])
        assert cleaned == ""
        assert categories == ["legal"]

    def test_pass_through_when_no_match(self) -> None:
        """Content passes through unchanged when no matchers match."""
        matcher = _make_matcher(
            name="legal",
            mode="count",
            pattern=r"copyright",
            threshold=5,
        )
        content = "This is just normal content with no legal text."
        cleaned, categories = strip_chunk_content(content, [matcher])
        assert cleaned == content.strip()
        assert categories == []

    def test_multiple_matchers_applied_sequentially(self) -> None:
        """Multiple matchers are applied in order; first count match empties content."""
        count_matcher = _make_matcher(
            name="legal",
            mode="count",
            pattern=r"copyright",
            threshold=1,
        )
        line_matcher = _make_matcher(
            name="toc",
            mode="line_ratio",
            pattern=r"^\s*-\s+\S",
            threshold=0.70,
        )
        content = "Copyright 2024 notice here."
        cleaned, categories = strip_chunk_content(content, [count_matcher, line_matcher])
        assert cleaned == ""
        assert "legal" in categories

    def test_empty_matchers_returns_original(self) -> None:
        """Empty matchers list returns content unchanged."""
        content = "Unmodified content."
        cleaned, categories = strip_chunk_content(content, [])
        assert cleaned == content.strip()
        assert categories == []


# ---------------------------------------------------------------------------
# TestFilterAndStripChunks
# ---------------------------------------------------------------------------


class TestFilterAndStripChunks:
    """Tests for filter_and_strip_chunks."""

    def test_excludes_chunk_when_empty_after_strip(self) -> None:
        """Chunk is excluded when cleaned content is below min_content_length."""
        matcher = _make_matcher(
            name="legal",
            mode="count",
            pattern=r"copyright|all rights reserved",
            threshold=2,
        )
        chunks = [
            _make_chunk("c1", 0, "Copyright 2024. All rights reserved."),
            _make_chunk(
                "c2",
                1,
                "This is a perfectly normal chunk of text that is long enough to pass the minimum content length filter easily.",
            ),
        ]
        kept, stats = filter_and_strip_chunks(chunks, [matcher], min_content_length=10)
        assert len(kept) == 1
        assert kept[0]["id"] == "c2"
        assert stats.excluded_chunks == 1

    def test_keeps_chunks_above_min_length(self) -> None:
        """Chunks with cleaned content above min_content_length are kept."""
        matcher = _make_matcher(
            name="toc",
            mode="line_ratio",
            pattern=r"^\s*-\s+\S",
            threshold=0.50,
        )
        # 3 bullet lines + 1 long paragraph = some lines stripped but enough remains
        content = "- item 1\n- item 2\n- item 3\n" + "A" * 200
        chunks = [_make_chunk("c1", 0, content)]
        kept, stats = filter_and_strip_chunks(chunks, [matcher], min_content_length=100)
        assert len(kept) == 1
        assert kept[0]["content"] != content  # content was cleaned
        assert "toc" in stats.categories_matched

    def test_stats_tracking(self) -> None:
        """FilterStats accurately tracks totals and category matches."""
        matcher = _make_matcher(
            name="legal",
            mode="count",
            pattern=r"copyright",
            threshold=1,
        )
        chunks = [
            _make_chunk("c1", 0, "Copyright notice here."),
            _make_chunk("c2", 1, "Another copyright mention."),
            _make_chunk(
                "c3",
                2,
                "Clean content with enough characters to pass the minimum length filter threshold.",
            ),
        ]
        kept, stats = filter_and_strip_chunks(chunks, [matcher], min_content_length=10)
        assert stats.total_chunks == 3
        assert stats.excluded_chunks == 2
        assert stats.categories_matched.get("legal", 0) >= 2
        assert len(kept) == 1

    def test_no_op_with_empty_matchers(self) -> None:
        """Empty matchers list returns all chunks unchanged."""
        chunks = [
            _make_chunk("c1", 0, "First chunk."),
            _make_chunk("c2", 1, "Second chunk."),
        ]
        kept, stats = filter_and_strip_chunks(chunks, [])
        assert len(kept) == 2
        assert stats.total_chunks == 2
        assert stats.excluded_chunks == 0

    def test_regex_timeouts_zero_for_stdlib_matchers(self) -> None:
        """FilterStats.regex_timeouts stays 0 when all matchers use stdlib re.Pattern."""
        # Built-in matchers use compiled stdlib re.Pattern — no timeout mechanism.
        matcher = _make_matcher(
            name="legal",
            mode="count",
            pattern=r"copyright",
            threshold=1,
        )
        chunks = [_make_chunk("c1", 0, "Copyright notice here.")]
        _, stats = filter_and_strip_chunks(chunks, [matcher])
        assert stats.regex_timeouts == 0

    def test_regex_timeouts_counted_for_pathological_safe_user_regex(self) -> None:
        """FilterStats.regex_timeouts reflects timeouts from SafeUserRegex matchers.

        Uses a known-expensive pattern against a non-matching input so the
        timeout fires and the SafeUserRegex.timeout_count accumulates.
        """
        # (a?){N}a{N} is exponential for the regex engine at N=400.
        n = 400
        user_pattern = compile_safe(rf"(a?){{{n}}}a{{{n}}}")
        custom_matcher = CategoryMatcher(
            name="custom_0",
            description="pathological test pattern",
            mode="count",
            pattern=user_pattern,
            threshold=1,
        )
        payload = "a" * n  # forces the exponential path — no match possible
        chunks = [_make_chunk("c1", 0, payload)]
        _, stats = filter_and_strip_chunks(chunks, [custom_matcher])
        # At least one timeout must have fired (findall is called by count mode)
        assert stats.regex_timeouts >= 1

    def test_regex_timeouts_zero_when_safe_user_regex_matches_fast(self) -> None:
        """FilterStats.regex_timeouts stays 0 when SafeUserRegex completes quickly."""
        user_pattern = compile_safe(r"hello")
        custom_matcher = CategoryMatcher(
            name="custom_0",
            description="fast pattern",
            mode="count",
            pattern=user_pattern,
            threshold=1,
        )
        chunks = [_make_chunk("c1", 0, "hello world, hello again")]
        _, stats = filter_and_strip_chunks(chunks, [custom_matcher])
        assert stats.regex_timeouts == 0


# ---------------------------------------------------------------------------
# TestBuildExtractionGroups
# ---------------------------------------------------------------------------


class TestBuildExtractionGroups:
    """Tests for build_extraction_groups."""

    def test_token_budget_packing(self) -> None:
        """Multiple small chunks are packed into one group within token budget."""
        chunks = [
            _make_chunk("c1", 0, "A" * 100),  # ~25 tokens
            _make_chunk("c2", 1, "B" * 100),  # ~25 tokens
            _make_chunk("c3", 2, "C" * 100),  # ~25 tokens
        ]
        groups = build_extraction_groups(chunks, target_tokens=200, overlap=0)
        # All 3 chunks (~75 tokens total) fit in one group with target 200
        assert len(groups) == 1
        assert len(groups[0]["small_chunk_ids"]) == 3

    def test_large_chunk_gets_own_group(self) -> None:
        """A chunk exceeding target_tokens still gets its own group."""
        chunks = [
            _make_chunk("c1", 0, "A" * 4000),  # ~1000 tokens, exceeds target
            _make_chunk("c2", 1, "B" * 100),  # ~25 tokens
        ]
        groups = build_extraction_groups(chunks, target_tokens=200, overlap=0)
        assert len(groups) == 2
        assert groups[0]["small_chunk_ids"] == ["c1"]
        assert groups[1]["small_chunk_ids"] == ["c2"]

    def test_overlap_behavior(self) -> None:
        """Overlap causes last N chunks of group i to appear as first N of group i+1."""
        # Each chunk ~25 tokens (100 chars / 4), target 60 tokens -> ~2 chunks per group
        # With overlap=1, advance = max(1, 2-1) = 1, so groups overlap by 1 chunk
        chunks = [
            _make_chunk(f"c{i}", i, "X" * 100)  # ~25 tokens each
            for i in range(6)
        ]
        groups = build_extraction_groups(chunks, target_tokens=60, overlap=1)
        # Each group should hold 2 chunks (~50 tokens), advance by 1
        assert len(groups) >= 3
        # Verify overlap: last chunk of group i should be first of group i+1
        for i in range(len(groups) - 1):
            current_ids = groups[i]["small_chunk_ids"]
            next_ids = groups[i + 1]["small_chunk_ids"]
            assert current_ids[-1] == next_ids[0], (
                f"Group {i} last chunk should overlap with group {i + 1} first chunk"
            )

    def test_empty_input(self) -> None:
        """Empty chunk list returns empty groups list."""
        groups = build_extraction_groups([], target_tokens=900)
        assert groups == []

    def test_combined_content_assembly(self) -> None:
        """Combined content joins chunk contents with double newlines."""
        chunks = [
            _make_chunk("c1", 0, "First chunk text."),
            _make_chunk("c2", 1, "Second chunk text."),
        ]
        groups = build_extraction_groups(chunks, target_tokens=5000, overlap=0)
        assert len(groups) == 1
        assert "First chunk text." in groups[0]["combined_content"]
        assert "Second chunk text." in groups[0]["combined_content"]
        assert "\n\n" in groups[0]["combined_content"]

    def test_required_keys(self) -> None:
        """Each group dict has all required keys."""
        chunks = [_make_chunk("c1", 0, "Some content.")]
        groups = build_extraction_groups(chunks, target_tokens=900, overlap=0)
        assert len(groups) == 1
        group = groups[0]
        assert "id" in group
        assert "group_index" in group
        assert "small_chunk_ids" in group
        assert "combined_content" in group
        assert "char_start" in group
        assert "char_end" in group
        assert group["group_index"] == 0
        assert group["char_start"] == 0
        assert group["char_end"] == len(group["combined_content"])


# ---------------------------------------------------------------------------
# TestResolveContentExclusions
# ---------------------------------------------------------------------------


class TestResolveContentExclusions:
    """Tests for resolve_content_exclusions."""

    def test_none_domain_returns_empty(self) -> None:
        """None domain returns empty matcher list."""
        result = resolve_content_exclusions(None)
        assert result == []

    def test_domain_with_categories(self) -> None:
        """Domain with categories resolves to the correct matchers."""
        domain = MagicMock()
        domain.get_content_exclusions.return_value = {
            "categories": ["toc", "legal"],
        }
        result = resolve_content_exclusions(domain)
        assert len(result) == 2
        names = [m.name for m in result]
        assert "toc" in names
        assert "legal" in names

    def test_domain_with_custom_patterns(self) -> None:
        """Domain with custom patterns compiles them into matchers."""
        domain = MagicMock()
        domain.get_content_exclusions.return_value = {
            "custom_patterns": [
                {
                    "regex": "make install",
                    "mode": "count",
                    "threshold": 2,
                    "description": "Build instructions",
                },
            ],
        }
        result = resolve_content_exclusions(domain)
        assert len(result) == 1
        assert result[0].name == "custom_0"

    def test_combined_categories_and_custom(self) -> None:
        """Domain with both categories and custom patterns returns all matchers."""
        domain = MagicMock()
        domain.get_content_exclusions.return_value = {
            "categories": ["legal"],
            "custom_patterns": [
                {
                    "regex": "build step",
                    "mode": "count",
                    "threshold": 1,
                    "description": "Build",
                },
            ],
        }
        result = resolve_content_exclusions(domain)
        assert len(result) == 2
        names = [m.name for m in result]
        assert "legal" in names
        assert "custom_0" in names

    def test_domain_without_get_content_exclusions(self) -> None:
        """Domain without get_content_exclusions method returns empty list."""
        domain = MagicMock(spec=[])  # No methods at all
        result = resolve_content_exclusions(domain)
        assert result == []


# ---------------------------------------------------------------------------
# TestAggregateChunkResults
# ---------------------------------------------------------------------------


class TestAggregateChunkResults:
    """Tests for ``aggregate_chunk_results``, focusing on the bounds-check guard."""

    def test_remaps_chunk_local_indices_to_global(self) -> None:
        """Valid chunk-local indices are offset into the global entity list."""
        completed_chunks = [
            {
                "id": "t0",
                "raw_entities": [{"name": "A"}, {"name": "B"}],
                "raw_relationships": [{"source": 0, "target": 1, "type": "REL"}],
                "input_text": "chunk zero",
                "chunk_sentences": ["chunk zero"],
            },
            {
                "id": "t1",
                "raw_entities": [{"name": "C"}],
                "raw_relationships": [],
                "input_text": "chunk one",
                "chunk_sentences": ["chunk one"],
            },
            {
                "id": "t2",
                "raw_entities": [{"name": "D"}, {"name": "E"}],
                # chunk-local 0→D, 1→E; global 3→D, 4→E
                "raw_relationships": [{"source": 0, "target": 1, "type": "LATER"}],
                "input_text": "chunk two",
                "chunk_sentences": ["chunk two"],
            },
        ]
        result = aggregate_chunk_results(completed_chunks)
        assert len(result["entities"]) == 5
        assert result["relationships"][0] == {
            "source": 0,
            "target": 1,
            "type": "REL",
        }
        # Indices for the last chunk must be offset by 3 (2 + 1 prior entities).
        assert result["relationships"][1] == {
            "source": 3,
            "target": 4,
            "type": "LATER",
        }
        assert result["chunk_texts"] == ["chunk zero", "chunk one", "chunk two"]
        assert result["chunk_sentences"] == [
            ["chunk zero"],
            ["chunk one"],
            ["chunk two"],
        ]

    def test_out_of_bounds_relationship_index_dropped_and_logged(
        self,
        caplog: pytest.LogCaptureFixture,
        structlog_for_caplog: None,  # pytest fixture, side-effect only
    ) -> None:
        """A relationship pointing past len(entities) is dropped, not propagated.

        Defends against malformed LLM output (or a regression elsewhere in the
        pipeline) that yields a ``source``/``target`` index outside the
        combined global entity list. Such relationships must be skipped with a
        structured WARNING rather than silently producing a bad-edge target at
        commit time.
        """
        # chunk t0 has 2 entities (global 0..1); chunk t1 has 1 entity (global 2).
        # A relationship with chunk-local target=5 in t0 would remap to global 5,
        # which is beyond the global list length (3).
        completed_chunks = [
            {
                "id": "t0",
                "raw_entities": [{"name": "A"}, {"name": "B"}],
                "raw_relationships": [
                    {"source": 0, "target": 5, "type": "REL"},  # target out of bounds
                    {"source": 0, "target": 1, "type": "GOOD"},  # valid
                ],
                "input_text": "chunk zero",
                "chunk_sentences": None,
            },
            {
                "id": "t1",
                "raw_entities": [{"name": "C"}],
                "raw_relationships": [],
                "input_text": "chunk one",
                "chunk_sentences": None,
            },
        ]

        with caplog.at_level(logging.WARNING):
            result = aggregate_chunk_results(completed_chunks)

        # Bad relationship dropped; good one kept with its (unchanged) indices.
        types = [r["type"] for r in result["relationships"]]
        assert "REL" not in types
        assert types == ["GOOD"]
        assert result["relationships"][0]["source"] == 0
        assert result["relationships"][0]["target"] == 1

        # All entities still present — only the malformed relationship was filtered.
        assert len(result["entities"]) == 3

        # A WARNING was emitted with the canonical event name.  Because
        # ``structlog_for_caplog`` routes structlog through stdlib,
        # the event name appears in the rendered message — no need for a
        # broader ``str(record.__dict__)`` fallback.
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warning_records, "Expected at least one WARNING record"
        combined = " ".join(r.getMessage() for r in warning_records)
        assert "invalid_relationship_index_dropped" in combined

    def test_negative_source_index_dropped(
        self,
        caplog: pytest.LogCaptureFixture,
        structlog_for_caplog: None,  # pytest fixture, side-effect only
    ) -> None:
        """A relationship with a negative source index is dropped."""
        completed_chunks = [
            {
                "id": "t0",
                "raw_entities": [{"name": "A"}, {"name": "B"}],
                "raw_relationships": [
                    # After remap (offset 0), source stays -1 → out of bounds.
                    {"source": -1, "target": 0, "type": "BAD_SOURCE"},
                    {"source": 1, "target": 0, "type": "GOOD"},
                ],
                "input_text": "chunk zero",
                "chunk_sentences": None,
            },
        ]
        with caplog.at_level(logging.WARNING):
            result = aggregate_chunk_results(completed_chunks)

        types = [r["type"] for r in result["relationships"]]
        assert "BAD_SOURCE" not in types
        assert "GOOD" in types

    def test_boolean_source_index_dropped(
        self,
        caplog: pytest.LogCaptureFixture,
        structlog_for_caplog: None,  # pytest fixture, side-effect only
    ) -> None:
        """A relationship with a boolean source index is dropped.

        Python treats ``bool`` as a subclass of ``int`` (``True == 1``,
        ``False == 0``), so an ``isinstance(..., int)`` check alone would let
        a stray JSON boolean through as a valid index.  The guard rejects
        bools explicitly.
        """
        completed_chunks = [
            {
                "id": "t0",
                "raw_entities": [{"name": "A"}, {"name": "B"}],
                "raw_relationships": [
                    # ``True`` would coerce to 1, which is in-bounds — but the
                    # guard must still reject it as non-integer input.
                    {"source": True, "target": 0, "type": "BAD_BOOL_SRC"},
                    {"source": 0, "target": False, "type": "BAD_BOOL_TGT"},
                    {"source": 1, "target": 0, "type": "GOOD"},
                ],
                "input_text": "chunk zero",
                "chunk_sentences": None,
            },
        ]
        with caplog.at_level(logging.WARNING):
            result = aggregate_chunk_results(completed_chunks)

        types = [r["type"] for r in result["relationships"]]
        assert "BAD_BOOL_SRC" not in types
        assert "BAD_BOOL_TGT" not in types
        assert "GOOD" in types

    def test_chunk_local_oob_dropped_in_non_first_chunk(
        self,
        caplog: pytest.LogCaptureFixture,
        structlog_for_caplog: None,  # pytest fixture, side-effect only
    ) -> None:
        """A chunk-local out-of-bounds index is dropped via the pre-offset check.

        The guard validates ``source``/``target`` against the **chunk's own
        entity count** (``chunk_entity_count``) *before* applying the global
        ``entity_offset``.  That is the right level to check because the LLM
        emits chunk-local indices; the bad index is never remapped into the
        global namespace.

        This test also pins the happy-path: the surviving valid relationship
        in the same later chunk is correctly remapped
        (``source_idx + entity_offset``, ``target_idx + entity_offset``).
        """
        completed_chunks = [
            {
                "id": "t0",
                "raw_entities": [{"name": "A"}],
                "raw_relationships": [],
                "input_text": "",
                "chunk_sentences": None,
            },
            {
                "id": "t1",
                "raw_entities": [{"name": "B"}, {"name": "C"}],
                # chunk_entity_count for t1 is 2; target=99 fails the
                # pre-offset chunk-local bounds check (not 0 <= 99 < 2).
                "raw_relationships": [
                    {"source": 0, "target": 99, "type": "BAD"},
                    {"source": 0, "target": 1, "type": "GOOD"},
                ],
                "input_text": "",
                "chunk_sentences": None,
            },
        ]
        with caplog.at_level(logging.WARNING):
            result = aggregate_chunk_results(completed_chunks)

        types = [r["type"] for r in result["relationships"]]
        assert "BAD" not in types
        # The surviving valid relationship in the non-first chunk is
        # correctly remapped: chunk-local (0, 1) + entity_offset (1) → (1, 2).
        good = next(r for r in result["relationships"] if r["type"] == "GOOD")
        assert good["source"] == 0 + 1, "source must be offset by prior chunk entities"
        assert good["target"] == 1 + 1, "target must be offset by prior chunk entities"

    def test_empty_input_returns_empty_result(self) -> None:
        """An empty input list yields empty lists for every output field."""
        result = aggregate_chunk_results([])
        assert result["entities"] == []
        assert result["relationships"] == []
        assert result["chunk_texts"] == []
        assert result["chunk_sentences"] == []

    def test_dropped_relationships_count_zero_when_all_valid(self) -> None:
        """dropped_relationships_invalid_index is 0 when all relationships are valid."""
        completed_chunks = [
            {
                "id": "t0",
                "raw_entities": [{"name": "A"}, {"name": "B"}],
                "raw_relationships": [
                    {"source": 0, "target": 1, "type": "VALID"},
                ],
                "input_text": "text",
                "chunk_sentences": None,
            },
        ]
        result = aggregate_chunk_results(completed_chunks)
        assert result["dropped_relationships_invalid_index"] == 0
        assert len(result["relationships"]) == 1

    def test_dropped_relationships_count_reflects_oob_and_bool_drops(
        self,
        caplog: pytest.LogCaptureFixture,
        structlog_for_caplog: None,  # pytest fixture, side-effect only
    ) -> None:
        """dropped_relationships_invalid_index counts out-of-bounds and bool drops.

        Two relationships are malformed (one out-of-bounds source, one bool
        source) and one is valid; the counter must be 2 and exactly one
        relationship must survive in the output.
        """
        completed_chunks = [
            {
                "id": "t0",
                "raw_entities": [{"name": "A"}, {"name": "B"}],
                "raw_relationships": [
                    {"source": 0, "target": 1, "type": "GOOD"},  # valid
                    {"source": 5, "target": 0, "type": "OOB"},  # out of bounds (chunk has 2)
                    {"source": True, "target": 0, "type": "BOOL"},  # bool rejected
                ],
                "input_text": "text",
                "chunk_sentences": None,
            },
        ]
        with caplog.at_level(logging.WARNING):
            result = aggregate_chunk_results(completed_chunks)

        assert result["dropped_relationships_invalid_index"] == 2
        assert len(result["relationships"]) == 1
        assert result["relationships"][0]["type"] == "GOOD"


# ---------------------------------------------------------------------------
# TestDetectExtractionDomainRanking
# ---------------------------------------------------------------------------


def _make_domain(name: str) -> Any:
    """A domain stub exposing the guidance methods detect_extraction_domain calls."""
    dom = MagicMock()
    dom.name = name
    dom.get_entity_guidance.return_value = f"{name}-entities"
    dom.get_relationship_guidance.return_value = f"{name}-rels"
    return dom


class _FakeRegistry:
    """Minimal registry exposing the three methods detect_extraction_domain uses."""

    def __init__(
        self,
        *,
        ranking: list[tuple[Any, float]],
        best: tuple[Any, float],
        by_name: dict[str, Any] | None = None,
    ) -> None:
        self._ranking = ranking
        self._best = best
        self._by_name = by_name or {}

    def rank_domains(
        self, text: str, filename: str, metadata: dict[str, Any]
    ) -> list[tuple[Any, float]]:
        return self._ranking

    def get_best_domain(
        self, text: str, filename: str, metadata: dict[str, Any]
    ) -> tuple[Any, float]:
        return self._best

    def get_domain(self, name: str) -> Any:
        return self._by_name.get(name)


class TestDetectExtractionDomainRanking:
    """detect_extraction_domain threads ranking + low_confidence onto both paths."""

    def test_auto_path_includes_ordered_ranking(self) -> None:
        """Auto detection exposes the full ordered ranking with winner first."""
        bio = _make_domain("biographical")
        hist = _make_domain("historical")
        registry = _FakeRegistry(
            ranking=[(bio, 1.20), (hist, 1.05)],
            best=(bio, 1.20),
        )
        result = detect_extraction_domain(registry, None, "sample", "x.txt", {})
        assert result["detected_domain"] == "biographical"
        assert result["confidence"] == pytest.approx(1.20)
        assert result["ranking"] == [
            {"domain": "biographical", "score": pytest.approx(1.20)},
            {"domain": "historical", "score": pytest.approx(1.05)},
        ]
        # A confident winner that matches ranking[0] is not low confidence.
        assert result["low_confidence"] is False
        assert result["ranking"][0]["domain"] == result["detected_domain"]

    def test_auto_path_generic_fallback_is_low_confidence(self) -> None:
        """A generic fallback (winner != ranking[0]) flags low_confidence and still
        guarantees ranking[0] is defined by synthesizing from the resolved domain.
        """
        news = _make_domain("news")  # below-floor candidate
        generic = _make_domain("generic")  # the fallback get_best_domain resolved to
        registry = _FakeRegistry(
            ranking=[(news, 0.74)],  # raw candidate, below the 1.0 floor
            best=(generic, 0.10),  # get_best_domain fell back to generic@0.1
        )
        result = detect_extraction_domain(registry, None, "sample", "x.txt", {})
        assert result["detected_domain"] == "generic"
        assert result["confidence"] == pytest.approx(0.10)
        assert result["low_confidence"] is True
        # ranking is the raw candidate list (news), but the winner is generic —
        # the mismatch is exactly why it's low_confidence.
        assert result["ranking"][0]["domain"] == "news"

    def test_auto_path_empty_candidates_synthesizes_ranking_zero(self) -> None:
        """When no candidate can_handle, ranking[0] is synthesized from the winner."""
        generic = _make_domain("generic")
        registry = _FakeRegistry(ranking=[], best=(generic, 0.10))
        result = detect_extraction_domain(registry, None, "sample", "x.txt", {})
        assert result["low_confidence"] is True
        assert result["ranking"] == [
            {"domain": "generic", "score": pytest.approx(0.10)},
        ]

    def test_auto_path_minimal_fallback_no_domains(self) -> None:
        """No domains registered: get_best_domain returns _MinimalFallbackDomain@0.0;
        ranking[0] is still defined and low_confidence is True.
        """
        fallback = _make_domain("fallback")
        registry = _FakeRegistry(ranking=[], best=(fallback, 0.0))
        result = detect_extraction_domain(registry, None, "sample", "x.txt", {})
        assert result["detected_domain"] == "fallback"
        assert result["confidence"] == 0.0
        assert result["low_confidence"] is True
        assert result["ranking"] == [{"domain": "fallback", "score": 0.0}]

    def test_forced_path_includes_ranking_and_not_low_confidence(self) -> None:
        """Forced domain is confidence 1.0, never low_confidence, but still carries
        the unconditionally-computed recommendation ranking for the bypass surfaces.
        """
        forced = _make_domain("technical")
        # A different auto recommendation exists; it must still be exposed.
        bio = _make_domain("biographical")
        registry = _FakeRegistry(
            ranking=[(bio, 1.30)],
            best=(bio, 1.30),
            by_name={"technical": forced},
        )
        result = detect_extraction_domain(registry, "technical", "sample", "x.txt", {})
        assert result["detected_domain"] == "technical"
        assert result["confidence"] == 1.0
        assert result["low_confidence"] is False
        assert result["entity_guidance"] == "technical-entities"
        assert result["relationship_guidance"] == "technical-rels"
        # The auto recommendation is computed unconditionally and surfaced.
        assert result["ranking"] == [
            {"domain": "biographical", "score": pytest.approx(1.30)},
        ]

    def test_forced_unknown_domain_still_returns_ranking(self) -> None:
        """A forced name the registry doesn't know returns domain=None but the keys
        are still present (no KeyError downstream).
        """
        registry = _FakeRegistry(ranking=[], best=(_make_domain("generic"), 0.10))
        result = detect_extraction_domain(registry, "nonexistent", "sample", "x.txt", {})
        assert result["domain"] is None
        assert result["detected_domain"] == "nonexistent"
        assert result["confidence"] == 1.0
        assert result["low_confidence"] is False
        assert "ranking" in result
