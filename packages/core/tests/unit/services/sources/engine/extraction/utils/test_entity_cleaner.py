# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for entity_cleaner: plausibility filter, relationship dedup, and within-task merge."""

from __future__ import annotations

from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
    _merge_within_task_relationships,
)
from chaoscypher_core.services.sources.engine.extraction.utils.entity_cleaner import (
    _has_mid_sentence_cap,
    _name_plausibility_score,
    deduplicate_relationships,
    enforce_relationship_limits,
    filter_implausible_entities,
    validate_relationship_type_constraints,
)


# ---------------------------------------------------------------------------
# Source text fixtures
# ---------------------------------------------------------------------------

WAR_AND_PEACE_SENTENCES = [
    "Prince Andrei sat in the armchair by the window.",
    "He saw Napoleon at the far end of the hall.",
    "Napoleon entered the study and drew his sword.",
    "She wiped her face with the handkerchief and said adieu.",
    "Pierre crossed the room with rapid steps, nose blowing loudly.",
    "The carriage rolled down the road toward the shoulder of the hill.",
    "The duty of every soldier is to serve his country.",
]


# ---------------------------------------------------------------------------
# _has_mid_sentence_cap tests
# ---------------------------------------------------------------------------


class TestHasMidSentenceCap:
    """Tests for the _has_mid_sentence_cap helper."""

    def test_proper_noun_mid_sentence(self) -> None:
        """Word capitalized mid-sentence returns 'mid'."""
        source = "The army of Napoleon marched east."
        assert _has_mid_sentence_cap("napoleon", source.lower(), source) == "mid"

    def test_common_noun_only_lowercase(self) -> None:
        """Word appearing only lowercase returns 'lower'."""
        source = "He sat in the armchair by the fire."
        assert _has_mid_sentence_cap("armchair", source.lower(), source) == "lower"

    def test_word_only_at_sentence_start(self) -> None:
        """Word capitalized only at sentence start returns 'start'."""
        source = "Armchair was old. The armchair creaked."
        assert _has_mid_sentence_cap("armchair", source.lower(), source) == "start"

    def test_word_not_in_source(self) -> None:
        """Word not found at all returns 'none'."""
        source = "The sun was shining brightly."
        assert _has_mid_sentence_cap("napoleon", source.lower(), source) == "none"

    def test_empty_inputs(self) -> None:
        """Empty word or source returns 'none'."""
        assert _has_mid_sentence_cap("", "text", "Text") == "none"
        assert _has_mid_sentence_cap("word", "", "") == "none"

    def test_whole_word_boundary(self) -> None:
        """Only matches whole words, not substrings."""
        source = "He was extraordinary in his work."
        # "extra" is a substring of "extraordinary" — should not match
        assert _has_mid_sentence_cap("extra", source.lower(), source) == "none"

    def test_multiple_occurrences_one_capitalized(self) -> None:
        """Returns 'mid' if any mid-sentence occurrence is capitalized."""
        source = "He met Pierre at the pier. The pierre was old."
        assert _has_mid_sentence_cap("pierre", source.lower(), source) == "mid"

    def test_after_exclamation(self) -> None:
        """Word after '! ' is treated as sentence start."""
        source = "Stop! Armchair is broken."
        assert _has_mid_sentence_cap("armchair", source.lower(), source) == "start"

    def test_after_question(self) -> None:
        """Word after '? ' is treated as sentence start."""
        source = "Really? Sword was missing."
        assert _has_mid_sentence_cap("sword", source.lower(), source) == "start"


# ---------------------------------------------------------------------------
# _name_plausibility_score tests
# ---------------------------------------------------------------------------


class TestNamePlausibilityScore:
    """Tests for _name_plausibility_score with source grounding."""

    def _make_source(self) -> tuple[str, str]:
        source_original = " ".join(WAR_AND_PEACE_SENTENCES)
        return source_original.lower(), source_original

    def test_proper_name_scores_high(self) -> None:
        """Real character name scores near 1.0."""
        lower, original = self._make_source()
        score = _name_plausibility_score("Prince Andrei", lower, original, True)
        assert score >= 0.85

    def test_single_proper_name_scores_high(self) -> None:
        """Single proper name (Napoleon) scores above threshold."""
        lower, original = self._make_source()
        score = _name_plausibility_score("Napoleon", lower, original, True)
        assert score >= 0.85  # Grounded: appears capitalized mid-sentence

    def test_the_armchair_filtered(self) -> None:
        """'The Armchair' as Character type scores below threshold."""
        lower, original = self._make_source()
        score = _name_plausibility_score("The Armchair", lower, original, True)
        assert score < 0.40

    def test_the_sword_filtered(self) -> None:
        """'The Sword' as Character type scores below threshold."""
        lower, original = self._make_source()
        score = _name_plausibility_score("The Sword", lower, original, True)
        assert score < 0.40

    def test_the_face_filtered(self) -> None:
        """'The Face' as Character type scores below threshold."""
        lower, original = self._make_source()
        score = _name_plausibility_score("The Face", lower, original, True)
        assert score < 0.40

    def test_the_nose_blowing_filtered(self) -> None:
        """'The Nose Blowing' as Character type scores below threshold."""
        lower, original = self._make_source()
        score = _name_plausibility_score("The Nose Blowing", lower, original, True)
        assert score < 0.40

    def test_the_rapid_steps_filtered(self) -> None:
        """'The Rapid Steps' as Character type scores below threshold."""
        lower, original = self._make_source()
        score = _name_plausibility_score("The Rapid Steps", lower, original, True)
        assert score < 0.40

    def test_non_named_type_no_grounding_penalty(self) -> None:
        """Non-named types skip the grounding signal (score stays higher)."""
        lower, original = self._make_source()
        # "The Sword" as a non-named type (Theme/Symbol) should score higher
        # because grounding signal defaults to 1.0
        score_named = _name_plausibility_score("The Sword", lower, original, True)
        score_other = _name_plausibility_score("The Sword", lower, original, False)
        assert score_other > score_named

    def test_word_not_in_source_no_penalty(self) -> None:
        """Entity whose words aren't in source at all gets no grounding penalty."""
        lower, original = self._make_source()
        # "Gatsby" doesn't appear in War and Peace text
        score = _name_plausibility_score("The Great Gatsby", lower, original, True)
        # Should get grounded=1.0 (benefit of the doubt)
        assert score >= 0.50

    def test_empty_name(self) -> None:
        """Empty name returns 0.0."""
        assert _name_plausibility_score("", "text", "Text", True) == 0.0


