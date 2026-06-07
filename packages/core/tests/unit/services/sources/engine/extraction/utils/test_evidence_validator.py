# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for evidence_validator pure functions."""

from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.quality.counters import QualityCounter
from chaoscypher_core.services.sources.engine.extraction.utils import (
    evidence_validator as ev,
)
from chaoscypher_core.services.sources.engine.extraction.utils.evidence_validator import (
    _text_contains_name,
    _text_contains_significant_word,
    _try_best_effort_parse,
    validate_entity_evidence,
    validate_relationship_evidence,
)


# ============================================================================
# _text_contains_name
# ============================================================================


class TestTextContainsName:
    """Tests for strict substring name matching."""

    def test_exact_match(self) -> None:
        assert _text_contains_name("Alice went home", "Alice") is True

    def test_case_insensitive(self) -> None:
        assert _text_contains_name("alice went home", "Alice") is True

    def test_no_match(self) -> None:
        assert _text_contains_name("Bob went home", "Alice") is False

    def test_alias_match(self) -> None:
        assert _text_contains_name("Dr. Smith arrived", "John Smith", ["Dr. Smith"]) is True

    def test_alias_no_match(self) -> None:
        assert _text_contains_name("Bob arrived", "Alice", ["Carol"]) is False

    def test_empty_name(self) -> None:
        assert _text_contains_name("some text", "") is True  # Empty string is always "in"

    def test_partial_substring(self) -> None:
        assert _text_contains_name("The princess arrived", "prince") is True  # Substring match


# ============================================================================
# _text_contains_significant_word
# ============================================================================


class TestTextContainsSignificantWord:
    """Tests for standard-mode significant word matching."""

    def test_significant_word_found(self) -> None:
        assert (
            _text_contains_significant_word("Einstein developed the theory", "Albert Einstein")
            is True
        )

    def test_short_words_ignored(self) -> None:
        # "Al" is < 4 chars, not significant
        result = _text_contains_significant_word(
            "Something else entirely", "Al Bo", min_word_length=4
        )
        # Falls back to strict match since no significant words
        assert result is False

    def test_alias_words_checked(self) -> None:
        assert (
            _text_contains_significant_word(
                "The professor lectured", "A.E.", aliases=["Professor Einstein"]
            )
            is True
        )

    def test_no_significant_match(self) -> None:
        assert _text_contains_significant_word("Unrelated content here", "Albert Einstein") is False

    def test_punctuation_stripped(self) -> None:
        assert _text_contains_significant_word("Einstein was remarkable", "Einstein,") is True


# ============================================================================
# _try_best_effort_parse
# ============================================================================


class TestTryBestEffortParse:
    """Tests for malformed sent_ref recovery."""

    def test_single_reference(self) -> None:
        assert _try_best_effort_parse("S3") == "S3"

    def test_range_from_tokens(self) -> None:
        result = _try_best_effort_parse("S3 to S5")
        assert result == "S3-S5"

    def test_semicolons(self) -> None:
        result = _try_best_effort_parse("S2;S4;S6")
        assert result == "S2-S6"

    def test_no_references(self) -> None:
        assert _try_best_effort_parse("no references here") is None

    def test_invalid_zero_reference(self) -> None:
        assert _try_best_effort_parse("S0") is None


# ============================================================================
# validate_entity_evidence
# ============================================================================


