# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for evidence_validator pure functions."""

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.quality.counters import QualityCounter
from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    DEFAULT_NAME_CONVENTIONS,
)
from chaoscypher_core.services.sources.engine.extraction.utils import (
    evidence_validator as ev,
)
from chaoscypher_core.services.sources.engine.extraction.utils.evidence_validator import (
    CitedText,
    _entity_named,
    _try_best_effort_parse,
    validate_entity_evidence,
    validate_relationship_evidence,
)


if TYPE_CHECKING:
    from chaoscypher_core.services.sources.engine.extraction.utils.known_entities import (
        KnownEntities,
    )


def _named(text: str, name: str, aliases: list[str] | None = None, level: str = "strict") -> bool:
    """``_entity_named`` on one text under the universal conventions."""
    cited = CitedText.read(text, DEFAULT_NAME_CONVENTIONS)
    return _entity_named(cited, {"name": name, "aliases": aliases or []}, level)


# ============================================================================
# strict: the full name or an alias, as consecutive whole words
# ============================================================================


class TestStrictNaming:
    """The strict rung matches whole names, never a name inside a word."""

    def test_exact_match(self) -> None:
        assert _named("Alice went home", "Alice") is True

    def test_case_insensitive(self) -> None:
        assert _named("alice went home", "Alice") is True

    def test_no_match(self) -> None:
        assert _named("Bob went home", "Alice") is False

    def test_alias_match(self) -> None:
        assert _named("Dr. Smith arrived", "John Smith", ["Dr. Smith"]) is True

    def test_alias_no_match(self) -> None:
        assert _named("Bob arrived", "Alice", ["Carol"]) is False

    def test_empty_name_names_no_one(self) -> None:
        assert _named("some text", "") is False  # until wave 6 "" was "in" every text

    def test_a_name_inside_a_word_is_no_name(self) -> None:
        assert _named("The princess arrived", "prince") is False  # was a substring match
        assert _named("Anna arrived", "Ann") is False


# ============================================================================
# standard: a naming word, whole
# ============================================================================