# ---------------------------------------------------------------------------
# filter_implausible_entities integration tests
# ---------------------------------------------------------------------------


class TestFilterImplausibleEntities:
    """Integration tests for the full filter pipeline."""

    def _run_filter(
        self,
        entities: list[dict],
        named_types: set[str] | None = None,
    ) -> tuple[list[dict], dict]:
        return filter_implausible_entities(
            entities,
            WAR_AND_PEACE_SENTENCES,
            named_referent_types=named_types,
        )

    def test_problem_entities_filtered(self) -> None:
        """Common nouns promoted to Character type are filtered out."""
        entities = [
            {"name": "The Armchair", "type": "Character"},
            {"name": "The Sword", "type": "Character"},
            {"name": "The Face", "type": "Character"},
            {"name": "The Handkerchief", "type": "Character"},
        ]
        filtered, mapping = self._run_filter(entities, {"Character"})
        assert len(filtered) == 0
        assert all(v is None for v in mapping.values())

    def test_legitimate_entities_pass(self) -> None:
        """Real named entities pass the filter."""
        entities = [
            {"name": "Prince Andrei", "type": "Character"},
            {"name": "Napoleon", "type": "Character"},
            {"name": "Pierre", "type": "Character"},
        ]
        filtered, _mapping = self._run_filter(entities, {"Character"})
        assert len(filtered) == 3
        names = {e["name"] for e in filtered}
        assert names == {"Prince Andrei", "Napoleon", "Pierre"}

    def test_mixed_entities(self) -> None:
        """Filter keeps real entities and removes hallucinated ones."""
        entities = [
            {"name": "Napoleon", "type": "Character"},
            {"name": "The Armchair", "type": "Character"},
            {"name": "Prince Andrei", "type": "Character"},
            {"name": "The Sword", "type": "Character"},
        ]
        filtered, _mapping = self._run_filter(entities, {"Character"})
        names = {e["name"] for e in filtered}
        assert "Napoleon" in names
        assert "Prince Andrei" in names
        assert "The Armchair" not in names
        assert "The Sword" not in names

    def test_non_named_types_unaffected(self) -> None:
        """Theme/Symbol types use low threshold and skip grounding."""
        entities = [
            {"name": "The Sword", "type": "Symbol"},
            {"name": "The Duty", "type": "Theme"},
        ]
        # With named_types={"Character"}, Symbol and Theme are non-named
        # They only get filtered at the 0.15 threshold
        filtered, _mapping = self._run_filter(entities, {"Character"})
        assert len(filtered) == 2

    def test_empty_entities(self) -> None:
        """Empty entity list returns empty."""
        filtered, mapping = self._run_filter([])
        assert filtered == []
        assert mapping == {}

    def test_index_mapping_correct(self) -> None:
        """Index mapping correctly tracks old→new positions."""
        entities = [
            {"name": "Napoleon", "type": "Character"},
            {"name": "The Armchair", "type": "Character"},
            {"name": "Pierre", "type": "Character"},
        ]
        _filtered, mapping = self._run_filter(entities, {"Character"})
        assert mapping[0] == 0  # Napoleon → index 0
        assert mapping[1] is None  # The Armchair → removed
        assert mapping[2] == 1  # Pierre → index 1

    def test_word_only_at_sentence_start_treated_as_not_grounded(self) -> None:
        """Entity whose words only appear at sentence start gets grounding=0.0."""
        # "The Carriage" — "carriage" appears in "The carriage rolled..."
        # which is sentence start, so it's not grounded mid-sentence
        entities = [
            {"name": "The Carriage", "type": "Character"},
        ]
        filtered, _mapping = self._run_filter(entities, {"Character"})
        assert len(filtered) == 0

    def test_named_referent_types_none_applies_to_all(self) -> None:
        """When named_referent_types is None, all types get grounding check."""
        entities = [
            {"name": "The Armchair", "type": "Character"},
        ]
        filtered, _mapping = self._run_filter(entities, named_types=None)
        assert len(filtered) == 0


# ---------------------------------------------------------------------------
# deduplicate_relationships tests (chunk-aware)
# ---------------------------------------------------------------------------