class TestValidateEntityEvidence:
    """Tests for entity evidence validation across modes."""

    SENTENCES = [
        "Albert Einstein was born in Germany.",
        "He developed the theory of relativity.",
        "Einstein won the Nobel Prize in 1921.",
    ]

    def test_strict_mode_full_name_required(self) -> None:
        entity = {"name": "Einstein", "sent_ref": "S1"}
        assert validate_entity_evidence(entity, self.SENTENCES, mode="strict") is True

    def test_strict_mode_fails_no_name_match(self) -> None:
        entity = {"name": "Bohr", "sent_ref": "S1"}
        assert validate_entity_evidence(entity, self.SENTENCES, mode="strict") is False

    def test_standard_mode_significant_word(self) -> None:
        entity = {"name": "Albert Einstein", "sent_ref": "S2"}
        # "Einstein" not in S2 but "theory" is not a name word
        # Actually "He developed the theory" — no Einstein. Let's use S3.
        entity = {"name": "Albert Einstein", "sent_ref": "S3"}
        assert validate_entity_evidence(entity, self.SENTENCES, mode="standard") is True

    def test_relaxed_mode_valid_ref_sufficient(self) -> None:
        entity = {"name": "Nobody Here", "sent_ref": "S1"}
        assert validate_entity_evidence(entity, self.SENTENCES, mode="relaxed") is True

    def test_no_sent_ref_fails_all_modes(self) -> None:
        entity = {"name": "Einstein"}
        assert validate_entity_evidence(entity, self.SENTENCES, mode="strict") is False
        assert validate_entity_evidence(entity, self.SENTENCES, mode="standard") is False
        assert validate_entity_evidence(entity, self.SENTENCES, mode="relaxed") is False

    def test_out_of_bounds_ref_fails(self) -> None:
        entity = {"name": "Einstein", "sent_ref": "S99"}
        assert validate_entity_evidence(entity, self.SENTENCES, mode="relaxed") is False

    def test_invalid_sent_ref_format(self) -> None:
        entity = {"name": "Einstein", "sent_ref": "invalid"}
        assert validate_entity_evidence(entity, self.SENTENCES, mode="relaxed") is False


class TestNarrativeMode:
    """Tests for the narrative evidence validation mode."""

    SENTENCES = [
        "Prince Andrei rode through the battlefield.",
        "He turned to her and spoke softly about the coming battle.",
        "The old count sat in his chair by the window.",
        "She danced with grace at the ball.",
    ]

    ENTITIES = [
        {"name": "Prince Andrei", "aliases": []},
        {"name": "Natasha", "aliases": []},
        {"name": "Count Rostov", "aliases": ["The Count"]},
    ]

    def test_both_names_found(self) -> None:
        """Both entity names in text -> accepted (same as standard)."""
        rel = {"source": 0, "target": 2, "type": "spoke_to", "sent_ref": "S1"}
        sentences = ["Prince Andrei spoke to Count Rostov."]
        assert (
            validate_relationship_evidence(rel, self.ENTITIES, sentences, mode="narrative") is True
        )

    def test_one_name_sufficient(self) -> None:
        """One entity name found, no rel keyword -> accepted in narrative (rejected in standard)."""
        rel = {"source": 0, "target": 1, "type": "spoke_to", "sent_ref": "S1"}
        # S1 has "Prince Andrei" but not "Natasha"
        assert (
            validate_relationship_evidence(rel, self.ENTITIES, self.SENTENCES, mode="narrative")
            is True
        )
        # Same should fail in standard (no rel keyword "spoke" in S1 which says "rode")
        assert (
            validate_relationship_evidence(rel, self.ENTITIES, self.SENTENCES, mode="standard")
            is False
        )

    def test_zero_names_rel_keyword_found(self) -> None:
        """Zero entity names but rel type keyword in text -> accepted."""
        rel = {"source": 0, "target": 1, "type": "spoke_to", "sent_ref": "S2"}
        # S2: "He turned to her and spoke softly..." — no entity names but "spoke" present
        assert (
            validate_relationship_evidence(rel, self.ENTITIES, self.SENTENCES, mode="narrative")
            is True
        )

    def test_zero_names_zero_keywords_rejected(self) -> None:
        """Zero entity names AND zero rel keywords -> rejected."""
        rel = {"source": 0, "target": 1, "type": "influenced", "sent_ref": "S4"}
        # S4: "She danced with grace at the ball." — no names, no "influenced"
        assert (
            validate_relationship_evidence(rel, self.ENTITIES, self.SENTENCES, mode="narrative")
            is False
        )

    def test_short_rel_type_one_name_accepted(self) -> None:
        """Short rel type (no words >= 4 chars) with one name -> accepted (unlike standard)."""
        rel = {"source": 0, "target": 1, "type": "is", "sent_ref": "S1"}
        # S1 has "Prince Andrei" but not "Natasha", rel type "is" has no significant words
        assert (
            validate_relationship_evidence(rel, self.ENTITIES, self.SENTENCES, mode="narrative")
            is True
        )
        # Standard rejects: one entity found but "is" produces no significant keywords
        assert (
            validate_relationship_evidence(rel, self.ENTITIES, self.SENTENCES, mode="standard")
            is False
        )

    def test_invalid_sent_ref_rejected(self) -> None:
        """Invalid sent_ref still rejected in narrative mode."""
        rel = {"source": 0, "target": 1, "type": "spoke_to", "sent_ref": ""}
        assert (
            validate_relationship_evidence(rel, self.ENTITIES, self.SENTENCES, mode="narrative")
            is False
        )

    def test_standard_mode_unchanged(self) -> None:
        """Standard mode behavior is not affected by narrative mode addition."""
        rel = {"source": 0, "target": 1, "type": "rode_through", "sent_ref": "S1"}
        # S1 has "Prince Andrei" + "rode" keyword -> standard should accept
        assert (
            validate_relationship_evidence(rel, self.ENTITIES, self.SENTENCES, mode="standard")
            is True
        )

    def test_strict_mode_unchanged(self) -> None:
        """Strict mode behavior is not affected by narrative mode addition."""
        rel = {"source": 0, "target": 1, "type": "spoke_to", "sent_ref": "S1"}
        # S1 has "Prince Andrei" but NOT "Natasha" -> strict rejects
        assert (
            validate_relationship_evidence(rel, self.ENTITIES, self.SENTENCES, mode="strict")
            is False
        )


