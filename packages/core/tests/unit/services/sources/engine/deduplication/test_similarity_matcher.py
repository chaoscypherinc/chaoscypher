# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for similarity_matcher pure functions."""

import pytest

from chaoscypher_core.services.sources.engine.deduplication.similarity_matcher import (
    are_types_compatible,
    calculate_entity_similarity,
    core_type_key,
    normalize_name_key,
)


# ============================================================================
# are_types_compatible
# ============================================================================


class TestAreTypesCompatible:
    """Two types are compatible when their core types are one (spec 2026-10-07 § 6)."""

    def test_identical_types(self) -> None:
        assert are_types_compatible("Person", "Person") is True

    def test_case_insensitive(self) -> None:
        assert are_types_compatible("person", "PERSON") is True

    def test_two_old_names_of_one_core_type_are_compatible(self) -> None:
        """An old snapshot's ``Character`` still dedups with a ``Person`` (the mapping table)."""
        assert are_types_compatible("Person", "Character") is True
        assert are_types_compatible("Character", "Author") is True
        assert are_types_compatible("military_unit", "Organization") is True

    def test_a_core_type_is_no_generic_placeholder(self) -> None:
        """``Concept`` and ``Object`` are core types: until 2026-10-07 they matched every type."""
        assert are_types_compatible("Concept", "Person") is False
        assert are_types_compatible("Object", "Location") is False

    def test_an_unknown_or_missing_type_is_only_itself(self) -> None:
        assert are_types_compatible("unknown", "Person") is False
        assert are_types_compatible("", "Person") is False
        assert are_types_compatible("thing", "Thing") is True
        assert are_types_compatible("", "") is True

    def test_incompatible_types(self) -> None:
        assert are_types_compatible("Person", "Organization") is False
        assert are_types_compatible("Character", "Location") is False


class TestCoreTypeKey:
    """The one reading of a written type wherever two are compared."""

    @pytest.mark.parametrize(
        ("written", "key"),
        [
            ("Person", "Person"),
            ("  person ", "Person"),
            ("Character", "Person"),
            ("Literary Work", "Work"),
            ("item", "Object"),
            ("Quux", "quux"),
            ("UNKNOWN", "unknown"),
            ("", ""),
            (None, ""),
        ],
    )
    def test_core_type_key(self, written: str | None, key: str) -> None:
        assert core_type_key(written) == key


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

    def test_a_shared_alias_is_no_bonus(self) -> None:
        """Alias-to-alias is not evidence (the exact dedup's rule): ``Kurágin`` on father and son."""
        a = {"name": "Anatole", "aliases": ["Kurágin"]}
        b = {"name": "Vasíli", "aliases": ["Kurágin"]}
        assert calculate_entity_similarity(a, b, 0.5) == pytest.approx(0.5)

    def test_a_name_in_the_others_aliases_earns_its_bonus_only_as_alias_evidence(self) -> None:
        """A bare role given as an alias names no one (``the prince``): no bonus; the caller's verdict wins."""
        a = {"name": "Prince Hippolyte", "aliases": ["the prince"]}
        b = {"name": "the prince", "aliases": []}
        assert calculate_entity_similarity(a, b, 0.5) == pytest.approx(0.5)
        assert calculate_entity_similarity(a, b, 0.5, alias_evidence=(False, True)) == (
            pytest.approx(0.65)
        )
        assert calculate_entity_similarity(a, b, 0.5, alias_evidence=(True, False)) == (
            pytest.approx(0.5)
        )

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
