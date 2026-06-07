# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for entity deduplication and normalization."""

import pytest

from chaoscypher_core.services.sources.engine.deduplication.service import EntityProcessor
from chaoscypher_core.services.sources.engine.deduplication.similarity_matcher import (
    extract_significant_words,
    normalize_name_key,
    should_merge_names,
)


# Title words used by tests that verify title-word filtering behavior
_TEST_TITLE_WORDS = frozenset(
    {
        "mr",
        "mrs",
        "ms",
        "miss",
        "dr",
        "sir",
        "dame",
        "madam",
        "the",
        "of",
        "de",
        "von",
        "van",
        "prince",
        "princess",
        "count",
        "countess",
        "duke",
        "duchess",
        "lord",
        "lady",
        "baron",
        "baroness",
        "king",
        "queen",
        "colonel",
        "captain",
        "general",
        "major",
        "lieutenant",
        "father",
        "mother",
        "brother",
        "sister",
        "uncle",
        "aunt",
        "grandfather",
        "grandmother",
    }
)


class TestNormalizeNameKey:
    """Tests for normalize_name_key() function."""

    def test_strips_diacritics(self):
        """Diacritics (accents, umlauts, cedillas) are stripped."""
        assert normalize_name_key("Anna Pávlovna Schérer") == "anna pavlovna scherer"
        assert normalize_name_key("François") == "francois"
        assert normalize_name_key("naïve") == "naive"
        assert normalize_name_key("Müller") == "muller"
        assert normalize_name_key("São Paulo") == "sao paulo"
        assert normalize_name_key("Ångström") == "angstrom"

    def test_lowercases_and_strips_whitespace(self):
        """Names are lowercased and whitespace-trimmed."""
        assert normalize_name_key("  HELLO  ") == "hello"
        assert normalize_name_key("Anna Pavlovna") == "anna pavlovna"

    def test_preserves_non_latin_scripts(self):
        """CJK, Cyrillic, Arabic, etc. pass through without mangling."""
        assert normalize_name_key("東京") == "東京"
        assert normalize_name_key("Москва") == "москва"
        assert normalize_name_key("القاهرة") == "القاهرة"

    def test_empty_and_whitespace(self):
        """Empty strings and whitespace-only strings return empty."""
        assert normalize_name_key("") == ""
        assert normalize_name_key("   ") == ""


class TestDeduplicateEntitiesWithMappingNormalization:
    """Tests that deduplicate_entities_with_mapping uses diacritics normalization."""

    def test_deduplicates_diacritics_variants(self):
        """Entities with accented vs unaccented names merge as duplicates."""
        entities = [
            {"name": "Anna Pavlovna Scherer", "type": "Person"},
            {"name": "Anna Pávlovna Schérer", "type": "Person"},
        ]
        processor = EntityProcessor()
        unique, mapping = processor.deduplicate_entities_with_mapping(entities)

        assert len(unique) == 1
        assert unique[0]["name"] == "Anna Pavlovna Scherer"
        assert mapping[0] == 0
        assert mapping[1] == 0

    def test_preserves_distinct_entities(self):
        """Entities with genuinely different names are kept separate."""
        entities = [
            {"name": "Anna Pavlovna", "type": "Person"},
            {"name": "Pierre Bezukhov", "type": "Person"},
        ]
        processor = EntityProcessor()
        unique, _mapping = processor.deduplicate_entities_with_mapping(entities)

        assert len(unique) == 2


class TestExtractSignificantWordsNormalization:
    """Tests that extract_significant_words strips diacritics before tokenizing."""

    def test_normalizes_diacritics_in_words(self):
        """Words with diacritics are normalized so they match plain variants."""
        words = extract_significant_words("Anna Pávlovna Schérer", frozenset())
        assert "pavlovna" in words
        assert "scherer" in words
        assert "anna" in words

    def test_ignores_title_words(self):
        """Title words are still excluded after normalization."""
        words = extract_significant_words("Prince Andréi Bolkonsky", _TEST_TITLE_WORDS)
        assert "prince" not in words
        assert "andrei" in words
        assert "bolkonsky" in words