# ============================================================================
# QualityCounter increments in filter_entities_by_evidence
# ============================================================================


class TestFilterEntitiesByEvidenceCounters:
    """Each dropped entity must increment EVIDENCE_ENTITIES_DROPPED."""

    # Sentences with no references to the fabricated entity names so all
    # entities will fail evidence validation (sent_ref out-of-bounds).
    SENTENCES = ["Only one short sentence here."]

    @pytest.mark.asyncio
    async def test_increments_once_per_dropped_entity(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two entities with invalid sent_ref -> two counter increments."""
        bumps: list[QualityCounter] = []

        async def fake_increment(*, adapter, source_id, database_name, counter, n=1) -> None:
            bumps.extend([counter] * n)

        monkeypatch.setattr(ev, "increment_quality_counter", fake_increment)

        entities = [
            {"name": "Alpha", "sent_ref": "S99"},
            {"name": "Beta", "sent_ref": "S100"},
        ]
        adapter = MagicMock()

        result, _idx_map, stats = await ev.filter_entities_by_evidence(
            entities,
            self.SENTENCES,
            mode="relaxed",  # relaxed still rejects out-of-bounds refs
            adapter=adapter,
            source_id="src-001",
            database_name="default",
        )

        assert result == []
        assert stats["entities_dropped"] == 2
        assert bumps.count(QualityCounter.EVIDENCE_ENTITIES_DROPPED) == 2

    @pytest.mark.asyncio
    async def test_no_increment_when_adapter_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No counter call when adapter=None (best-effort guard)."""
        bumps: list[QualityCounter] = []

        async def fake_increment(*, adapter, source_id, database_name, counter, n=1) -> None:
            bumps.extend([counter] * n)

        monkeypatch.setattr(ev, "increment_quality_counter", fake_increment)

        entities = [{"name": "Alpha", "sent_ref": "S99"}]

        await ev.filter_entities_by_evidence(
            entities,
            self.SENTENCES,
            mode="relaxed",
            adapter=None,
            source_id="src-001",
            database_name="default",
        )

        assert bumps == []

    @pytest.mark.asyncio
    async def test_no_increment_when_source_id_is_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No counter call when source_id=None (best-effort guard)."""
        bumps: list[QualityCounter] = []

        async def fake_increment(*, adapter, source_id, database_name, counter, n=1) -> None:
            bumps.extend([counter] * n)

        monkeypatch.setattr(ev, "increment_quality_counter", fake_increment)

        entities = [{"name": "Alpha", "sent_ref": "S99"}]
        adapter = MagicMock()

        await ev.filter_entities_by_evidence(
            entities,
            self.SENTENCES,
            mode="relaxed",
            adapter=adapter,
            source_id=None,
            database_name="default",
        )

        assert bumps == []

    @pytest.mark.asyncio
    async def test_no_increment_for_kept_entities(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Entities that pass validation must NOT trigger a counter increment."""
        bumps: list[QualityCounter] = []

        async def fake_increment(*, adapter, source_id, database_name, counter, n=1) -> None:
            bumps.extend([counter] * n)

        monkeypatch.setattr(ev, "increment_quality_counter", fake_increment)

        # S1 references the first (and only) sentence — relaxed mode accepts it
        entities = [{"name": "Alpha", "sent_ref": "S1"}]
        adapter = MagicMock()

        result, _idx_map, stats = await ev.filter_entities_by_evidence(
            entities,
            self.SENTENCES,
            mode="relaxed",
            adapter=adapter,
            source_id="src-001",
            database_name="default",
        )

        assert len(result) == 1
        assert bumps == []


# ============================================================================
# QualityCounter increments in filter_relationships_by_evidence
# ============================================================================


class TestFilterRelationshipsByEvidenceCounters:
    """Each dropped relationship must increment EVIDENCE_RELATIONSHIPS_DROPPED."""

    ENTITIES = [{"name": "Alpha"}, {"name": "Beta"}]
    SENTENCES = ["Only one short sentence here."]

    @pytest.mark.asyncio
    async def test_increments_once_per_dropped_relationship(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two relationships with invalid sent_ref -> two counter increments."""
        bumps: list[QualityCounter] = []

        async def fake_increment(*, adapter, source_id, database_name, counter, n=1) -> None:
            bumps.extend([counter] * n)

        monkeypatch.setattr(ev, "increment_quality_counter", fake_increment)

        relationships = [
            {"source": 0, "target": 1, "type": "related_to", "sent_ref": "S99"},
            {"source": 0, "target": 1, "type": "related_to", "sent_ref": "S100"},
        ]
        adapter = MagicMock()

        result, stats = await ev.filter_relationships_by_evidence(
            relationships,
            self.ENTITIES,
            self.SENTENCES,
            mode="relaxed",
            adapter=adapter,
            source_id="src-001",
            database_name="default",
        )

        assert result == []
        assert stats["relationships_dropped"] == 2
        assert bumps.count(QualityCounter.EVIDENCE_RELATIONSHIPS_DROPPED) == 2

    @pytest.mark.asyncio
    async def test_no_increment_when_adapter_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No counter call when adapter=None (best-effort guard)."""
        bumps: list[QualityCounter] = []

        async def fake_increment(*, adapter, source_id, database_name, counter, n=1) -> None:
            bumps.extend([counter] * n)

        monkeypatch.setattr(ev, "increment_quality_counter", fake_increment)

        relationships = [
            {"source": 0, "target": 1, "type": "related_to", "sent_ref": "S99"},
        ]

        await ev.filter_relationships_by_evidence(
            relationships,
            self.ENTITIES,
            self.SENTENCES,
            mode="relaxed",
            adapter=None,
            source_id="src-001",
            database_name="default",
        )

        assert bumps == []

    @pytest.mark.asyncio
    async def test_no_increment_for_kept_relationships(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Relationships that pass validation must NOT trigger a counter increment."""
        bumps: list[QualityCounter] = []

        async def fake_increment(*, adapter, source_id, database_name, counter, n=1) -> None:
            bumps.extend([counter] * n)

        monkeypatch.setattr(ev, "increment_quality_counter", fake_increment)

        # S1 is in bounds — relaxed mode accepts
        relationships = [
            {"source": 0, "target": 1, "type": "related_to", "sent_ref": "S1"},
        ]
        adapter = MagicMock()

        result, stats = await ev.filter_relationships_by_evidence(
            relationships,
            self.ENTITIES,
            self.SENTENCES,
            mode="relaxed",
            adapter=adapter,
            source_id="src-001",
            database_name="default",
        )

        assert len(result) == 1
        assert bumps == []