class TestDeduplicateRelationships:
    """Tests for chunk-aware relationship deduplication."""

    def test_same_chunk_index_collapses(self) -> None:
        """Same (src, tgt, type, chunk_index) → highest confidence wins."""
        rels = [
            {"source": 0, "target": 1, "type": "friendship", "confidence": 0.7, "chunk_index": 0},
            {"source": 0, "target": 1, "type": "friendship", "confidence": 0.9, "chunk_index": 0},
        ]
        result = deduplicate_relationships(rels)
        assert len(result) == 1
        assert result[0]["confidence"] == 0.9

    def test_different_chunk_index_preserves_both(self) -> None:
        """Same (src, tgt, type) from different chunks → both survive."""
        rels = [
            {"source": 0, "target": 1, "type": "friendship", "confidence": 0.7, "chunk_index": 0},
            {"source": 0, "target": 1, "type": "friendship", "confidence": 0.6, "chunk_index": 1},
        ]
        result = deduplicate_relationships(rels)
        assert len(result) == 2

    def test_different_types_same_pair_preserved(self) -> None:
        """Multiple types between same pair → all preserved."""
        rels = [
            {"source": 0, "target": 1, "type": "friendship", "confidence": 0.8, "chunk_index": 0},
            {"source": 0, "target": 1, "type": "confronts", "confidence": 0.7, "chunk_index": 0},
            {"source": 0, "target": 1, "type": "admires", "confidence": 0.6, "chunk_index": 0},
        ]
        result = deduplicate_relationships(rels)
        assert len(result) == 3
        types = {r["type"] for r in result}
        assert types == {"friendship", "confronts", "admires"}

    def test_symmetric_different_chunks_preserved(self) -> None:
        """Symmetric type from different chunks → both survive."""
        rels = [
            {"source": 0, "target": 1, "type": "debates", "confidence": 0.8, "chunk_index": 0},
            {"source": 1, "target": 0, "type": "debates", "confidence": 0.7, "chunk_index": 1},
        ]
        result = deduplicate_relationships(rels, symmetric_types=frozenset({"debates"}))
        assert len(result) == 2

    def test_symmetric_same_chunk_collapses(self) -> None:
        """Symmetric type within same chunk → collapse to one."""
        rels = [
            {"source": 0, "target": 1, "type": "debates", "confidence": 0.8, "chunk_index": 0},
            {"source": 1, "target": 0, "type": "debates", "confidence": 0.7, "chunk_index": 0},
        ]
        result = deduplicate_relationships(rels, symmetric_types=frozenset({"debates"}))
        assert len(result) == 1
        assert result[0]["confidence"] == 0.8

    def test_inverse_canonicalizes_preserves_distinct(self) -> None:
        """Inverse pairs from different chunks → both survive (canonicalized)."""
        rels = [
            {"source": 0, "target": 1, "type": "parent_of", "confidence": 0.9, "chunk_index": 0},
            {"source": 1, "target": 0, "type": "child_of", "confidence": 0.8, "chunk_index": 1},
        ]
        result = deduplicate_relationships(rels, inverse_map={"parent_of": "child_of"})
        assert len(result) == 2

    def test_inverse_same_chunk_collapses(self) -> None:
        """Inverse pairs from same chunk → collapse to one."""
        rels = [
            {"source": 0, "target": 1, "type": "parent_of", "confidence": 0.9, "chunk_index": 0},
            {"source": 1, "target": 0, "type": "child_of", "confidence": 0.8, "chunk_index": 0},
        ]
        result = deduplicate_relationships(rels, inverse_map={"parent_of": "child_of"})
        assert len(result) == 1

    def test_no_chunk_index_treated_as_same(self) -> None:
        """Missing chunk_index → defaults to None, same context."""
        rels = [
            {"source": 0, "target": 1, "type": "friendship", "confidence": 0.7},
            {"source": 0, "target": 1, "type": "friendship", "confidence": 0.9},
        ]
        result = deduplicate_relationships(rels)
        assert len(result) == 1
        assert result[0]["confidence"] == 0.9

    def test_filtering_log_records_phases(self) -> None:
        """Filtering log records correct phase information."""
        from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
            FilteringLog,
        )

        log = FilteringLog()
        rels = [
            {"source": 0, "target": 1, "type": "friendship", "confidence": 0.7, "chunk_index": 0},
            {"source": 0, "target": 1, "type": "friendship", "confidence": 0.9, "chunk_index": 0},
            {"source": 0, "target": 1, "type": "debates", "confidence": 0.8, "chunk_index": 0},
            {"source": 1, "target": 0, "type": "debates", "confidence": 0.7, "chunk_index": 0},
        ]
        result = deduplicate_relationships(
            rels,
            symmetric_types=frozenset({"debates"}),
            filtering_log=log,
        )
        assert len(result) == 2
        assert log.has_removals
        log_dict = log.to_dict()
        stages = log_dict.get("stages", [])
        assert any(s["stage"] == "relationship_dedup" for s in stages)

    def test_empty_list(self) -> None:
        """Empty list returns empty."""
        result = deduplicate_relationships([])
        assert result == []


# ---------------------------------------------------------------------------
# _merge_within_task_relationships tests
# ---------------------------------------------------------------------------