class TestShouldMergeNamesWithDiacritics:
    """Tests that should_merge_names handles diacritics correctly."""

    def test_containment_with_diacritics(self):
        """Containment check works across diacritics variants."""
        _empty: frozenset[str] = frozenset()
        words_a = extract_significant_words("Anna Pavlovna", _empty)
        words_b = extract_significant_words("Anna Pávlovna Schérer", _empty)

        merge, canonical = should_merge_names(
            0, "Anna Pavlovna", words_a, 1, "Anna Pávlovna Schérer", words_b
        )

        assert merge is True
        assert canonical == 1  # Longer name is canonical

    def test_word_overlap_with_diacritics(self):
        """Word overlap check works when one variant has diacritics."""
        _empty: frozenset[str] = frozenset()
        words_a = extract_significant_words("Pavlovna Scherer", _empty)
        words_b = extract_significant_words("Pávlovna Schérer", _empty)

        merge, _canonical = should_merge_names(
            0, "Pavlovna Scherer", words_a, 1, "Pávlovna Schérer", words_b
        )

        assert merge is True

    def test_different_patronymics_same_first_last_no_merge(self):
        """Different patronymics with same first/last name should NOT merge."""
        _empty: frozenset[str] = frozenset()
        words_a = extract_significant_words("Anna Pávlovna Schérer", _empty)
        words_b = extract_significant_words("Anna Mikháylovna Schérer", _empty)

        merge, _canonical = should_merge_names(
            0, "Anna Pávlovna Schérer", words_a, 1, "Anna Mikháylovna Schérer", words_b
        )

        assert merge is False

    def test_progressive_naming_subset_merges(self):
        """Shorter name that is a word subset of longer name should merge."""
        _empty: frozenset[str] = frozenset()
        words_a = extract_significant_words("Bob Smith", _empty)
        words_b = extract_significant_words("Bob Smith Jr", _empty)

        merge, canonical = should_merge_names(0, "Bob Smith", words_a, 1, "Bob Smith Jr", words_b)

        assert merge is True
        assert canonical == 1  # Longer name is canonical

    def test_single_word_subset_merges(self):
        """Two-word name that is a subset of three-word name should merge."""
        _empty: frozenset[str] = frozenset()
        words_a = extract_significant_words("Anna Mikháylovna", _empty)
        words_b = extract_significant_words("Anna Mikháylovna Drubetskaya", _empty)

        merge, canonical = should_merge_names(
            0, "Anna Mikháylovna", words_a, 1, "Anna Mikháylovna Drubetskaya", words_b
        )

        assert merge is True
        assert canonical == 1


class TestCalculateEntitySimilarityNormalization:
    """Tests that alias matching bonus works across diacritics variants."""

    def test_alias_bonus_with_diacritics(self):
        """Name-in-alias bonus fires when alias has diacritics."""
        entity_a = {
            "name": "Anna Pavlovna Scherer",
            "aliases": [],
        }
        entity_b = {
            "name": "Annette Scherer",
            "aliases": ["Anna Pávlovna Schérer"],
        }

        similarity = EntityProcessor.calculate_entity_similarity(entity_a, entity_b, 0.80)

        # Should get the +0.15 bonus because entity_a's name matches entity_b's alias
        assert similarity == pytest.approx(0.95, abs=0.01)

    def test_alias_overlap_with_diacritics(self):
        """Alias overlap bonus fires across diacritics variants."""
        entity_a = {
            "name": "Anna",
            "aliases": ["Anna Pavlovna"],
        }
        entity_b = {
            "name": "Annette",
            "aliases": ["Anna Pávlovna"],
        }

        similarity = EntityProcessor.calculate_entity_similarity(entity_a, entity_b, 0.70)

        # Should get +0.10 bonus from alias overlap
        assert similarity == pytest.approx(0.80, abs=0.01)


class TestMergeEntitiesDescriptors:
    """Tests that merge_entities merges descriptors alongside aliases."""

    def test_merges_descriptors_from_both(self):
        """Descriptors from both entities are unioned."""
        kept = {
            "name": "Anna",
            "aliases": [],
            "descriptors": ["the hostess"],
        }
        duplicate = {
            "name": "Annette",
            "aliases": [],
            "descriptors": ["Boris's mother"],
        }

        processor = EntityProcessor()
        merged = processor.merge_entities(kept, duplicate)

        assert set(merged["descriptors"]) == {"the hostess", "Boris's mother"}

    def test_merges_when_one_has_no_descriptors(self):
        """Merge works when only one entity has descriptors."""
        kept = {"name": "Anna", "aliases": []}
        duplicate = {
            "name": "Annette",
            "aliases": [],
            "descriptors": ["the hostess"],
        }

        processor = EntityProcessor()
        merged = processor.merge_entities(kept, duplicate)

        assert merged["descriptors"] == ["the hostess"]

    def test_no_descriptors_key_when_both_empty(self):
        """No descriptors key when neither entity has descriptors."""
        kept = {"name": "Anna", "aliases": []}
        duplicate = {"name": "Annette", "aliases": []}

        processor = EntityProcessor()
        merged = processor.merge_entities(kept, duplicate)

        assert "descriptors" not in merged or not merged.get("descriptors")


