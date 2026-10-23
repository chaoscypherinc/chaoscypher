# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The evidence modes are one ladder: strict ⊂ standard ⊂ narrative in what they accept.

Guard for the 2026-10-06 audit (E4): the ``narrative`` mode (the lenient
preset) fell through to a verbatim substring match for entities, so a group
whose entities carry their "most complete name form" kept none of them,
while it accepted a relationship on one endpoint alone.
"""

from __future__ import annotations

from typing import Any

import pytest

from chaoscypher_core.services.sources.engine.extraction.utils.evidence_validator import (
    filter_entities_by_evidence,
    filter_relationships_by_evidence,
    validate_entity_evidence,
    validate_relationship_evidence,
)


LADDER = ("strict", "standard", "narrative")

SALON = [
    "Anna Pávlovna greeted Pierre warmly at the door.",
    "The abbé spoke of the social contract with Pierre.",
    "The little princess laughed and shook her head at Pierre.",
]

SALON_ENTITIES: list[dict[str, Any]] = [
    {"name": "Anna Pávlovna Schérer", "aliases": ["Annette"], "sent_ref": "S1"},
    {"name": "Pierre Bezúkhov", "aliases": [], "sent_ref": "S1"},
    {"name": "Abbé Morio", "aliases": [], "descriptors": ["the abbé"], "sent_ref": "S2"},
    {
        "name": "Lise Bolkónskaya",
        "aliases": [],
        "descriptors": ["the little princess"],
        "sent_ref": "S3",
    },
]


async def _kept(mode: str) -> list[str]:
    kept, _mapping, _stats = await filter_entities_by_evidence(SALON_ENTITIES, SALON, mode=mode)
    return [e["name"] for e in kept]


@pytest.mark.asyncio
async def test_the_salon_group_keeps_its_most_complete_names_in_narrative() -> None:
    """Narrative keeps every entity standard keeps, and the epithet-only one besides.

    Strict (what narrative used to apply to entities) keeps none: no full
    name appears verbatim.
    """
    strict, standard, narrative = [await _kept(mode) for mode in LADDER]
    assert strict == []
    assert standard == ["Anna Pávlovna Schérer", "Pierre Bezúkhov", "Abbé Morio"]
    assert narrative == [e["name"] for e in SALON_ENTITIES]
    assert set(standard) <= set(narrative)


ENTITY_CASES: list[tuple[str, dict[str, Any]]] = [
    ("Albert Einstein was born in Germany.", {"name": "Albert Einstein"}),
    ("Einstein won the prize.", {"name": "Albert Einstein"}),
    ("Al B arrived late.", {"name": "Albert Xyz", "aliases": ["Al B"]}),
    ("The count sat by the window.", {"name": "Count Rostóv"}),
    ("The hostess smiled.", {"name": "Anna Schérer", "descriptors": ["the hostess"]}),
    ("Nobody is here.", {"name": "Anna Schérer", "descriptors": ["the hostess"]}),
    ("Pávlovna nodded.", {"name": "Anna Pavlovna Scherer"}),
    ("Bo ran.", {"name": "Bo"}),
]


def test_entity_acceptance_is_monotone_up_the_ladder() -> None:
    """For every case: accepted by strict ⇒ by standard ⇒ by narrative."""
    for text, entity in ENTITY_CASES:
        verdicts = [
            validate_entity_evidence({**entity, "sent_ref": "S1"}, [text], mode=mode)
            for mode in LADDER
        ]
        assert verdicts == sorted(verdicts), (text, entity, verdicts)


REL_ENTITIES: list[dict[str, Any]] = [
    {"name": "Nicholas Rostóv"},
    {"name": "Pierre Bezúkhov"},
    {"name": "Lise Bolkónskaya", "descriptors": ["the little princess"]},
]

REL_CASES: list[tuple[str, dict[str, Any]]] = [
    ("Nicholas embraced Pierre.", {"source": 0, "target": 1, "type": "interacts_with"}),
    ("Pierre laughed.", {"source": 0, "target": 1, "type": "sibling_of"}),
    ("Pierre was his sibling.", {"source": 0, "target": 1, "type": "sibling_of"}),
    ("The little princess teased Pierre.", {"source": 2, "target": 1, "type": "interacts_with"}),
    ("The little princess teased him.", {"source": 2, "target": 1, "type": "teased"}),
    ("They spoke at length.", {"source": 0, "target": 1, "type": "spoke_with"}),
    ("Nicholas Rostóv and Pierre Bezúkhov.", {"source": 0, "target": 1, "type": "x"}),
]


def test_relationship_acceptance_is_monotone_up_the_ladder() -> None:
    """For every case: accepted by strict ⇒ by standard ⇒ by narrative."""
    for text, rel in REL_CASES:
        verdicts = [
            validate_relationship_evidence(
                {**rel, "sent_ref": "S1"}, REL_ENTITIES, [text], mode=mode
            )
            for mode in LADDER
        ]
        assert verdicts == sorted(verdicts), (text, rel, verdicts)


@pytest.mark.asyncio
async def test_one_endpoint_alone_never_carries_a_kinship_edge() -> None:
    """Nicholas sibling_of Pierre, cited on a sentence naming only Pierre: unevidenced in both.

    The same edge on a sentence naming Pierre and the relation word passes
    both, and the bare copy folds into it; alone, the bare edge is kept
    flagged ``unevidenced`` (keep, flag, never delete, 2026-10-07), never as
    an evidenced fact.
    """
    from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
        UNIVERSAL_VOCABULARY,
    )

    entities = REL_ENTITIES[:2]
    bare = {"source": 0, "target": 1, "type": "sibling_of", "sent_ref": "S1"}
    worded = {**bare, "sent_ref": "S2"}
    sentences = ["Pierre stood alone by the window.", "Pierre was a sibling to them all."]
    for mode in ("standard", "narrative"):
        kept, stats = await filter_relationships_by_evidence(
            [bare, worded], entities, sentences, mode=mode, vocabulary=UNIVERSAL_VOCABULARY
        )
        assert kept == [worded], mode
        assert (stats["relationships_dropped"], stats["relationships_unevidenced"]) == (0, 1)
        kept, _ = await filter_relationships_by_evidence(
            [bare], entities, sentences, mode=mode, vocabulary=UNIVERSAL_VOCABULARY
        )
        assert kept[0]["properties"]["unevidenced"] is True, mode


def test_names_match_accent_folded() -> None:
    """An unaccented name is found in accented text (and the other way round)."""
    assert validate_entity_evidence(
        {"name": "Anna Pavlovna Scherer", "sent_ref": "S1"}, ["Pávlovna nodded."], mode="standard"
    )
    assert validate_entity_evidence(
        {"name": "Mlle Schérer", "sent_ref": "S1"}, ["Mlle Scherer nodded."], mode="strict"
    )