class TestMergeWithinTaskRelationships:
    """Tests for within-task relationship merging."""

    def test_merges_same_triple(self) -> None:
        """Same (src, tgt, type) → combined justification, max confidence, merged refs."""
        rels = [
            {
                "source": 0,
                "target": 1,
                "type": "friendship",
                "confidence": 0.7,
                "justification": "They are friends",
                "sent_ref": "1",
                "chunk_index": 0,
            },
            {
                "source": 0,
                "target": 1,
                "type": "friendship",
                "confidence": 0.9,
                "justification": "Close companions",
                "sent_ref": "3",
                "chunk_index": 0,
            },
        ]
        result = _merge_within_task_relationships(rels)
        assert len(result) == 1
        assert result[0]["confidence"] == 0.9
        assert "They are friends" in result[0]["justification"]
        assert "Close companions" in result[0]["justification"]
        assert "1" in result[0]["sent_ref"]
        assert "3" in result[0]["sent_ref"]

    def test_different_types_not_merged(self) -> None:
        """Different types on same pair → separate entries."""
        rels = [
            {"source": 0, "target": 1, "type": "friendship", "confidence": 0.8},
            {"source": 0, "target": 1, "type": "confronts", "confidence": 0.7},
        ]
        result = _merge_within_task_relationships(rels)
        assert len(result) == 2

    def test_single_relationship_passthrough(self) -> None:
        """Single relationship passes through unchanged."""
        rels = [
            {"source": 0, "target": 1, "type": "friendship", "confidence": 0.8},
        ]
        result = _merge_within_task_relationships(rels)
        assert len(result) == 1
        assert result[0]["confidence"] == 0.8

    def test_duplicate_justification_deduplicated(self) -> None:
        """Identical justifications are not repeated."""
        rels = [
            {
                "source": 0,
                "target": 1,
                "type": "friendship",
                "confidence": 0.7,
                "justification": "They are friends",
            },
            {
                "source": 0,
                "target": 1,
                "type": "friendship",
                "confidence": 0.9,
                "justification": "They are friends",
            },
        ]
        result = _merge_within_task_relationships(rels)
        assert len(result) == 1
        assert result[0]["justification"] == "They are friends"

    def test_empty_list(self) -> None:
        """Empty list returns empty."""
        result = _merge_within_task_relationships([])
        assert result == []