class TestStandardNaming:
    """Standard-mode naming words: whole words of four letters or more, titles left out."""

    def test_significant_word_found(self) -> None:
        assert _named("Einstein developed the theory", "Albert Einstein", level="standard")

    def test_short_words_ignored(self) -> None:
        # "Al" and "Bo" are under four letters: only the whole name names them
        assert not _named("Something else entirely", "Al Bo", level="standard")

    def test_alias_words_checked(self) -> None:
        assert _named("The professor lectured", "A.E.", ["Professor Einstein"], level="standard")

    def test_no_significant_match(self) -> None:
        assert not _named("Unrelated content here", "Albert Einstein", level="standard")

    def test_punctuation_stripped(self) -> None:
        assert _named("Einstein was remarkable", "Einstein,", level="standard")

    def test_a_naming_word_inside_another_word_names_no_one(self) -> None:
        assert not _named("The Einsteinian view prevailed", "Albert Einstein", level="standard")


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

    def test_one_name_alone_is_not_enough(self) -> None:
        """One endpoint named, no relation word -> rejected in narrative, as in standard."""
        rel = {"source": 0, "target": 1, "type": "spoke_to", "sent_ref": "S1"}
        # S1 has "Prince Andrei" but not "Natasha", and no "spoke"
        for mode in ("narrative", "standard"):
            assert (
                validate_relationship_evidence(rel, self.ENTITIES, self.SENTENCES, mode=mode)
                is False
            )

    def test_zero_names_rel_keyword_rejected(self) -> None:
        """Zero endpoints named, relation word present -> rejected (no endpoint at all)."""
        rel = {"source": 0, "target": 1, "type": "spoke_to", "sent_ref": "S2"}
        # S2: "He turned to her and spoke softly..." — no entity names but "spoke" present
        assert (
            validate_relationship_evidence(rel, self.ENTITIES, self.SENTENCES, mode="narrative")
            is False
        )

    def test_zero_names_zero_keywords_rejected(self) -> None:
        """Zero entity names AND zero rel keywords -> rejected."""
        rel = {"source": 0, "target": 1, "type": "influenced", "sent_ref": "S4"}
        # S4: "She danced with grace at the ball." — no names, no "influenced"
        assert (
            validate_relationship_evidence(rel, self.ENTITIES, self.SENTENCES, mode="narrative")
            is False
        )

    def test_one_name_plus_relation_word_accepted(self) -> None:
        """One endpoint named plus a vocabulary word of the relation -> accepted in narrative and standard."""
        from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
            UNIVERSAL_VOCABULARY,
        )

        rel = {"source": 0, "target": 1, "type": "sibling_of", "sent_ref": "S1"}
        sentences = ["Prince Andrei rode beside his sister.", *self.SENTENCES[1:]]
        for mode in ("narrative", "standard"):
            assert (
                validate_relationship_evidence(
                    rel, self.ENTITIES, sentences, mode=mode, vocabulary=UNIVERSAL_VOCABULARY
                )
                is True
            )

    def test_one_name_plus_the_labels_own_word_rejected(self) -> None:
        """The label's own words are never evidence: "rode" does not state ``rode_with`` (wave 5)."""
        rel = {"source": 0, "target": 1, "type": "rode_with", "sent_ref": "S1"}
        for mode in ("narrative", "standard"):
            assert (
                validate_relationship_evidence(rel, self.ENTITIES, self.SENTENCES, mode=mode)
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
        # S1 has "Prince Andrei" + the label's own word "rode": no vocabulary states it
        assert (
            validate_relationship_evidence(rel, self.ENTITIES, self.SENTENCES, mode="standard")
            is False
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
    async def test_dropped_entities_fold_into_one_counter_write(self) -> None:
        """M dropped entities -> ONE ``n=M`` UPDATE, not M blocking commits."""
        entities = [
            {"name": "Alpha", "sent_ref": "S99"},
            {"name": "Beta", "sent_ref": "S100"},
            {"name": "Gamma", "sent_ref": "S101"},
        ]
        adapter = MagicMock()

        await ev.filter_entities_by_evidence(
            entities,
            self.SENTENCES,
            mode="relaxed",
            adapter=adapter,
            source_id="src-001",
            database_name="default",
        )

        adapter.increment_source_counter.assert_called_once_with(
            source_id="src-001",
            database_name="default",
            column=QualityCounter.EVIDENCE_ENTITIES_DROPPED.value,
            n=3,
        )

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
    """Each unevidenced relationship kept increments RELATIONSHIPS_UNEVIDENCED_KEPT; each drop
    (an endpoint that is no entity) EVIDENCE_RELATIONSHIPS_DROPPED (keep, flag, never delete).
    """

    ENTITIES = [{"name": "Alpha"}, {"name": "Beta"}]
    SENTENCES = ["Only one short sentence here."]

    @pytest.mark.asyncio
    async def test_increments_once_per_kept_and_per_dropped_relationship(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two with an out-of-range sent_ref are kept flagged; one with no target entity drops."""
        bumps: list[QualityCounter] = []
        writes: list[tuple[QualityCounter, int]] = []

        async def fake_increment(*, adapter, source_id, database_name, counter, n=1) -> None:
            bumps.extend([counter] * n)
            writes.append((counter, n))

        monkeypatch.setattr(ev, "increment_quality_counter", fake_increment)

        relationships = [
            {"source": 0, "target": 1, "type": "related_to", "sent_ref": "S99"},
            {"source": 0, "target": 1, "type": "related_to", "sent_ref": "S100"},
            {"source": 0, "target": 7, "type": "related_to", "sent_ref": "S1"},
        ]
        adapter = MagicMock()

        result, stats = await ev.filter_relationships_by_evidence(
            relationships,
            self.ENTITIES,
            self.SENTENCES,
            mode="standard",
            adapter=adapter,
            source_id="src-001",
            database_name="default",
        )

        assert [r["sent_ref"] for r in result] == ["S99", "S100"]
        assert all(r["properties"]["unevidenced"] is True for r in result)
        assert result[0]["properties"]["evidence_reason"] == (
            "it cites S99, outside its chunk's 1 sentences"
        )
        assert (stats["relationships_dropped"], stats["relationships_unevidenced"]) == (1, 2)
        assert bumps.count(QualityCounter.RELATIONSHIPS_UNEVIDENCED_KEPT) == 2
        assert bumps.count(QualityCounter.EVIDENCE_RELATIONSHIPS_DROPPED) == 1
        # One write per counter, not one per item (the blocking-commit fix).
        assert sorted(writes, key=lambda w: w[0].value) == sorted(
            [
                (QualityCounter.EVIDENCE_RELATIONSHIPS_DROPPED, 1),
                (QualityCounter.RELATIONSHIPS_UNEVIDENCED_KEPT, 2),
            ],
            key=lambda w: w[0].value,
        )

    @pytest.mark.asyncio
    async def test_dropped_relationships_fold_into_one_counter_write(self) -> None:
        """M dropped relationships -> ONE ``n=M`` UPDATE, not M blocking commits.

        Only an endpoint that is no entity of the chunk drops (keep, flag, never
        delete); a bad sent_ref between real entities is kept flagged instead.
        """
        relationships = [
            {"source": 0, "target": 7, "type": "related_to", "sent_ref": "S1"},
            {"source": 8, "target": 1, "type": "related_to", "sent_ref": "S1"},
            {"source": 1, "target": 9, "type": "related_to", "sent_ref": "S1"},
        ]
        adapter = MagicMock()

        await ev.filter_relationships_by_evidence(
            relationships,
            self.ENTITIES,
            self.SENTENCES,
            mode="standard",  # relaxed skips the endpoint check
            adapter=adapter,
            source_id="src-001",
            database_name="default",
        )

        adapter.increment_source_counter.assert_called_once_with(
            source_id="src-001",
            database_name="default",
            column=QualityCounter.EVIDENCE_RELATIONSHIPS_DROPPED.value,
            n=3,
        )

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


# -- one reading of a sent_ref (CitedSentences) ---------------------------------------------


_CITED = ["[S1] Pierre came in.", "[S2] Anna Pávlovna smiled.", "[S3] The rain fell."]


def test_cited_sentences_reads_a_reference_or_says_why_it_cannot() -> None:
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        DEFAULT_NAME_CONVENTIONS,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.evidence_validator import (
        CitedSentences,
        EvidenceFailure,
    )

    ok = CitedSentences.of("S2-S3", _CITED)
    assert ok.failure is None and ok.reason is None
    assert ok.sentences == (_CITED[1], _CITED[2])
    assert "pavlovna" in ok.read(DEFAULT_NAME_CONVENTIONS).words
    none = CitedSentences.of(None, _CITED)
    assert (none.failure, none.reason) == (EvidenceFailure.NO_SENT_REF, "it cites no sentence")
    bad = CitedSentences.of("S1 to S2", _CITED)
    assert bad.failure is EvidenceFailure.UNREADABLE_SENT_REF
    assert bad.reason == "its sentence reference 'S1 to S2' names no sentence"
    repaired = CitedSentences.of("S1 to S2", _CITED, repair=True)
    assert repaired.failure is None and repaired.sent_ref == "S1-S2"
    far = CitedSentences.of("S9", _CITED)
    assert far.failure is EvidenceFailure.SENT_REF_OUT_OF_RANGE
    assert far.reason == "it cites S9, outside its chunk's 3 sentences"


@pytest.mark.parametrize("sent_ref", [None, "", "S1 to S2", "S9", "S2"])
def test_the_entity_and_relationship_verdicts_read_the_reference_through_it(
    sent_ref: str | None,
) -> None:
    from chaoscypher_core.services.sources.engine.extraction.utils.evidence_validator import (
        CitedSentences,
        EvidenceFailure,
        entity_evidence,
        relationship_evidence,
    )

    entities = [{"name": "Pierre"}, {"name": "Anna Pávlovna"}]
    entity = {"name": "Anna Pávlovna", "sent_ref": sent_ref}
    ref = CitedSentences.of(sent_ref, _CITED)
    verdict = entity_evidence(entity, _CITED, "standard", candidates=entities)
    if ref.failure is None:
        assert verdict.evidenced
    else:
        assert (verdict.failure, verdict.reason) == (ref.failure, ref.reason)
    rel = {"source": 0, "target": 1, "type": "knows", "sent_ref": sent_ref}
    rel_ref = CitedSentences.of(sent_ref, _CITED, repair=True)
    rel_verdict = relationship_evidence(rel, entities, _CITED, "standard")
    if rel_ref.failure is not None:
        assert (rel_verdict.failure, rel_verdict.reason) == (rel_ref.failure, rel_ref.reason)
    else:  # a readable reference: the verdict is about the sentences, never the reference
        assert rel_verdict.failure not in {
            EvidenceFailure.NO_SENT_REF,
            EvidenceFailure.UNREADABLE_SENT_REF,
            EvidenceFailure.SENT_REF_OUT_OF_RANGE,
        }


def test_cited_sentences_hold_the_one_sentence_before_the_first_cited() -> None:
    from chaoscypher_core.services.sources.engine.extraction.utils.evidence_validator import (
        CitedSentences,
    )

    ref = CitedSentences.of("S2-S3", _CITED)
    assert ref.preceding == _CITED[0]
    assert "pierre" in ref.read(DEFAULT_NAME_CONVENTIONS, look_back=True).words
    assert "pierre" not in ref.read(DEFAULT_NAME_CONVENTIONS).words
    assert CitedSentences.of("S1", _CITED).preceding == ""
    assert CitedSentences.of("S3, S2", _CITED).preceding == _CITED[0]
    assert CitedSentences.of("S9", _CITED).preceding == ""


# -- known endpoints (spec 2026-10-11 D8) ----------------------------------------------------


def _known_count() -> KnownEntities:
    from chaoscypher_core.services.sources.engine.extraction.utils.known_entities import (
        KnownEntities,
    )
    from tests.fakes.domain_tables import domain_conventions

    known = KnownEntities(conventions=domain_conventions("literary"))
    known.add_group(
        [
            {
                "name": "Count Oswin Varrow",
                "type": "Person",
                "aliases": ["the count"],
                "properties": {},
            }
        ],
        [],
    )
    return known


def _child_of_verdict(
    count_record: dict[str, Any], sentence: str, *, known: KnownEntities | None
) -> ev.RelationshipEvidence:
    from tests.fakes.domain_tables import domain_conventions, domain_vocabulary

    return ev.relationship_evidence(
        {"source": 0, "target": 1, "type": "child_of", "sent_ref": "S1"},
        [{"name": "Ilse Marren", "type": "Person"}, count_record],
        [f"[S1] {sentence}"],
        "standard",
        vocabulary=domain_vocabulary("literary"),
        conventions=domain_conventions("literary"),
        known=known,
    )


_KNOWN_COUNT: dict[str, Any] = {
    "name": "Count Oswin Varrow",
    "type": "Person",
    "aliases": ["the count"],
}
_THE_COUNT: dict[str, Any] = {"name": "the count", "type": "Person"}


@pytest.mark.parametrize(
    "sentence",
    [
        "The count threw his arms round the little girl.",
        "The count threw his arms round his little daughter.",
    ],
)
def test_a_known_endpoint_is_never_named_by_a_title_or_its_own_reference_alias(
    sentence: str,
) -> None:
    from chaoscypher_core.services.sources.engine.extraction.utils.evidence_validator import (
        EvidenceFailure,
    )

    verdict = _child_of_verdict(_KNOWN_COUNT, sentence, known=_known_count())
    assert verdict.failure is EvidenceFailure.KNOWN_ENDPOINT_NOT_NAMED
    assert verdict.reason == (
        '"Count Oswin Varrow" is not named in S1 (a known entity needs its name in the sentence)'
    )
    # without the list the record's alias and the title rung name it, as before
    assert _child_of_verdict(_KNOWN_COUNT, sentence, known=None).failure is not (
        EvidenceFailure.KNOWN_ENDPOINT_NOT_NAMED
    )


@pytest.mark.parametrize(
    ("sentence", "evidenced"),
    [
        ("The count threw his arms round the little girl.", False),  # no relation word
        ("The count threw his arms round his little daughter.", True),
    ],
)
def test_a_reference_endpoint_reads_the_title_rung_as_before(
    sentence: str, *, evidenced: bool
) -> None:
    known = _child_of_verdict(_THE_COUNT, sentence, known=_known_count())
    assert known == _child_of_verdict(_THE_COUNT, sentence, known=None)
    assert known.evidenced is evidenced
    if not evidenced:  # the title names "the count"; the edge falls on the missing relation word
        assert known.failure is ev.EvidenceFailure.RELATION_NOT_STATED
        assert (known.reason or "").startswith(
            '"the count" is named in S1 but "Ilse Marren" is not'
        )


def test_a_known_endpoint_is_named_by_its_name_or_the_sentence_before() -> None:
    known = _known_count()
    named = _child_of_verdict(
        _KNOWN_COUNT, "Count Oswin Varrow threw his arms round his little daughter.", known=known
    )
    assert named.evidenced
    from tests.fakes.domain_tables import domain_conventions, domain_vocabulary

    looked_back = ev.relationship_evidence(
        {"source": 0, "target": 1, "type": "child_of", "sent_ref": "S2"},
        [{"name": "Ilse Marren", "type": "Person"}, _KNOWN_COUNT],
        ["[S1] Count Oswin Varrow came in.", "[S2] The count kissed his little daughter."],
        "standard",
        vocabulary=domain_vocabulary("literary"),
        conventions=domain_conventions("literary"),
        known=known,
    )
    assert looked_back.evidenced


@pytest.mark.parametrize("mode", ["strict", "standard"])
def test_a_known_endpoint_is_named_by_rule_two_as_the_guard_reads_it(mode: str) -> None:
    """The edge rule reads rule 1 or rule 2, as the name guard does (Task 14 round 1).

    A record written as the entry's distinctive alias ("the King of Valdria"), or under the
    entry's own name, is named where the edge's sentence writes that alias whole; the alias's
    place word alone ("Valdria") names no one.
    """
    from chaoscypher_core.services.sources.engine.extraction.utils.known_entities import (
        KnownEntities,
    )

    known = KnownEntities()
    known.add_group(
        [{"name": "Prince Orlan Vesk", "type": "Person", "aliases": ["the King of Valdria"]}],
        [],
    )
    rel = {"source": 0, "target": 1, "type": "knows", "sent_ref": "S1"}
    whole = ["[S1] Ilse Marren bowed to the King of Valdria."]
    place = ["[S1] Ilse Marren rode to Valdria."]
    for name in ("the King of Valdria", "Prince Orlan Vesk"):
        entities: list[dict[str, Any]] = [
            {"name": "Ilse Marren", "type": "Person"},
            {"name": name, "type": "Person"},
        ]
        assert ev.relationship_evidence(rel, entities, whole, mode, known=known).evidenced, name
        verdict = ev.relationship_evidence(rel, entities, place, mode, known=known)
        assert verdict.failure is ev.EvidenceFailure.KNOWN_ENDPOINT_NOT_NAMED, name


def test_strict_reads_a_known_endpoint_by_phrase_only() -> None:
    known = _known_count()
    entities: list[dict[str, Any]] = [{"name": "Tobin"}, _KNOWN_COUNT]
    rel = {"source": 0, "target": 1, "type": "knows", "sent_ref": "S1"}
    by_word = ["[S1] Tobin saw Varrow."]
    assert ev.relationship_evidence(rel, entities, by_word, "standard", known=known).evidenced
    strict = ev.relationship_evidence(rel, entities, by_word, "strict", known=known)
    assert strict.failure is ev.EvidenceFailure.KNOWN_ENDPOINT_NOT_NAMED
    by_name = ["[S1] Tobin saw Count Oswin Varrow."]
    assert ev.relationship_evidence(rel, entities, by_name, "strict", known=known).evidenced


@pytest.mark.asyncio
async def test_a_known_endpoint_edge_is_kept_flagged_and_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.fakes.domain_tables import domain_conventions, domain_vocabulary

    bumps: list[QualityCounter] = []

    async def fake_increment(**kw: Any) -> None:
        bumps.extend([kw["counter"]] * kw.get("n", 1))

    monkeypatch.setattr(ev, "increment_quality_counter", fake_increment)
    rel = {"source": 0, "target": 1, "type": "child_of", "sent_ref": "S1"}
    kept, stats = await ev.filter_relationships_by_evidence(
        [rel],
        [{"name": "Ilse Marren", "type": "Person"}, _KNOWN_COUNT],
        ["[S1] The count threw his arms round the little girl."],
        mode="standard",
        adapter=MagicMock(),
        source_id="src-001",
        vocabulary=domain_vocabulary("literary"),
        conventions=domain_conventions("literary"),
        known=_known_count(),
    )
    assert len(kept) == 1 and kept[0]["properties"]["unevidenced"] is True
    assert kept[0]["properties"]["evidence_reason"] == (
        '"Count Oswin Varrow" is not named in S1 (a known entity needs its name in the sentence)'
    )
    assert stats["relationships_unevidenced"] == 1
    assert bumps.count(QualityCounter.RELATIONSHIPS_UNEVIDENCED_KEPT) == 1


def test_an_alias_whose_reading_holds_no_naming_word_names_by_phrase_only() -> None:
    """``alias_name``: "My wife" reads as "My", no name; its "wife" is no naming word.

    Review round 1: every alias stays phrase evidence (the strict rung as it
    was); only the word rung reads the aliases that hold a naming word.
    """
    conv = DEFAULT_NAME_CONVENTIONS
    assert ev.alias_name("My wife", conv) is None and ev.alias_name("his wife", conv) is None
    assert ev.alias_name("the King of Prussia", conv) == "the King of Prussia"
    assert ev.alias_name("our dear Wintzingerode", conv) == "our Wintzingerode"
    lise = {"name": "Lise", "aliases": ["my wife"]}
    for text in ("I told my sister.", "The wife rose."):
        cited = CitedText.read(text, conv)
        assert not any(_entity_named(cited, lise, level) for level in ("strict", "standard"))
    mine = CitedText.read("My wife rose.", conv)
    assert _entity_named(mine, lise, "strict") and _entity_named(mine, lise, "standard")
    assert _named("Lise came.", "Lise", ["my wife"])  # the name still names her


@pytest.mark.parametrize("mode", ["strict", "standard"])
def test_a_record_whose_name_is_no_known_entry_keeps_todays_rungs(mode: str) -> None:
    """D8: a reference-named record and a record's reference alias name by phrase, list or none."""
    known = _known_count()
    the_count = {"name": "the count", "type": "Person", "sent_ref": "S1"}
    assert ev.entity_evidence(the_count, ["[S1] The count rose."], mode).evidenced
    rostov = {"name": "Count Ilyá Rostóv", "type": "Person", "aliases": ["the count"]}
    rel = {"source": 0, "target": 1, "type": "knows", "sent_ref": "S1"}
    entities = [{"name": "Natásha", "type": "Person"}, rostov]
    for list_ in (None, known):  # Rostóv is no entry of the list: the list changes nothing
        verdict = ev.relationship_evidence(
            rel, entities, ["[S1] Natásha kissed the count."], mode, known=list_
        )
        assert verdict.evidenced
