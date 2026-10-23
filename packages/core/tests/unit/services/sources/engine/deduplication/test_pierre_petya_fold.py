# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The protagonist folded into a one-mention nickname (2026-10-07, both v4 War and Peace arms).

The v4 extraction wrote Pierre 26 times and once as ``E|Pierre|Character|Pétya``
(the drinking party, where Anatole greets him "Here is Pétya!"); the name-day
chunk wrote the Rostóvs' youngest boy as ``E|Pétya|Character|little Pétya``.
The exact step's alias link joined the two groups, and then:

* the survivor ranking put accents above support, so the one ``Pétya``
  mention (an accent) took the label from the 26 ``Pierre`` mentions; the
  steps also passed "members writing the same label" as support, which is
  one for every member after the exact step;
* the descriptor-alias cleaner dropped every alias sharing no word with the
  label ``Pétya``: ``Pierre``, ``Monsieur Pierre``, ``Pierre Bezúkhov``;
* the normalizer every extraction path hands the commit kept a key list of
  its own without ``aliases_given``, ``merged_names``, ``attested_names`` and
  ``descriptors``, so the node kept no name of Pierre's at all.

The same run merged the doll ``Mimi`` into Natásha (her chunk's output listed
``Mimi`` as Natásha's alias and as an entity of its own), and an alias the
text never writes (``Pierre Bezúkhov`` in a chunk that writes only
``Pierre``; ``Person`` written into the alias field) was evidence like any
other. The name ``Pétya`` itself is no extractor invention: the chunk writes
it, addressed to Pierre, so the two Pétyas stay one node by name (a homonym
the name rule cannot split); Pierre now keeps the label, the node and every
name of both.
"""

from __future__ import annotations

from typing import Any

import pytest

from chaoscypher_core.services.sources.engine.deduplication import name_identity as ni
from chaoscypher_core.services.sources.engine.deduplication.service import EntityProcessor
from chaoscypher_core.services.sources.engine.extraction import orchestration
from chaoscypher_core.services.sources.engine.extraction.extractor import run_deduplication
from chaoscypher_core.services.sources.engine.extraction.preprocessor import normalize_entities
from chaoscypher_core.services.sources.engine.extraction.utils.entity_cleaner import (
    clean_descriptor_aliases,
)
from chaoscypher_core.settings import EngineSettings
from tests.fakes.domain_tables import domain_conventions


BOTH = ("literary", "generic")

_PARTY = "Pierre entered the room. “Wait a bit, you fellows.... Here is Pétya! Good man!” cried he."
_NAME_DAY = "Sónya and little Pétya, his youngest boy, had all settled down in the drawing room."
_SALON = "Monsieur Pierre bowed. Pierre smiled at the hostess."


def _group(text: str, entities: list[dict[str, Any]]) -> dict[str, Any]:
    """One extraction group's result, as the chunk extractor hands it to the aggregation."""
    return {
        "raw_entities": [{**e, "sent_ref": "S1"} for e in entities],
        "raw_relationships": [],
        "input_text": text,
        "small_chunk_ids": [],
    }


def _v4_mentions(pierre_mentions: int = 26) -> list[dict[str, Any]]:
    """The v4 shape: many Pierre mentions, one giving ``Pétya``, and the boy's own mention."""
    groups = [
        _group(_SALON, [{"name": "Pierre", "type": "Character", "aliases": ["Monsieur Pierre"]}])
        for _ in range(pierre_mentions - 1)
    ]
    groups.append(_group(_PARTY, [{"name": "Pierre", "type": "Character", "aliases": ["Pétya"]}]))
    groups.append(
        _group(_NAME_DAY, [{"name": "Pétya", "type": "Character", "aliases": ["little Pétya"]}])
    )
    entities = orchestration.aggregate_chunk_results(groups)["entities"]
    for k, entity in enumerate(entities):
        entity["source_chunk_indices"] = [k]
    return entities


async def _dedup(entities: list[dict[str, Any]], domain: str) -> list[dict[str, Any]]:
    out, _rels, _emb, _log = await run_deduplication(
        entities=[dict(e) for e in entities],
        relationships=[],
        detected_domain=domain,
        settings=EngineSettings(),
        embedding_service=None,
    )
    return out


# -- (b) the survivor is the name the text uses -------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", BOTH)
async def test_the_name_26_mentions_write_keeps_the_label_over_a_one_mention_nickname(
    domain: str,
) -> None:
    """The v4 fold: one node, labelled ``Pierre``, holding every name of both records."""
    (pierre,) = [e for e in await _dedup(_v4_mentions(), domain) if e["type"] == "Character"]
    assert pierre["name"] == "Pierre"
    assert {"Monsieur Pierre", "Pétya", "little Pétya"} <= set(pierre["aliases"])
    assert "Pétya" in pierre[ni.MERGED_NAMES_KEY]
    assert len(pierre["source_chunk_indices"]) == 27


def test_support_outranks_accents_and_length_between_two_names() -> None:
    """``Pierre`` (26) over ``Pétya`` (1); one name's spellings still go to the accents."""
    for conv in (domain_conventions("literary"), domain_conventions("generic")):
        candidates = [
            ni.SurvivorCandidate("Pétya", given=("little Pétya",), support=1),
            ni.SurvivorCandidate("Pierre", given=("Pétya",), support=26),
        ]
        assert candidates[ni.pick_survivor(candidates, conv)].label == "Pierre"
        spellings = [
            ni.SurvivorCandidate("Anna Pavlovna Scherer", support=3),
            ni.SurvivorCandidate("Anna Pávlovna Schérer", support=1),
        ]
        assert spellings[ni.pick_survivor(spellings, conv)].label == "Anna Pávlovna Schérer"


def test_a_names_support_is_every_member_written_under_it() -> None:
    """Single mentions: 3 of ``Taras`` against 2 of the longer, accented ``Tárasik``."""
    labels = ["Tárasik", "Taras", "Tárasik", "Taras", "Taras"]
    candidates = [ni.SurvivorCandidate(x, given=(f"{x} alias",), support=1) for x in labels]
    assert labels[ni.pick_survivor(candidates, domain_conventions("literary"))] == "Taras"


def test_record_support_is_the_mentions_behind_an_entity() -> None:
    assert ni.record_support({"name": "Pierre", "source_chunk_indices": [1, 4, 4, 9]}) == 3
    assert ni.record_support({"name": "Pierre", "chunk_index": 7}) == 1
    assert ni.record_support({"label": "Pierre"}) == 1
    assert ni.survivor_candidate({"name": "P", "source_chunk_indices": [1, 2]}).support == 2
    assert ni.survivor_candidate({"name": "P"}, support=9).support == 9  # a node's degree


def _merged_pair() -> list[dict[str, Any]]:
    """The two groups as the exact step leaves them: Pierre's 26 mentions, the boy's one."""
    return [
        {"name": "Pétya", "type": "Character", "aliases": ["little Pétya"],
         "source_chunk_indices": [40]},
        {"name": "Pierre", "type": "Character", "aliases": ["Monsieur Pierre", "Pétya"],
         "source_chunk_indices": list(range(26))},
    ]  # fmt: skip


@pytest.mark.parametrize("domain", BOTH)
def test_the_hierarchical_step_reads_the_mentions_behind_each_member(domain: str) -> None:
    """After the exact step every member writes its own label once: support is its chunks."""
    merged, _rels, _map = EntityProcessor(
        name_conventions=domain_conventions(domain)
    ).resolve_hierarchical_names(_merged_pair(), [])
    assert [e["name"] for e in merged] == ["Pierre"]


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", BOTH)
async def test_the_semantic_step_reads_the_mentions_behind_each_member(domain: str) -> None:
    unique, _mapping, _emb = await EntityProcessor(
        name_conventions=domain_conventions(domain)
    ).deduplicate_entities_semantic(
        _merged_pair(),
        embedding_service=object(),
        similarity_threshold=0.9,
        precomputed_embeddings=[[1.0, 0.0], [1.0, 0.0]],
    )
    assert [e["name"] for e in unique] == ["Pierre"]


# -- (c) a merged-in name is never lost ---------------------------------------------------------


def test_the_cleaner_never_drops_a_name_a_merge_folded_in() -> None:
    """Had ``Pétya`` kept the label, Pierre's names stay: they were an entity's own names."""
    entity = {
        "name": "Pétya",
        "aliases": ["little Pétya", "Pierre", "Monsieur Pierre", "Pierre Bezúkhov", "Hercules"],
        ni.MERGED_NAMES_KEY: ["Pierre"],
    }
    clean_descriptor_aliases([entity], frozenset({"monsieur"}))
    assert entity["aliases"] == ["little Pétya", "Pierre", "Monsieur Pierre", "Pierre Bezúkhov"]


def test_the_normalizer_carries_what_the_name_rule_reads_to_the_commit() -> None:
    """``aliases_given``, ``merged_names``, ``attested_names`` and ``descriptors`` reach the commit."""
    entity = {
        "name": "Pierre",
        "type": "Character",
        "aliases": ["Pétya"],
        ni.GIVEN_ALIASES_KEY: [],
        ni.MERGED_NAMES_KEY: ["Pétya"],
        ni.ATTESTED_NAMES_KEY: ["Pierre", "Pétya"],
        "descriptors": ["Bezúkhov's son"],
    }
    (normalized,) = normalize_entities([entity])
    for key in ni.NAME_RECORD_KEYS:
        assert normalized[key] == entity[key], key


def test_the_commit_stores_every_name_record_key_on_the_node() -> None:
    from types import SimpleNamespace

    from chaoscypher_core.services.sources.engine.commit.entity import (
        _DEFAULT_NODE_TEMPLATE,
        EntityCommitHandler,
    )

    handler = EntityCommitHandler.__new__(EntityCommitHandler)  # the builder reads no state
    entity = {
        "name": "Pierre",
        "aliases": ["Pétya"],
        ni.GIVEN_ALIASES_KEY: [],
        ni.MERGED_NAMES_KEY: ["Pétya"],
        ni.ATTESTED_NAMES_KEY: ["Pierre", "Pétya"],
        "descriptors": ["Bezúkhov's son"],
    }
    props = handler._build_entity_properties(
        entity,
        "Pierre",
        _DEFAULT_NODE_TEMPLATE,
        {_DEFAULT_NODE_TEMPLATE: SimpleNamespace(id=_DEFAULT_NODE_TEMPLATE, properties=[])},
        {"filename": "book.txt"},
        "doc-1",
    )
    for key in ni.NAME_RECORD_KEYS:
        assert props[key] == entity[key], key


# -- (a) an alias is evidence only when its own text writes it, and names no one else there -------


def test_the_aggregation_gives_only_the_aliases_the_text_writes_and_no_other_entity_bears() -> None:
    text = "Here is Pétya! cried Anatole to Pierre. Natásha showed them Mimi, her doll."
    (pierre, natasha, mimi) = orchestration.aggregate_chunk_results(
        [
            _group(
                text,
                [
                    {"name": "Pierre", "type": "Character",
                     "aliases": ["Pétya", "Pierre Bezúkhov", "Person"]},
                    {"name": "Natásha", "type": "Character", "aliases": ["Mimi"]},
                    {"name": "Mimi", "type": "Object", "aliases": []},
                ],
            )
        ]
    )["entities"]  # fmt: skip
    assert pierre[ni.GIVEN_ALIASES_KEY] == ["Pétya"]
    assert pierre["aliases"] == ["Pétya", "Pierre Bezúkhov", "Person"]  # kept, as hints
    assert natasha[ni.GIVEN_ALIASES_KEY] == []
    assert mimi[ni.GIVEN_ALIASES_KEY] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", BOTH)
async def test_an_alias_the_output_lists_as_an_entity_of_its_own_joins_no_one(domain: str) -> None:
    """The v4 literary arm merged the doll into Natásha; the extractor had listed both."""
    groups = [
        _group(
            "Natásha produced Mimi, her doll, from the folds of her frock.",
            [
                {"name": "Natásha", "type": "Character", "aliases": ["Mimi"]},
                {"name": "Mimi", "type": "Object", "aliases": []},
            ],
        ),
        _group(
            "Borís had known that doll Mimi when her nose was whole.",
            [{"name": "Mimi", "type": "Object", "aliases": ["doll Mimi"]}],
        ),
    ]
    entities = orchestration.aggregate_chunk_results(groups)["entities"]
    names = sorted(e["name"] for e in await _dedup(entities, domain))
    assert names == ["Mimi", "Natásha"]


def test_a_given_alias_a_later_step_took_off_is_no_longer_evidence() -> None:
    """``record_given_aliases`` reads the given aliases the entity still carries."""
    item = {"name": "Pierre", "aliases": ["Pétya"], ni.GIVEN_ALIASES_KEY: ["Pétya", "Hercules"]}
    assert ni.record_given_aliases(item) == ["Pétya"]
    node = {"label": "Pierre", "properties": {"aliases": [], ni.GIVEN_ALIASES_KEY: ["Pétya"]}}
    assert ni.record_given_aliases(node) == []


def test_evidence_aliases_is_verbatim_and_whole_words() -> None:
    attested = ni.attested_names(
        ["Pierre", "Pétya", "Petya", "Pierre Bezúkhov"], ["Here is Pétya! said he to Pierre."]
    )
    assert ni.evidence_aliases(["Pétya", "Petya", "Pierre Bezúkhov"], attested) == ["Pétya"]
    assert ni.evidence_aliases(["Pétya"], attested, others=["pétya"]) == []


# -- epithets and determiners, which the alias rule exposed in the generic arm -------------------


@pytest.mark.parametrize("domain", BOTH)
def test_an_epithet_alias_adds_no_word_to_the_fuller_name(domain: str) -> None:
    """``the famous Prince Andrew`` withheld ``Prince Andrew`` from ``Prince Andrew Bolkónski``."""
    records = [
        ni.NameRecord("Prince Andrew", "Character", ("the famous Prince Andrew",)),
        ni.NameRecord("Prince Andrew Bolkónski", "Character"),
    ]
    (pair,) = ni.plan_name_pairs(records, conventions=domain_conventions(domain))
    assert pair[2].kind == "subset"
    # a fuller alias of capitalised names still names someone else (wave 2)
    records = [
        ni.NameRecord("Princess Bolkónskaya", "Character", ("Lise Meinen",)),
        ni.NameRecord("Princess Mary Bolkónskaya", "Character"),
    ]
    assert ni.plan_name_pairs(records, conventions=domain_conventions(domain)) == []


@pytest.mark.parametrize("domain", BOTH)
def test_a_determiner_is_no_naming_word(domain: str) -> None:
    """``our Emperor`` joined ``our dear Wintzingerode`` as a name with words left out."""
    conv = domain_conventions(domain)
    assert ni.identity_words("our Emperor", conv) == frozenset()
    records = [
        ni.NameRecord("Emperor", "Character", ("our Emperor",)),
        ni.NameRecord("Wintzingerode", "Character", ("our dear Wintzingerode",)),
    ]
    assert ni.plan_name_pairs(records, conventions=conv) == []
    assert ni.alias_as_name("the famous Prince Andrew", ni._title_set(conv.titles)) == (
        "the Prince Andrew"
    )
