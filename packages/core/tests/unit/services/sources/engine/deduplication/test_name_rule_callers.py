# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The one name rule, through its real callers (audit E2, E3, E6).

The pre-commit deduplication (``run_deduplication`` -> exact dedup ->
``resolve_hierarchical_names``) and the post-commit identity resolver
(``plan_merges``) share ``name_identity``: a pair one merges, the other
merges. The Book One splits the 0.4 word-subset rule folded wrongly stay
apart; the ones that are one person merge.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from chaoscypher_core.services.sources.engine.deduplication import service as dedup_service_mod
from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    GIVEN_ALIASES_KEY,
    UNIVERSAL_TITLE_WORDS,
    NameConventions,
    NameRecord,
    fold,
    name_words,
    plan_name_pairs,
    same_entity,
)
from chaoscypher_core.services.sources.engine.deduplication.service import EntityProcessor
from chaoscypher_core.services.sources.engine.extraction.extractor import run_deduplication
from chaoscypher_core.settings import EngineSettings
from tests.fakes.domain_tables import domain_conventions


_LITERARY = (
    Path(__file__).parents[6]
    / "src/chaoscypher_core/services/sources/engine/extraction/domains/plugins/literary.jsonld"
)


def _literary_title_words() -> frozenset[str]:
    """The title words the literary domain hands the deduplication."""
    return frozenset(json.loads(_LITERARY.read_text())["title_words"])


class _LiteraryResolver:
    """The domain lookups ``run_deduplication`` makes, answered for the literary domain."""

    def get_domain_title_words(self, _domain: str | None) -> frozenset[str]:
        """The literary title words."""
        return _literary_title_words()

    def get_domain_name_conventions(self, _domain: str | None) -> NameConventions:
        """The literary naming conventions (its title words, gendered titles, name forms)."""
        return domain_conventions("literary")

    def get_domain_symmetric_relationships(self, _domain: str | None) -> list[str]:
        """None needed here."""
        return []

    def get_domain_inverse_relationships(self, _domain: str | None) -> dict[str, str]:
        """None needed here."""
        return {}


async def _dedup_names(
    names: list[str], *, aliases: dict[str, list[str]] | None = None
) -> list[dict]:
    """Run the real deduplication (exact + hierarchical, no embeddings) over ``names``."""
    entities = [
        {"name": n, "type": "Character", "aliases": list((aliases or {}).get(n, []))} for n in names
    ]
    deduplicated, _rels, _emb, _log = await run_deduplication(
        entities=entities,
        relationships=[],
        detected_domain="literary",
        settings=EngineSettings(),
        embedding_service=None,
        domain_resolver=_LiteraryResolver(),
    )
    return deduplicated


def _groups(entities: list[dict]) -> set[frozenset[str]]:
    """Each surviving entity as the set of names it holds (its name plus its aliases)."""
    return {frozenset([e["name"], *e.get("aliases", [])]) for e in entities}


def test_the_literary_title_words_cover_the_shared_titles() -> None:
    """The domain's conventions hold every universal title: the floor its own words add to."""
    folded = {fold(w) for w in domain_conventions("literary").titles}
    floor = {fold(w) for w in UNIVERSAL_TITLE_WORDS}
    assert floor <= folded, sorted(floor - folded)
    assert {fold(w) for w in _literary_title_words()} <= folded


@pytest.mark.asyncio
async def test_a_title_and_surname_merge_into_the_one_full_name() -> None:
    """``Mlle Schérer`` is ``Anna Pávlovna Schérer`` with words left out."""
    out = await _dedup_names(["Mlle Schérer", "Anna Pávlovna Schérer", "Prince Vasíli Kurágin"])
    assert [e["name"] for e in out] == ["Anna Pávlovna Schérer", "Prince Vasíli Kurágin"]
    assert "Mlle Schérer" in out[0]["aliases"]
    # which aliases were given under the name leaves the dedup for the commit to store
    assert out[0][GIVEN_ALIASES_KEY] == []
    assert out[1][GIVEN_ALIASES_KEY] == []


@pytest.mark.asyncio
async def test_a_short_name_that_fits_two_people_merges_with_neither() -> None:
    """Count Bezúkhov (the father) beside Pierre and Cyril; Nicholas beside two Nicholases."""
    out = await _dedup_names(
        [
            "Count Bezúkhov",
            "Pierre Bezúkhov",
            "Cyril Vladímirovich Bezúkhov",
            "Nicholas",
            "Prince Nicholas Bolkónski",
            "Nicholas Rostóv",
        ]
    )
    assert sorted(e["name"] for e in out) == sorted(
        [
            "Count Bezúkhov",
            "Pierre Bezúkhov",
            "Cyril Vladímirovich Bezúkhov",
            "Nicholas",
            "Prince Nicholas Bolkónski",
            "Nicholas Rostóv",
        ]
    )


