# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Keep, flag, never delete: a relationship without evidence is kept flagged, with its reason.

Until 2026-10-07 the per-chunk evidence filter deleted every relationship
whose cited sentence it could not read as stating it, justification and
all: 162 on the B0 literary pack (``war_and_peace_book1-v4b``) and 105 on
the generic one, a third of what the model wrote. An offline replay of the
two packs' recorded pass-1/pass-2 answers under this filter keeps every one
of them (literary 157 flagged and 1 folded into its evidenced copy, generic
99 and 4) and drops none: every relationship the model wrote had two real
endpoints. The cases below are the replay's, with the sentences the model
cited (Book One, group and ``sent_ref`` as the v4b extraction numbered them).
"""

from __future__ import annotations

from typing import Any

import pytest

from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    EVIDENCE_REASON_KEY,
    UNEVIDENCED_KEY,
    is_unevidenced_record,
)
from chaoscypher_core.services.sources.engine.extraction.utils.evidence_validator import (
    EvidenceFailure,
    filter_relationships_by_evidence,
    relationship_evidence,
    sort_relationships_by_evidence,
    validate_relationship_evidence,
)
from tests.fakes.domain_tables import domain_vocabulary


LITERARY = domain_vocabulary("literary")
GENERIC = domain_vocabulary("generic")


def _character(name: str, *aliases: str) -> dict[str, Any]:
    return {"name": name, "type": "Character", "aliases": list(aliases)}


# Group 0, S6 (literary): "She" is Anna Pávlovna; located_in has no relation words.
GRIPPE = [
    "She was, as she said, suffering from la grippe; grippe being then a new word in "
    "St. Petersburg, used only by the elite."
]
# Group 3, S15 (literary): Prince Vasíli speaking to Anna Pávlovna, neither named.
SLAVE = [
    "“I am your faithful slave and to you alone I can confess that my children are the "
    "bane of my life. It is the cross I have to bear. That is how I explain it to myself. It "
    "can’t be helped!”"  # noqa: RUF001 (the text as written)
]
# Group 3, S19 (literary): "a relation of yours" is no word of related_to's vocabulary.
MATCHMAKING = [
    "“They say old maids have a mania for matchmaking, and though I don’t feel that "  # noqa: RUF001
    "weakness in myself as yet, I know a little person who is very unhappy with her father. "
    "She is a relation of yours, Princess Mary Bolkónskaya.”"
]


def _rel(source: int, target: int, label: str, justification: str) -> dict[str, Any]:
    return {
        "source": source,
        "target": target,
        "type": label,
        "confidence": 0.9,
        "sent_ref": "S1",
        "justification": justification,
    }


CASES = [
    (
        [_character("Anna Pávlovna Schérer", "Anna Pávlovna"),
         {"name": "St. Petersburg", "type": "Location", "aliases": []}],
        GRIPPE,
        _rel(0, 1, "located_in", "Anna Pávlovna is in St. Petersburg."),
        EvidenceFailure.LABEL_NEEDS_BOTH_ENDPOINTS,
        '"St. Petersburg" is named in S1 but "Anna Pávlovna Schérer" is not, and '
        "located_in needs both endpoints named (the domain's relation vocabulary has no "
        "words for it)",
    ),
    (
        [_character("Prince Vasíli"), _character("Anna Pávlovna")],
        SLAVE,
        _rel(0, 1, "confides_in", "Prince Vasíli tells Anna Pávlovna that his "
             "children are the bane of his life."),
        EvidenceFailure.ENDPOINTS_NOT_NAMED,
        'neither "Prince Vasíli" nor "Anna Pávlovna" is named in S1',
    ),
    (
        [_character("Anna Pávlovna"), _character("Princess Mary Bolkónskaya")],
        MATCHMAKING,
        _rel(0, 1, "related_to", "Anna Pávlovna mentions that Princess Mary "
             "Bolkónskaya is a relation of Prince Vasíli."),
        EvidenceFailure.RELATION_NOT_STATED,
        '"Princess Mary Bolkónskaya" is named in S1 but "Anna Pávlovna" is not, and '
        "no word of the domain's relation vocabulary for related_to stands there",
    ),
]  # fmt: skip


@pytest.mark.parametrize(("entities", "sentences", "rel", "failure", "reason"), CASES)
def test_the_verdict_names_why_a_book_one_relationship_has_no_evidence(
    entities: list[dict[str, Any]],
    sentences: list[str],
    rel: dict[str, Any],
    failure: EvidenceFailure,
    reason: str,
) -> None:
    verdict = relationship_evidence(rel, entities, sentences, "standard", vocabulary=LITERARY)
    assert (verdict.failure, verdict.reason) == (failure, reason)
    assert not verdict.evidenced and not verdict.dropped
    # the boolean reader is the same decision
    assert not validate_relationship_evidence(
        rel, entities, sentences, "standard", vocabulary=LITERARY
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("entities", "sentences", "rel", "failure", "reason"), CASES)
async def test_the_filter_keeps_it_flagged_with_its_reason_and_justification(
    entities: list[dict[str, Any]],
    sentences: list[str],
    rel: dict[str, Any],
    failure: EvidenceFailure,
    reason: str,
) -> None:
    kept, stats = await filter_relationships_by_evidence(
        [rel], entities, sentences, mode="standard", vocabulary=LITERARY
    )
    assert kept == [{**rel, "properties": {UNEVIDENCED_KEY: True, EVIDENCE_REASON_KEY: reason}}]
    assert is_unevidenced_record(kept[0]["properties"])
    assert stats == {
        "relationships_checked": 1,
        "relationships_dropped": 0,
        "relationships_unevidenced": 1,
    }
    assert "properties" not in rel  # the input is untouched


def test_an_endpoint_that_is_no_entity_is_still_dropped() -> None:
    """It carries nothing anyone can read: dropped in every mode, whatever its sentence."""
    entities = [_character("Anna Pávlovna")]
    for mode in ("strict", "standard", "narrative"):
        for rel in (
            {"source": 0, "target": 3, "type": "knows", "sent_ref": "S1"},
            {"source": "x", "target": 0, "type": "knows", "sent_ref": "S1"},
            {"source": 0, "target": 3, "type": "knows"},
            {"source": 0, "target": 3, "type": "knows", "sent_ref": "S9"},
        ):
            verdict = relationship_evidence(rel, entities, ["Anna Pávlovna smiled."], mode)
            assert verdict.failure is EvidenceFailure.INVALID_ENDPOINT, (mode, rel)
            sorting = sort_relationships_by_evidence(
                [rel], entities, ["Anna Pávlovna smiled."], mode
            )
            assert sorting.relationships == [] and len(sorting.dropped) == 1


def test_every_sentence_reference_failure_keeps_the_relationship() -> None:
    entities = [_character("Anna Pávlovna"), _character("Pierre")]
    base = {"source": 0, "target": 1, "type": "interacts_with"}
    sentences = ["Anna Pávlovna greeted Pierre."]
    expected = {
        None: (EvidenceFailure.NO_SENT_REF, "it cites no sentence"),
        "sentence three": (
            EvidenceFailure.UNREADABLE_SENT_REF,
            "its sentence reference 'sentence three' names no sentence",
        ),
        "S4": (
            EvidenceFailure.SENT_REF_OUT_OF_RANGE,
            "it cites S4, outside its chunk's 1 sentences",
        ),
    }
    for sent_ref, (failure, reason) in expected.items():
        rel = {**base, **({"sent_ref": sent_ref} if sent_ref else {})}
        for mode in ("strict", "standard", "narrative", "relaxed"):
            verdict = relationship_evidence(rel, entities, sentences, mode)
            assert (verdict.failure, verdict.reason) == (failure, reason), (sent_ref, mode)
            (kept,) = sort_relationships_by_evidence([rel], entities, sentences, mode).relationships
            assert kept["properties"] == {UNEVIDENCED_KEY: True, EVIDENCE_REASON_KEY: reason}


def test_strict_mode_names_the_endpoint_it_misses() -> None:
    entities = [_character("Anna Pávlovna"), _character("Pierre")]
    rel = {"source": 0, "target": 1, "type": "sibling_of", "sent_ref": "S1"}
    verdict = relationship_evidence(
        rel, entities, ["Anna Pávlovna had an elder sister."], "strict", vocabulary=LITERARY
    )
    assert verdict.failure is EvidenceFailure.ENDPOINT_NOT_NAMED
    assert verdict.reason == (
        '"Anna Pávlovna" is named in S1 but "Pierre" is not, and strict mode needs both'
    )
    # standard reads the kin word: evidenced
    assert relationship_evidence(
        rel, entities, ["Anna Pávlovna had an elder sister."], "standard", vocabulary=LITERARY
    ).evidenced


def test_an_unevidenced_copy_folds_into_its_evidenced_twin_and_keeps_its_text() -> None:
    """The generic arm's shape (4 on v4b): the same fact twice in one group, one copy evidenced.

    The within-chunk duplicate collapse keeps the higher confidence copy; the
    unevidenced one must not win it, so it folds into its evidenced twin here
    (its justification in ``properties.justifications``) and is not listed twice.
    """
    entities = [_character("Prince Vasíli Kurágin"), _character("Anna Pávlovna Schérer")]
    sentences = [
        "With these words she greeted Prince Vasíli Kurágin, who was the first to arrive.",
        "Prince Vasíli Kurágin kissed Anna Pávlovna Schérer's hand.",
    ]
    unevidenced = {
        **_rel(0, 1, "interacts_with", "He was the first to arrive at her reception."),
        "confidence": 0.95,
    }
    evidenced = {**_rel(0, 1, "interacts_with", "He kissed her hand."), "sent_ref": "S2"}
    sorting = sort_relationships_by_evidence(
        [unevidenced, evidenced], entities, sentences, "standard", vocabulary=GENERIC
    )
    assert sorting.relationships == [
        {
            **evidenced,
            "properties": {
                "justifications": [
                    "He kissed her hand.",
                    "He was the first to arrive at her reception.",
                ]
            },
        }
    ]
    assert [r for r, _ in sorting.folded] == [unevidenced]
    assert sorting.flagged == [] and sorting.dropped == []
