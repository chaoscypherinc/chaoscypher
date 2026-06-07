# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for similarity_matcher pure functions."""

import pytest

from chaoscypher_core.services.sources.engine.deduplication.similarity_matcher import (
    _is_word_boundary_match,
    are_types_compatible,
    calculate_entity_similarity,
    extract_significant_words,
    normalize_compatibility_map,
    normalize_name_key,
    should_merge_names,
)


# ============================================================================
# are_types_compatible
# ============================================================================


class TestAreTypesCompatible:
    """Tests for type compatibility checking."""

    def test_identical_types(self) -> None:
        assert are_types_compatible("Person", "Person") is True

    def test_case_insensitive(self) -> None:
        assert are_types_compatible("person", "PERSON") is True

    def test_generic_type_always_compatible(self) -> None:
        assert are_types_compatible("unknown", "Person") is True
        assert are_types_compatible("Person", "entity") is True
        assert are_types_compatible("thing", "Organization") is True
        assert are_types_compatible("", "Person") is True

    def test_incompatible_types(self) -> None:
        assert are_types_compatible("Person", "Organization") is False

    def test_compatible_via_map(self) -> None:
        compat = {"People": ["Person", "Character", "Individual"]}
        assert are_types_compatible("Person", "Character", compat) is True

    def test_not_in_same_group(self) -> None:
        compat = {"People": ["Person", "Character"]}
        assert are_types_compatible("Person", "Organization", compat) is False

    def test_normalized_map_takes_precedence(self) -> None:
        normalized = normalize_compatibility_map({"People": ["Person", "Character"]})
        assert are_types_compatible("person", "character", _normalized_map=normalized) is True


# ============================================================================
# normalize_name_key
# ============================================================================


class TestNormalizeNameKey:
    """Tests for diacritic stripping and normalization."""

    def test_lowercase(self) -> None:
        assert normalize_name_key("Anna Pavlovna") == "anna pavlovna"

    def test_strips_diacritics(self) -> None:
        assert normalize_name_key("François") == "francois"
        assert normalize_name_key("naïve") == "naive"
        assert normalize_name_key("José García") == "jose garcia"

    def test_strips_whitespace(self) -> None:
        assert normalize_name_key("  Alice  ") == "alice"

    def test_empty_string(self) -> None:
        assert normalize_name_key("") == ""


# ============================================================================
# calculate_entity_similarity
# ============================================================================


class TestCalculateEntitySimilarity:
    """Tests for similarity scoring with bonuses."""

    def test_base_similarity_only(self) -> None:
        a = {"name": "Alice"}
        b = {"name": "Bob"}
        assert calculate_entity_similarity(a, b, 0.5) == 0.5

    def test_exact_name_bonus(self) -> None:
        a = {"name": "Alice"}
        b = {"name": "Alice"}
        result = calculate_entity_similarity(a, b, 0.5)
        assert result == pytest.approx(0.7)  # 0.5 + 0.20

    def test_name_in_aliases_bonus(self) -> None:
        a = {"name": "Alice", "aliases": []}
        b = {"name": "Bob", "aliases": ["Alice"]}
        result = calculate_entity_similarity(a, b, 0.5)
        assert result == pytest.approx(0.65)  # 0.5 + 0.15

    def test_alias_overlap_bonus(self) -> None:
        a = {"name": "A", "aliases": ["X"]}
        b = {"name": "B", "aliases": ["X"]}
        result = calculate_entity_similarity(a, b, 0.5)
        assert result == pytest.approx(0.6)  # 0.5 + 0.10

    def test_capped_at_one(self) -> None:
        a = {"name": "Alice", "aliases": ["Bob"]}
        b = {"name": "Alice", "aliases": ["Alice"]}
        result = calculate_entity_similarity(a, b, 0.9)
        assert result <= 1.0

    def test_diacritics_handled(self) -> None:
        a = {"name": "François"}
        b = {"name": "Francois"}
        result = calculate_entity_similarity(a, b, 0.5)
        assert result == pytest.approx(0.7)  # Name match after normalization


# ============================================================================
# extract_significant_words
# ============================================================================


class TestExtractSignificantWords:
    """Tests for significant word extraction."""

    def test_extracts_words(self) -> None:
        result = extract_significant_words("Albert Einstein", frozenset())
        assert result == {"albert", "einstein"}

    def test_filters_title_words(self) -> None:
        titles = frozenset({"dr", "mr", "mrs"})
        result = extract_significant_words("Dr. Albert Einstein", titles)
        assert "dr" not in result
        assert "albert" in result

    def test_filters_single_char_words(self) -> None:
        result = extract_significant_words("A B Charlie", frozenset())
        assert "charlie" in result
        assert "a" not in result
        assert "b" not in result

    def test_handles_separators(self) -> None:
        result = extract_significant_words("Smith-Jones/Ltd", frozenset())
        assert "smith" in result
        assert "jones" in result
        assert "ltd" in result


# ============================================================================
# _is_word_boundary_match
# ============================================================================


class TestIsWordBoundaryMatch:
    """Tests for word boundary detection."""

    def test_match_at_start(self) -> None:
        assert _is_word_boundary_match("prince", "prince andrei") is True

    def test_match_at_end(self) -> None:
        assert _is_word_boundary_match("andrei", "prince andrei") is True

    def test_no_match_inside_word(self) -> None:
        assert _is_word_boundary_match("prince", "princess anna") is False

    def test_no_match_partial(self) -> None:
        assert _is_word_boundary_match("mary", "marya") is False

    def test_exact_full_match(self) -> None:
        assert _is_word_boundary_match("alice", "alice") is True


# ============================================================================
# should_merge_names
# ============================================================================


class TestShouldMergeNames:
    """Tests for merge decision logic."""

    def test_containment_merge(self) -> None:
        merged, canonical = should_merge_names(
            0,
            "Prince Andrei",
            {"prince", "andrei"},
            1,
            "Andrei",
            {"andrei"},
        )
        assert merged is True
        assert canonical == 0  # Longer name is canonical

    def test_no_merge_for_false_containment(self) -> None:
        merged, _ = should_merge_names(
            0,
            "Mary",
            {"mary"},
            1,
            "Marya",
            {"marya"},
        )
        assert merged is False

    def test_word_set_merge(self) -> None:
        merged, canonical = should_merge_names(
            0,
            "Albert Einstein",
            {"albert", "einstein"},
            1,
            "Einstein",
            {"einstein"},
        )
        assert merged is True
        assert canonical == 0  # More words = canonical

    def test_no_merge_low_word_overlap(self) -> None:
        # Words don't overlap enough AND no substring containment
        merged, _ = should_merge_names(
            0,
            "Alpha Beta Gamma Delta",
            {"alpha", "beta", "gamma", "delta"},
            1,
            "Zeta Alpha",
            {"zeta", "alpha"},
        )
        # "alpha" subset of A's words but 1/4 = 25% < 40% threshold for B→A
        # "zeta alpha" not a subset, "alpha beta gamma delta" not a subset
        # No containment either
        assert merged is False

    def test_no_merge_empty_words(self) -> None:
        merged, _ = should_merge_names(0, "A", set(), 1, "B", set())
        assert merged is False

    def test_no_merge_different_names(self) -> None:
        merged, _ = should_merge_names(
            0,
            "Alice",
            {"alice"},
            1,
            "Bob",
            {"bob"},
        )
        assert merged is False