@pytest.mark.asyncio
async def test_the_little_princess_and_princess_mary_stay_apart_beside_another_bolkonskaya() -> (
    None
):
    """``Princess Bolkónskaya`` (Lise) is not folded into Mary when a second fuller name fits."""
    out = await _dedup_names(
        ["Princess Bolkónskaya", "Princess Mary Bolkónskaya", "Lise Bolkónskaya"]
    )
    assert len(out) == 3


@pytest.mark.asyncio
async def test_two_names_that_each_add_a_word_never_merge() -> None:
    """A shared surname with a word added on both sides (the 0.4 rule merged these)."""
    out = await _dedup_names(
        [
            "Princess Mary Bolkónskaya",
            "Princess Lise Bolkónskaya",
            "Julie Karágina",
            "Márya Karágina",
        ]
    )
    assert len(out) == 4


@pytest.mark.asyncio
async def test_a_bare_title_never_merges_into_a_titled_name() -> None:
    """``Princess`` has no naming word: the old substring path folded it into any princess."""
    out = await _dedup_names(["Princess", "Princess Mary Bolkónskaya"])
    assert len(out) == 2


@pytest.mark.asyncio
async def test_an_alias_given_under_one_name_merges_the_other() -> None:
    """Rule A: ``Bóry`` is an alias the LLM gave Borís."""
    out = await _dedup_names(["Borís", "Bóry"], aliases={"Borís": ["Bóry"]})
    assert len(out) == 1


def test_the_pairwise_rule() -> None:
    """Same words, alias, strict subset; never a different type or words added on both sides."""
    rec = NameRecord
    literary = domain_conventions("literary")
    assert (
        same_entity(rec("Prince Andrew", "C"), rec("Andrew", "C"), conventions=literary).kind
        == "same"
    )  # type: ignore[union-attr]
    assert same_entity(rec("Borís", "C", ("Bóry",)), rec("Bóry", "C")).kind == "alias"  # type: ignore[union-attr]
    assert same_entity(rec("Bob Smith", "C"), rec("Bob Alan Smith", "C")).kind == "subset"  # type: ignore[union-attr]
    assert same_entity(rec("Bob Smith", "C"), rec("Bob Alan Smith", "Org")) is None
    # a generation word says which of two (it is a title word structurally, wave 4)
    assert same_entity(rec("Bob Smith", "C"), rec("Bob Smith Jr", "C")) is None
    assert (
        same_entity(rec("Anna Pávlovna Schérer", "C"), rec("Anna Mikháylovna Schérer", "C")) is None
    )
    assert same_entity(rec("Mary", "C"), rec("Marya", "C")) is None
    assert name_words("Anna Pávlovna Schérer") == {"anna", "pavlovna", "scherer"}


def test_plan_name_pairs_withholds_only_subset_evidence_for_an_ambiguous_name() -> None:
    """An alias still joins an ambiguous short name; a subset does not."""
    records = [
        NameRecord("Count Bezúkhov", "C", ("Cyril Vladímirovich Bezúkhov",)),
        NameRecord("Pierre Bezúkhov", "C"),
        NameRecord("Cyril Vladímirovich Bezúkhov", "C"),
    ]
    pairs = {(i, j): ev.kind for i, j, ev in plan_name_pairs(records)}
    assert pairs == {(0, 2): "alias"}


@pytest.mark.asyncio
async def test_semantic_dedup_keeps_every_member_of_a_three_way_cluster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each duplicate folds into the running merge, not the original (audit E6).

    Three mentions with identical embeddings: the survivor holds both other
    names as aliases, all three chunks and all three cited sentences.
    """

    async def _same_vectors(texts: list[str], _service: Any) -> Any:
        """Identical unit vectors for every text."""
        vectors = [[1.0, 0.0, 0.0, 0.0] for _ in texts]
        return vectors, np.array(vectors)

    monkeypatch.setattr(dedup_service_mod, "generate_entity_embeddings", _same_vectors)
    entities = [
        {
            "name": f"Hostess {n}",
            "type": "Character",
            "aliases": [f"alias {n}"],
            "source_chunk_indices": [n],
            "evidence": [{"sentence": f"Sentence {n}.", "chunk_id": f"c{n}"}],
        }
        for n in range(3)
    ]
    unique, mapping, _emb = await EntityProcessor().deduplicate_entities_semantic(
        entities, embedding_service=object(), similarity_threshold=0.9
    )
    assert len(unique) == 1
    assert set(mapping.values()) == {0}
    survivor = unique[0]
    names = {"Hostess 0", "Hostess 1", "Hostess 2"}
    # the survivor ranking picks the label (three equal names: the tie is the label's)
    assert survivor["name"] in names
    assert names - {survivor["name"]} | {"alias 0", "alias 1", "alias 2"} <= set(
        survivor["aliases"]
    )
    assert survivor["source_chunk_indices"] == [0, 1, 2]
    assert len(survivor["evidence"]) == 3