class TestMergeEntitiesTitleWordFilter:
    """Tests that merge_entities skips title-word aliases."""

    def test_single_title_word_not_added_as_alias(self):
        """Single-word title name is NOT added as alias during merge."""
        kept = {"name": "Prince Vasíli Kurágin", "type": "Character", "aliases": []}
        duplicate = {"name": "Prince", "type": "Character", "aliases": []}

        processor = EntityProcessor(title_words=_TEST_TITLE_WORDS)
        merged = processor.merge_entities(kept, duplicate)

        assert "Prince" not in merged["aliases"]

    def test_multi_word_name_with_title_still_added(self):
        """Multi-word name containing a title IS added as alias."""
        kept = {"name": "Count Nikolai Rostov", "type": "Character", "aliases": []}
        duplicate = {"name": "Count Rostov", "type": "Character", "aliases": []}

        processor = EntityProcessor(title_words=_TEST_TITLE_WORDS)
        merged = processor.merge_entities(kept, duplicate)

        assert "Count Rostov" in merged["aliases"]

    def test_non_title_single_word_still_added(self):
        """Single-word name that's not a title IS added as alias."""
        kept = {"name": "Pierre Bezukhov", "type": "Character", "aliases": []}
        duplicate = {"name": "Bezukhov", "type": "Character", "aliases": []}

        processor = EntityProcessor()
        merged = processor.merge_entities(kept, duplicate)

        assert "Bezukhov" in merged["aliases"]

    def test_uncle_title_word_not_added(self):
        """Family relation title word 'Uncle' is NOT added as alias."""
        kept = {"name": "Uncle Nikolai", "type": "Character", "aliases": []}
        duplicate = {"name": "Uncle", "type": "Character", "aliases": []}

        processor = EntityProcessor(title_words=_TEST_TITLE_WORDS)
        merged = processor.merge_entities(kept, duplicate)

        assert "Uncle" not in merged["aliases"]


class TestDeduplicateEntitiesAliasMatching:
    """Tests that exact dedup checks aliases for cross-matching."""

    def test_entity_name_matches_other_alias(self):
        """Entity B's name matches Entity A's alias -> merged."""
        entities = [
            {"name": "Anna Pavlovna", "type": "Person", "aliases": ["Anne P."]},
            {"name": "Anne P.", "type": "Person", "aliases": []},
        ]
        processor = EntityProcessor()
        unique, mapping = processor.deduplicate_entities_with_mapping(entities)

        assert len(unique) == 1
        assert mapping[0] == 0
        assert mapping[1] == 0
        assert "Anne P." in unique[0].get("aliases", [])

    def test_entity_alias_matches_other_name(self):
        """Entity A's alias matches Entity B's name (reverse direction) -> merged."""
        entities = [
            {"name": "Prince Andrei", "type": "Person", "aliases": []},
            {"name": "Andrew Bolkonsky", "type": "Person", "aliases": ["Prince Andrei"]},
        ]
        processor = EntityProcessor()
        unique, mapping = processor.deduplicate_entities_with_mapping(entities)

        assert len(unique) == 1
        assert mapping[1] == 0

    def test_alias_to_alias_not_merged(self):
        """Two entities sharing an alias but different names are NOT merged."""
        entities = [
            {"name": "Count Rostov", "type": "Person", "aliases": ["The Count"]},
            {"name": "Count Bezukhov", "type": "Person", "aliases": ["The Count"]},
        ]
        processor = EntityProcessor()
        unique, mapping = processor.deduplicate_entities_with_mapping(entities)

        assert len(unique) == 2

    def test_alias_collision_with_different_entity_name(self):
        """Alias key that collides with different entity name -> kept separate with type compat."""
        entities = [
            {"name": "Paris", "type": "Location", "aliases": []},
            {"name": "Helen", "type": "Person", "aliases": ["Paris"]},
        ]
        processor = EntityProcessor()
        unique, mapping = processor.deduplicate_entities_with_mapping(
            entities, require_type_compatibility=True
        )

        assert len(unique) == 2

    def test_alias_diacritics_normalized(self):
        """Aliases with diacritics are normalized before matching."""
        entities = [
            {"name": "Natasha Rostova", "type": "Person", "aliases": ["Natasha"]},
            {"name": "Natasha", "type": "Person", "aliases": []},
        ]
        processor = EntityProcessor()
        unique, mapping = processor.deduplicate_entities_with_mapping(entities)

        assert len(unique) == 1

    def test_alias_same_as_own_name_not_double_registered(self):
        """Alias that normalizes to the same key as the name is harmless."""
        entities = [
            {"name": "Pierre", "type": "Person", "aliases": ["Pierre"]},
            {"name": "Pierre Bezukhov", "type": "Person", "aliases": []},
        ]
        processor = EntityProcessor()
        unique, mapping = processor.deduplicate_entities_with_mapping(entities)

        assert len(unique) == 2

    def test_type_compatibility_respected_for_alias_merge(self):
        """Alias match still respects type compatibility when enabled."""
        entities = [
            {"name": "Mercury", "type": "Planet", "aliases": ["Hermes"]},
            {"name": "Hermes", "type": "God", "aliases": []},
        ]
        processor = EntityProcessor()
        unique, mapping = processor.deduplicate_entities_with_mapping(
            entities,
            require_type_compatibility=True,
            type_compatibility_map={"beings": ["God", "Deity"]},
        )

        assert len(unique) == 2

    def test_merged_aliases_registered_for_subsequent_entities(self):
        """After merge, newly acquired aliases are available for future matches."""
        entities = [
            {"name": "Anna Pavlovna", "type": "Person", "aliases": ["Annette"]},
            {"name": "Annette", "type": "Person", "aliases": ["Anne P."]},
            {"name": "Anne P.", "type": "Person", "aliases": []},
        ]
        processor = EntityProcessor()
        unique, mapping = processor.deduplicate_entities_with_mapping(entities)

        assert len(unique) == 1
        assert mapping[0] == 0
        assert mapping[1] == 0
        assert mapping[2] == 0