class TestValidateRelationshipTypeConstraints:
    """Tests for validate_relationship_type_constraints with fuzzy matching."""

    CONSTRAINTS = {
        "spouse_of": {
            "source_types": ["Character", "Person"],
            "target_types": ["Character", "Person"],
        },
        "interacts_with": {
            "source_types": ["Character"],
            "target_types": ["Character"],
        },
        "resides_in": {
            "source_types": ["Character", "Person"],
            "target_types": ["Location", "Place"],
        },
    }

    ENTITIES = [
        {"name": "Anna", "type": "Character"},
        {"name": "Pierre", "type": "Character"},
        {"name": "Moscow", "type": "Location"},
    ]

    def test_exact_match_passes(self) -> None:
        """Relationship with exact type match passes validation."""
        rels = [{"source": 0, "target": 1, "type": "spouse_of", "confidence": 0.9}]
        filtered, stats = validate_relationship_type_constraints(
            rels, self.ENTITIES, self.CONSTRAINTS
        )
        assert len(filtered) == 1

    def test_fuzzy_substring_match(self) -> None:
        """Relationship type matched via substring containment."""
        rels = [{"source": 0, "target": 1, "type": "interacts", "confidence": 0.9}]
        filtered, stats = validate_relationship_type_constraints(
            rels, self.ENTITIES, self.CONSTRAINTS
        )
        assert len(filtered) == 1
        assert stats.get("fuzzy_matched", 0) == 1

    def test_fuzzy_word_overlap_match(self) -> None:
        """Relationship type matched via significant word overlap."""
        rels = [{"source": 0, "target": 2, "type": "resides_in_city", "confidence": 0.9}]
        filtered, stats = validate_relationship_type_constraints(
            rels, self.ENTITIES, self.CONSTRAINTS
        )
        assert len(filtered) == 1
        assert stats.get("fuzzy_matched", 0) == 1

    def test_no_match_falls_through_by_default(self) -> None:
        """Unmatched type passes through when strict=False (default)."""
        rels = [{"source": 0, "target": 1, "type": "totally_unknown", "confidence": 0.9}]
        filtered, stats = validate_relationship_type_constraints(
            rels, self.ENTITIES, self.CONSTRAINTS
        )
        assert len(filtered) == 1
        assert stats.get("fell_through", 0) == 1

    def test_no_match_dropped_when_strict(self) -> None:
        """Unmatched type dropped when strict_edge_type_constraints=True."""
        rels = [{"source": 0, "target": 1, "type": "totally_unknown", "confidence": 0.9}]
        filtered, stats = validate_relationship_type_constraints(
            rels,
            self.ENTITIES,
            self.CONSTRAINTS,
            strict_edge_type_constraints=True,
        )
        assert len(filtered) == 0

    def test_direction_corrected_when_swapped(self) -> None:
        """Backwards source/target is auto-corrected when swap matches constraints."""
        # "resides_in" requires source=Character/Person, target=Location
        # Anna (Character) -> Moscow (Location) is correct
        # Moscow (Location) -> Anna (Character) is backwards — should be swapped
        rels = [{"source": 2, "target": 0, "type": "resides_in", "confidence": 0.9}]
        filtered, stats = validate_relationship_type_constraints(
            rels, self.ENTITIES, self.CONSTRAINTS
        )
        assert len(filtered) == 1
        # Direction should be corrected: source=Anna(0), target=Moscow(2)
        assert filtered[0]["source"] == 0
        assert filtered[0]["target"] == 2

    def test_falls_through_when_neither_direction_matches(self) -> None:
        """Falls through when neither original nor swapped direction matches."""
        # "resides_in" requires source=Character/Person, target=Location
        # Anna (Character) -> Pierre (Character) — neither is Location
        # Swapped: same types — still doesn't match target=Location
        rels = [{"source": 0, "target": 1, "type": "resides_in", "confidence": 0.9}]
        filtered, stats = validate_relationship_type_constraints(
            rels, self.ENTITIES, self.CONSTRAINTS
        )
        assert len(filtered) == 1
        assert stats.get("fell_through", 0) >= 1

    def test_source_target_mismatch_dropped_when_strict(self) -> None:
        """Source/target type mismatch dropped when strict=True."""
        # Character -> Character for resides_in — neither direction works
        rels = [{"source": 0, "target": 1, "type": "resides_in", "confidence": 0.9}]
        filtered, stats = validate_relationship_type_constraints(
            rels,
            self.ENTITIES,
            self.CONSTRAINTS,
            strict_edge_type_constraints=True,
        )
        assert len(filtered) == 0

    def test_correct_direction_not_swapped(self) -> None:
        """Already-correct direction is not modified."""
        # Anna (Character) resides_in Moscow (Location) — already correct
        rels = [{"source": 0, "target": 2, "type": "resides_in", "confidence": 0.9}]
        filtered, stats = validate_relationship_type_constraints(
            rels, self.ENTITIES, self.CONSTRAINTS
        )
        assert len(filtered) == 1
        assert filtered[0]["source"] == 0
        assert filtered[0]["target"] == 2

    def test_direction_correction_emits_filtering_log_stage(self) -> None:
        """Direction swap emits a 'relationship_direction_corrected' stage in the filtering_log."""
        from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
            FilteringLog,
        )

        # Moscow (Location) -> Anna (Character) for resides_in — backwards
        rels = [{"source": 2, "target": 0, "type": "resides_in", "confidence": 0.9}]
        log = FilteringLog()
        filtered, stats = validate_relationship_type_constraints(
            rels, self.ENTITIES, self.CONSTRAINTS, log
        )
        assert len(filtered) == 1
        assert stats["direction_corrected"] == 1

        log_dict = log.to_dict()
        stages = {s["stage"]: s for s in log_dict["stages"]}
        assert "relationship_direction_corrected" in stages, (
            "Expected 'relationship_direction_corrected' stage in filtering_log"
        )
        assert stages["relationship_direction_corrected"]["removed_count"] == 1

    def test_no_direction_correction_no_stage(self) -> None:
        """Already-correct relationship does not emit direction_corrected stage."""
        from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
            FilteringLog,
        )

        rels = [{"source": 0, "target": 2, "type": "resides_in", "confidence": 0.9}]
        log = FilteringLog()
        _, stats = validate_relationship_type_constraints(
            rels, self.ENTITIES, self.CONSTRAINTS, log
        )
        assert stats["direction_corrected"] == 0
        log_dict = log.to_dict()
        stage_names = {s["stage"] for s in log_dict["stages"]}
        assert "relationship_direction_corrected" not in stage_names

    def test_type_unmatched_strict_emits_type_constraint_stage(self) -> None:
        """In strict mode, unmatched type is recorded in 'relationship_type_constraint' stage,
        separate from index-validation drops.
        """
        from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
            FilteringLog,
        )

        rels = [{"source": 0, "target": 1, "type": "totally_unknown", "confidence": 0.9}]
        log = FilteringLog()
        filtered, stats = validate_relationship_type_constraints(
            rels,
            self.ENTITIES,
            self.CONSTRAINTS,
            log,
            strict_edge_type_constraints=True,
        )
        assert len(filtered) == 0

        log_dict = log.to_dict()
        stages = {s["stage"]: s for s in log_dict["stages"]}
        assert "relationship_type_constraint" in stages, (
            "Expected 'relationship_type_constraint' stage in filtering_log"
        )
        assert stages["relationship_type_constraint"]["removed_count"] == 1
        # Direction-corrected stage must NOT appear for a straight drop
        assert "relationship_direction_corrected" not in stages

    def test_type_unmatched_not_counted_in_direction_stage(self) -> None:
        """Type-unmatched drop and direction correction are counted in distinct stages."""
        from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
            FilteringLog,
        )

        # rel 0: backwards — will be direction-corrected
        # rel 1: totally_unknown type — will be dropped (strict mode)
        rels = [
            {"source": 2, "target": 0, "type": "resides_in", "confidence": 0.9},
            {"source": 0, "target": 1, "type": "totally_unknown", "confidence": 0.5},
        ]
        log = FilteringLog()
        filtered, stats = validate_relationship_type_constraints(
            rels,
            self.ENTITIES,
            self.CONSTRAINTS,
            log,
            strict_edge_type_constraints=True,
        )
        assert len(filtered) == 1  # only the corrected one survives
        assert stats["direction_corrected"] == 1

        log_dict = log.to_dict()
        stages = {s["stage"]: s for s in log_dict["stages"]}
        # Type-constraint stage records the drop
        assert stages["relationship_type_constraint"]["removed_count"] == 1
        # Direction stage records the correction (not a drop)
        assert stages["relationship_direction_corrected"]["removed_count"] == 1

    # -----------------------------------------------------------------------
    # _fuzzy_type_match audit (2026-05-20): rescue-rate stage emission.
    #
    # When tier 2 / 3 of ``_fuzzy_type_match`` rescues an edge-type the LLM
    # got wrong (e.g. emitted ``interacts`` for the ``interacts_with``
    # template), the validate function increments its local ``fuzzy_matched``
    # stat AND emits a ``relationship_type_fuzzy_matched`` stage on the
    # filtering_log. The finalizer then reads that stage's ``removed_count``
    # (a carry field — the stage name disambiguates) and promotes the count
    # into the ``RELATIONSHIPS_TYPE_FUZZY_MATCHED`` QualityCounter. Without
    # this stage emission the per-source rescue count is lost — the bug that
    # hid the 04fb8220 incident (39/40 relationships silently dropped while
    # quality_grade reported "Excellent (73.83)").
    #
    # The fell-through companion stage covers the balanced-mode "let an
    # unrecognized type pass without constraint check" path. Together with
    # ``relationship_type_constraint`` (strict-mode drops) they describe
    # every outcome of the cross-chunk type-constraint check.
    # -----------------------------------------------------------------------

    def test_fuzzy_matched_emits_filtering_log_stage(self) -> None:
        """Tier-2 fuzzy rescue emits a 'relationship_type_fuzzy_matched' stage.

        The stage is what extraction_finalizer reads to promote the rescue
        count into the per-source RELATIONSHIPS_TYPE_FUZZY_MATCHED counter.
        Without it the QualityCounter wiring is invisible end-to-end.
        """
        from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
            FilteringLog,
        )

        # "interacts" is a substring of "interacts_with" — tier-2 rescue.
        rels = [{"source": 0, "target": 1, "type": "interacts", "confidence": 0.9}]
        log = FilteringLog()
        filtered, stats = validate_relationship_type_constraints(
            rels, self.ENTITIES, self.CONSTRAINTS, log
        )
        # Rescued relationship survives.
        assert len(filtered) == 1
        assert stats["fuzzy_matched"] == 1

        log_dict = log.to_dict()
        stages = {s["stage"]: s for s in log_dict["stages"]}
        assert "relationship_type_fuzzy_matched" in stages, (
            "Expected 'relationship_type_fuzzy_matched' stage in filtering_log — "
            "extraction_finalizer reads this to increment "
            "RELATIONSHIPS_TYPE_FUZZY_MATCHED."
        )
        # ``removed_count`` is a carry field — the stage name disambiguates.
        # Same pattern as ``relationship_direction_corrected``.
        assert stages["relationship_type_fuzzy_matched"]["removed_count"] == 1

    def test_no_fuzzy_match_no_stage(self) -> None:
        """Exact-match relationships do not emit a fuzzy_matched stage."""
        from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
            FilteringLog,
        )

        # Exact match — no fuzzy rescue path.
        rels = [{"source": 0, "target": 1, "type": "spouse_of", "confidence": 0.9}]
        log = FilteringLog()
        _, stats = validate_relationship_type_constraints(
            rels, self.ENTITIES, self.CONSTRAINTS, log
        )
        assert stats["fuzzy_matched"] == 0
        log_dict = log.to_dict()
        stage_names = {s["stage"] for s in log_dict["stages"]}
        assert "relationship_type_fuzzy_matched" not in stage_names

    def test_fuzzy_match_count_aggregates_across_relationships(self) -> None:
        """Multiple fuzzy rescues are summed into a single stage's removed_count."""
        from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
            FilteringLog,
        )

        # Two tier-2 rescues + one exact match. Stage carries count == 2.
        rels = [
            {"source": 0, "target": 1, "type": "interacts", "confidence": 0.9},
            {"source": 0, "target": 2, "type": "resides_in_city", "confidence": 0.9},
            {"source": 0, "target": 1, "type": "spouse_of", "confidence": 0.9},
        ]
        log = FilteringLog()
        filtered, stats = validate_relationship_type_constraints(
            rels, self.ENTITIES, self.CONSTRAINTS, log
        )
        assert len(filtered) == 3
        assert stats["fuzzy_matched"] == 2

        log_dict = log.to_dict()
        stages = {s["stage"]: s for s in log_dict["stages"]}
        assert stages["relationship_type_fuzzy_matched"]["removed_count"] == 2

    def test_fell_through_emits_filtering_log_stage(self) -> None:
        """Balanced-mode fall-through emits a 'relationship_type_fell_through' stage.

        Mirror of the fuzzy_matched stage — the finalizer reads it to
        increment RELATIONSHIPS_TYPE_FELL_THROUGH, the third counter
        completing the cross-chunk type-constraint observability triad
        (dropped / fuzzy-rescued / fell-through).
        """
        from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
            FilteringLog,
        )

        # No fuzzy match possible — pure fall-through.
        rels = [{"source": 0, "target": 1, "type": "totally_unknown", "confidence": 0.9}]
        log = FilteringLog()
        filtered, stats = validate_relationship_type_constraints(
            rels, self.ENTITIES, self.CONSTRAINTS, log
        )
        assert len(filtered) == 1
        assert stats["fell_through"] == 1

        log_dict = log.to_dict()
        stages = {s["stage"]: s for s in log_dict["stages"]}
        assert "relationship_type_fell_through" in stages, (
            "Expected 'relationship_type_fell_through' stage in filtering_log — "
            "extraction_finalizer reads this to increment "
            "RELATIONSHIPS_TYPE_FELL_THROUGH."
        )
        assert stages["relationship_type_fell_through"]["removed_count"] == 1

    # -----------------------------------------------------------------------
    # Phase 4 (2026-05-08): enable_direction_correction toggle tests
    # -----------------------------------------------------------------------

    def test_direction_correction_swap_when_toggle_on(self) -> None:
        """Default: enable_direction_correction=True swaps and counts."""
        # Moscow (Location) -> Anna (Character) for resides_in — backwards
        rels = [{"source": 2, "target": 0, "type": "resides_in", "confidence": 0.9}]
        filtered, stats = validate_relationship_type_constraints(
            rels,
            self.ENTITIES,
            self.CONSTRAINTS,
            enable_direction_correction=True,
        )
        # Relationship is kept with swapped direction
        assert len(filtered) == 1
        assert filtered[0]["source"] == 0  # Anna
        assert filtered[0]["target"] == 2  # Moscow
        # Counter increments: measures wrong-direction emission rate
        assert stats["direction_corrected"] == 1

    def test_direction_correction_drop_when_toggle_off(self) -> None:
        """enable_direction_correction=False drops misdirected relationships.

        Counter still increments to measure wrong-direction LLM emission rate
        independent of how we handle it.
        """
        # Moscow (Location) -> Anna (Character) for resides_in — backwards
        rels = [{"source": 2, "target": 0, "type": "resides_in", "confidence": 0.9}]
        filtered, stats = validate_relationship_type_constraints(
            rels,
            self.ENTITIES,
            self.CONSTRAINTS,
            enable_direction_correction=False,
        )
        # Relationship is ABSENT — drop instead of swap
        assert len(filtered) == 0
        # Counter still increments (measures wrong-direction emission rate)
        assert stats["direction_corrected"] == 1

    def test_direction_correction_toggle_off_default_is_true(self) -> None:
        """Omitting enable_direction_correction defaults to True (swap behavior)."""
        rels = [{"source": 2, "target": 0, "type": "resides_in", "confidence": 0.9}]
        filtered, stats = validate_relationship_type_constraints(
            rels, self.ENTITIES, self.CONSTRAINTS
        )
        assert len(filtered) == 1
        assert filtered[0]["source"] == 0
        assert filtered[0]["target"] == 2

    def test_direction_correction_toggle_off_correct_relationship_unaffected(self) -> None:
        """Correctly-directed relationships are unaffected by the toggle."""
        # Anna (Character) resides_in Moscow (Location) — already correct
        rels = [{"source": 0, "target": 2, "type": "resides_in", "confidence": 0.9}]
        filtered, stats = validate_relationship_type_constraints(
            rels,
            self.ENTITIES,
            self.CONSTRAINTS,
            enable_direction_correction=False,
        )
        # Correct direction: no swap needed, relationship is kept
        assert len(filtered) == 1
        assert filtered[0]["source"] == 0
        assert filtered[0]["target"] == 2
        assert stats["direction_corrected"] == 0

    # -----------------------------------------------------------------------
    # Phase 5 (2026-05-18): missing entity-type enforcement
    #
    # The validator previously short-circuited the source/target type check
    # to True when the entity's type was empty/missing, which silently
    # admitted every relationship whose endpoint had no type — bypassing
    # both strict-mode drops AND the fell_through counter. The validator
    # must now treat a missing type as a constraint failure (so it either
    # drops in strict mode or registers as fell_through in non-strict).
    # -----------------------------------------------------------------------

    ENTITIES_WITH_MISSING_TYPE = [
        {"name": "Anna", "type": "Character"},
        {"name": "Pierre", "type": ""},  # no type
        {"name": "Moscow", "type": "Location"},
        {"name": "Untyped"},  # type key missing entirely
    ]

    def test_empty_source_type_dropped_when_strict(self) -> None:
        """Source entity with empty `type` causes a strict-mode drop, not silent pass."""
        # Pierre (type="") -> Moscow (Location), for resides_in.
        # interacts_with would also work since Pierre has no type to match.
        rels = [{"source": 1, "target": 2, "type": "resides_in", "confidence": 0.9}]
        filtered, stats = validate_relationship_type_constraints(
            rels,
            self.ENTITIES_WITH_MISSING_TYPE,
            self.CONSTRAINTS,
            strict_edge_type_constraints=True,
        )
        assert len(filtered) == 0
        assert stats["dropped_source_mismatch"] >= 1

    def test_empty_target_type_dropped_when_strict(self) -> None:
        """Target entity with empty `type` causes a strict-mode drop, not silent pass."""
        # Anna (Character) -> Pierre (type=""), for interacts_with
        rels = [{"source": 0, "target": 1, "type": "interacts_with", "confidence": 0.9}]
        filtered, stats = validate_relationship_type_constraints(
            rels,
            self.ENTITIES_WITH_MISSING_TYPE,
            self.CONSTRAINTS,
            strict_edge_type_constraints=True,
        )
        assert len(filtered) == 0
        assert stats["dropped_target_mismatch"] >= 1

    def test_missing_type_key_dropped_when_strict(self) -> None:
        """Entity with the `type` key missing entirely is treated the same as type=''."""
        # Untyped (no `type` key) -> Moscow (Location), for resides_in
        rels = [{"source": 3, "target": 2, "type": "resides_in", "confidence": 0.9}]
        filtered, stats = validate_relationship_type_constraints(
            rels,
            self.ENTITIES_WITH_MISSING_TYPE,
            self.CONSTRAINTS,
            strict_edge_type_constraints=True,
        )
        assert len(filtered) == 0

    def test_empty_source_type_falls_through_when_not_strict(self) -> None:
        """Non-strict mode: missing type registers as fell_through (no longer silent)."""
        rels = [{"source": 1, "target": 2, "type": "resides_in", "confidence": 0.9}]
        filtered, stats = validate_relationship_type_constraints(
            rels,
            self.ENTITIES_WITH_MISSING_TYPE,
            self.CONSTRAINTS,
            # strict_edge_type_constraints defaults to False
        )
        # Relationship survives the non-strict path, but the failure mode is
        # now observable (was previously silent).
        assert len(filtered) == 1
        assert stats["fell_through"] >= 1


class TestEnforceRelationshipLimitsOrphanProtection:
    """Tests for degree cap orphan protection in enforce_relationship_limits."""

    def test_orphan_protected_when_other_endpoint_at_cap(self) -> None:
        """Entity with 0 edges connecting to entity at cap -> relationship kept."""
        # Entity 0 (hub) already has many rels, entity 5 (minor) has none
        rels = [
            {"source": 0, "target": 1, "type": "a", "confidence": 0.9},
            {"source": 0, "target": 2, "type": "b", "confidence": 0.9},
            {"source": 0, "target": 3, "type": "c", "confidence": 0.9},
            # This one would be dropped without orphan protection
            {"source": 0, "target": 5, "type": "d", "confidence": 0.5},
        ]
        # max_entity_degree=3: entity 0 hits cap after 3 rels
        result, stats = enforce_relationship_limits(
            rels,
            entity_count=6,
            max_entity_degree=3,
            max_same_source_type=50,
        )
        # Entity 5 has 0 edges, so the 4th rel should be kept (orphan protection)
        entity_5_rels = [r for r in result if r.get("source") == 5 or r.get("target") == 5]
        assert len(entity_5_rels) == 1

    def test_both_endpoints_at_cap_still_dropped(self) -> None:
        """Both entities at cap -> relationship dropped normally."""
        rels = [
            {"source": 0, "target": 1, "type": "a", "confidence": 0.9},
            {"source": 0, "target": 2, "type": "b", "confidence": 0.9},
            {"source": 0, "target": 3, "type": "c", "confidence": 0.9},
            {"source": 1, "target": 2, "type": "d", "confidence": 0.9},
            {"source": 1, "target": 3, "type": "e", "confidence": 0.9},
            {"source": 1, "target": 4, "type": "f", "confidence": 0.9},
            # Both entity 0 (deg=3) and entity 1 (deg=3) at cap
            {"source": 0, "target": 1, "type": "g", "confidence": 0.5},
        ]
        result, stats = enforce_relationship_limits(
            rels,
            entity_count=5,
            max_entity_degree=3,
            max_same_source_type=50,
        )
        # Last rel should be dropped — both sides have 3+ edges
        assert stats["dropped_degree"] >= 1

    def test_entity_with_2_edges_not_protected(self) -> None:
        """Entity with 2 edges connecting to entity at cap -> dropped (only <2 protected)."""
        rels = [
            {"source": 0, "target": 1, "type": "a", "confidence": 0.9},
            {"source": 0, "target": 2, "type": "b", "confidence": 0.9},
            {"source": 0, "target": 3, "type": "c", "confidence": 0.9},
            {"source": 4, "target": 1, "type": "d", "confidence": 0.8},
            {"source": 4, "target": 2, "type": "e", "confidence": 0.8},
            # Entity 4 already has 2 edges, entity 0 at cap (3)
            {"source": 0, "target": 4, "type": "f", "confidence": 0.5},
        ]
        result, stats = enforce_relationship_limits(
            rels,
            entity_count=5,
            max_entity_degree=3,
            max_same_source_type=50,
        )
        # Entity 4 has 2 edges (>= 2), so not protected
        assert stats["dropped_degree"] >= 1