class TestResolveHierarchicalNames:
    """Tests for resolve_hierarchical_names() chain-merge correctness."""

    def test_chain_merge_keeps_every_entity_and_edge(self):
        """A node that becomes canonical and is then merged away must not orphan
        its own subordinate.

        Reproduces silent data loss: with the ordering [mid, short, long] of a
        same-type containment chain, the mid name ("Bob Smith") first becomes
        canonical for the short name ("Smith"), then is itself merged into the
        long name ("Bob Smith Jr"). The short name's subordinate group must be
        re-parented onto the final canonical; otherwise it is dropped from
        index_mapping and every relationship touching it silently vanishes.
        """
        entities = [
            {"name": "Bob Smith", "type": "Person"},
            {"name": "Smith", "type": "Person"},
            {"name": "Bob Smith Jr", "type": "Person"},
            {"name": "Acme Corp", "type": "Organization"},
        ]
        relationships = [
            {"source": 1, "target": 3, "type": "WORKS_AT"},
        ]
        processor = EntityProcessor()

        merged, remapped, index_mapping = processor.resolve_hierarchical_names(
            entities, relationships
        )

        # The three Person mentions collapse to one canonical; Acme survives.
        assert len(merged) == 2

        # Every original index must map to a real surviving entity — no silent drop.
        for old_idx in range(len(entities)):
            assert old_idx in index_mapping, f"index {old_idx} silently dropped"
            assert index_mapping[old_idx] is not None, f"index {old_idx} mapped to None"

        # The WORKS_AT edge from "Smith" must survive, remapped onto the
        # surviving canonical person and Acme Corp.
        assert len(remapped) == 1
        edge = remapped[0]
        assert edge["type"] == "WORKS_AT"
        assert edge["source"] == index_mapping[1]
        assert edge["target"] == index_mapping[3]
        assert merged[edge["target"]]["name"] == "Acme Corp"

    @pytest.mark.parametrize(
        "order",
        [
            ["Anna", "Anna Pavlovna", "Anna Pavlovna Scherer"],
            ["Anna Pavlovna Scherer", "Anna Pavlovna", "Anna"],
            ["Anna Pavlovna", "Anna", "Anna Pavlovna Scherer"],
        ],
    )
    def test_progressive_chain_collapses_to_one_regardless_of_order(self, order):
        """A same-type containment chain collapses to a single survivor and never
        drops an entity, no matter what order the mentions arrive in.
        """
        entities = [{"name": name, "type": "Person"} for name in order]
        processor = EntityProcessor()

        merged, _remapped, index_mapping = processor.resolve_hierarchical_names(entities, [])

        assert len(merged) == 1
        assert all(index_mapping[i] == 0 for i in range(len(entities)))
        # The most complete name wins as the canonical survivor.
        assert merged[0]["name"] == "Anna Pavlovna Scherer"

    def test_no_merge_candidates_preserves_all_entities_and_edges(self):
        """Unrelated entities are returned unchanged with an identity mapping and
        all relationships intact.
        """
        entities = [
            {"name": "Alice", "type": "Person"},
            {"name": "Bob", "type": "Person"},
            {"name": "Acme Corp", "type": "Organization"},
        ]
        relationships = [{"source": 0, "target": 2, "type": "WORKS_AT"}]
        processor = EntityProcessor()

        merged, remapped, index_mapping = processor.resolve_hierarchical_names(
            entities, relationships
        )

        assert len(merged) == 3
        assert index_mapping == {0: 0, 1: 1, 2: 2}
        assert len(remapped) == 1
        assert remapped[0]["source"] == 0
        assert remapped[0]["target"] == 2