class TestEnforceRelationshipLimitsProtectOrphansToggle:
    """Tests for the protect_orphans toggle threaded into enforce_relationship_limits.

    Phase 7 audit-remediation (2026-05-09): the < 2 edges exception must now be
    gated on the protect_orphans flag so the extraction-time and commit-time sites
    agree. Closes audit finding P1 #8.
    """

    # Shared relationship set: entity 0 hits max_entity_degree=3 after the first
    # three rels; entity 5 has zero edges at that point (orphan endpoint).
    _RELS: list[dict[str, object]] = [
        {"source": 0, "target": 1, "type": "a", "confidence": 0.9},
        {"source": 0, "target": 2, "type": "b", "confidence": 0.9},
        {"source": 0, "target": 3, "type": "c", "confidence": 0.9},
        # entity 0 is now at cap (degree=3); entity 5 has 0 edges (orphan)
        {"source": 0, "target": 5, "type": "d", "confidence": 0.5},
    ]

    def test_protect_orphans_true_keeps_orphan_endpoint(self) -> None:
        """When protect_orphans=True the < 2 edges exception applies (current behaviour)."""
        result, stats = enforce_relationship_limits(
            list(self._RELS),
            entity_count=6,
            max_entity_degree=3,
            max_same_source_type=50,
            protect_orphans=True,
        )
        entity_5_rels = [r for r in result if r.get("source") == 5 or r.get("target") == 5]
        assert len(entity_5_rels) == 1, "Orphan entity 5 should be kept when protect_orphans=True"
        assert stats.get("dropped_degree", 0) == 0

    def test_protect_orphans_false_drops_orphan_endpoint(self) -> None:
        """When protect_orphans=False the < 2 exception is gated off; orphan loses its rel."""
        result, stats = enforce_relationship_limits(
            list(self._RELS),
            entity_count=6,
            max_entity_degree=3,
            max_same_source_type=50,
            protect_orphans=False,
        )
        entity_5_rels = [r for r in result if r.get("source") == 5 or r.get("target") == 5]
        assert len(entity_5_rels) == 0, (
            "Orphan entity 5 should be dropped when protect_orphans=False"
        )
        assert stats.get("dropped_degree", 0) >= 1
