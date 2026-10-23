# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""A person written under a bare title or a descriptor is a reference (2026-10-07, both v4 arms).

The v4 War and Peace extraction writes people under a title or a descriptor
where the text does: ``the count``, ``Emperor``, ``the Emperor``, ``the
vicomte``, ``the little princess``, ``Prince Andrew's wife``. Those records were
dropped (the per-chunk plausibility score dropped three of the generic arm's
four ``the count`` mentions; the one kept, holding the Archives fact a question
needs, survived only because its type column was empty) or kept as separate,
unflagged people (``Emperor`` and ``the Emperor`` two nodes). Now:

* a reference that says something of its own is never dropped by a name filter;
* it is the named person its own names say it is (an alias its output gave it,
  or the one name its own sentences write with its title), never a person its
  output lists beside it nor one found elsewhere or by frequency (two
  emperors);
* anonymous references written alike are one node, flagged
  ``descriptor_reference``; a named survivor is never flagged and always keeps
  the label.
"""

from __future__ import annotations

from typing import Any

import pytest

from chaoscypher_core.services.graph.resolution.plan import plan_resolution
from chaoscypher_core.services.sources.engine.deduplication import name_identity as ni
from chaoscypher_core.services.sources.engine.deduplication.service import (
    merge_node_properties,
)
from chaoscypher_core.services.sources.engine.extraction import orchestration
from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
    ExclusionRule,
)
from chaoscypher_core.services.sources.engine.extraction.extractor import run_deduplication
from chaoscypher_core.services.sources.engine.extraction.utils.entity_cleaner import (
    filter_excluded_entities,
    filter_implausible_entities,
    kept_as_reference,
)
from chaoscypher_core.settings import EngineSettings
from tests.fakes.domain_tables import domain_conventions


BOTH = ("literary", "generic")
FLAG = ni.DESCRIPTOR_REFERENCE_KEY


def _group(text: str, entities: list[dict[str, Any]]) -> dict[str, Any]:
    """One extraction group's result, each entity cited on the group's first sentence."""
    return {
        "raw_entities": [
            {"type": "Character", "aliases": [], "description": f"{e['name']} is here.", **e,
             "sent_ref": "S1"}
            for e in entities
        ],
        "raw_relationships": [],
        "input_text": text,
        "small_chunk_ids": [],
    }  # fmt: skip


def _aggregate(*groups: dict[str, Any]) -> list[dict[str, Any]]:
    """The groups aggregated as the finalizer does (evidence, attested and given aliases stamped)."""
    entities = orchestration.aggregate_chunk_results(list(groups))["entities"]
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


def _named(out: list[dict[str, Any]], name: str) -> dict[str, Any]:
    (found,) = [e for e in out if e["name"] == name]
    return found


# -- what a reference is ----------------------------------------------------------------------


@pytest.mark.parametrize("domain", BOTH)
@pytest.mark.parametrize(
    "label",
    ["the count", "Emperor", "the Emperor", "our Emperor", "the little princess", "the visitor",
     "Prince Andrew's wife", "the vicomte"],
)  # fmt: skip
def test_a_persons_label_that_names_no_one_is_a_reference(domain: str, label: str) -> None:
    """A bare title, a role or a descriptor of a person, under either domain."""
    assert ni.reference_key(label, domain_conventions(domain), "Character") is not None


@pytest.mark.parametrize("domain", BOTH)
@pytest.mark.parametrize(
    "label", ["Pierre", "Prince Andrew", "Emperor Alexander", "Count Ilyá Rostóv", "Lise"]
)
def test_a_name_is_no_reference(domain: str, label: str) -> None:
    """A label with a naming word names its bearer."""
    assert ni.reference_key(label, domain_conventions(domain), "Character") is None


@pytest.mark.parametrize("domain", BOTH)
def test_references_written_alike_share_one_key_and_only_a_person_is_one(domain: str) -> None:
    """``Emperor``, ``the Emperor`` and ``our Emperor`` refer alike; ``the old capital`` is a place."""
    conv = domain_conventions(domain)
    keys = {
        ni.reference_key(x, conv, "Character") for x in ("Emperor", "the Emperor", "our Emperor")
    }
    assert keys == {"emperor"}
    assert ni.reference_key("the old capital", conv, "Location") is None


# -- the referent comes from the reference's own text -----------------------------------------


@pytest.mark.parametrize(
    ("texts", "expected"),
    [
        (["“The Emperor Alexander,” said she, “has declared it.”"],
         ["Emperor Alexander"]),
        (["She expects le Vicomte de Mortemart tonight."], ["Vicomte de Mortemart"]),
        (["The Emperor Alexander and the Emperor Napoleon met."], []),
        (["He has been received by the Emperor."], []),
        (["The Emperor spoke, and the Emperor Alexander smiled."], []),
    ],
)  # fmt: skip
def test_a_bare_titles_own_sentences_name_it_only_when_they_write_its_title_as_one_name(
    texts: list[str], expected: list[str]
) -> None:
    """Two names, or the title written bare beside a name, say nothing of whom it is."""
    title = "the vicomte" if "Vicomte" in texts[0] else "the Emperor"
    assert ni.text_referent_names(title, texts, domain_conventions("literary")) == expected


def test_a_descriptor_or_a_role_reads_no_name_from_its_text() -> None:
    """``Prince Andrew's wife`` names by relation; ``the visitor`` states no title."""
    conv = domain_conventions("literary")
    text = ["The Emperor Alexander spoke to the visitor."]
    assert ni.text_referent_names("Prince Andrew's wife", text, conv) == []
    assert ni.text_referent_names("the visitor", text, conv) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", BOTH)
async def test_anonymous_references_written_alike_are_one_flagged_node_and_join_no_name(
    domain: str,
) -> None:
    """``Emperor`` and ``the Emperor`` are one; the sole named emperor is never their referent.

    Book One has two emperors (Alexander and Napoleon): a referent read from
    how often a name holds the title would put "the Emperor" of an Alexander
    chunk into Napoleon wherever Napoleon is the one named with it.
    """
    out = await _dedup(
        _aggregate(
            _group("He has been received by the Emperor.", [{"name": "Emperor"}]),
            _group("It is difficult for me to ask the Emperor.", [{"name": "the Emperor"}]),
            _group("The Emperor Napoleon crowned himself.", [{"name": "Emperor Napoleon"}]),
        ),
        domain,
    )
    names = sorted(e["name"] for e in out)
    assert len(names) == 2
    assert "Emperor Napoleon" in names
    (reference,) = [e for e in out if e["name"] != "Emperor Napoleon"]
    assert reference["properties"][FLAG] is True
    assert FLAG not in (_named(out, "Emperor Napoleon").get("properties") or {})


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", BOTH)
async def test_a_reference_its_own_sentence_names_folds_into_the_name_and_keeps_the_descriptor(
    domain: str,
) -> None:
    """The identified mention joins Emperor Alexander; the anonymous one stays a flagged node."""
    out = await _dedup(
        _aggregate(
            _group(
                "“The Emperor Alexander,” said she, “has declared it.”",
                [{"name": "the Emperor"}],
            ),
            _group("The Emperor Alexander spoke.", [{"name": "Emperor Alexander"}]),
            _group("He asked the Emperor for a place.", [{"name": "the Emperor"}]),
        ),
        domain,
    )
    alexander = _named(out, "Emperor Alexander")
    assert "the Emperor" in alexander["aliases"]
    assert "the Emperor" in alexander[ni.MERGED_NAMES_KEY]
    assert FLAG not in (alexander.get("properties") or {})
    anonymous = _named(out, "the Emperor")
    assert anonymous["properties"][FLAG] is True
    assert len(anonymous["source_chunk_indices"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", BOTH)
async def test_a_reference_its_output_gives_a_name_folds_into_it_and_the_name_keeps_the_label(
    domain: str,
) -> None:
    """``Bezúkhov's son`` given ``Pierre`` (v4 generic) is Pierre; the label stays ``Pierre``."""
    out = await _dedup(
        _aggregate(
            _group("Pierre bowed to Bezúkhov's son, Pierre.", [
                {"name": "Bezúkhov's son", "aliases": ["Pierre"]}]),
            _group("Pierre stood by the window.", [{"name": "Pierre"}]),
        ),
        domain,
    )  # fmt: skip
    (pierre,) = out
    assert pierre["name"] == "Pierre"
    assert FLAG not in (pierre.get("properties") or {})


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", BOTH)
async def test_a_reference_listed_beside_a_holder_of_its_title_is_someone_else(domain: str) -> None:
    """``the countess`` beside ``Countess Véra`` (the v4 generic output: her mother) stays apart.

    Over the v4 outputs, both references a title rung over their own output
    would have folded went to the wrong person (``Countess`` into Countess
    Apráksina, ``the countess`` into Countess Véra).
    """
    out = await _dedup(
        _aggregate(
            _group(
                "The countess looked at Countess Véra.",
                [{"name": "the countess"}, {"name": "Countess Véra", "aliases": ["Véra"]}],
            )
        ),
        domain,
    )
    assert sorted(e["name"] for e in out) == ["Countess Véra", "the countess"]
    assert _named(out, "the countess")["properties"][FLAG] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", BOTH)
async def test_the_bare_title_alias_another_chunk_gave_identifies_no_one(domain: str) -> None:
    """``the count`` given to Ilyá in one chunk names no count of another chunk (Bezúkhov's too)."""
    out = await _dedup(
        _aggregate(
            _group("“You spoil her, Ilyá,” said the countess.", [
                {"name": "Ilyá", "aliases": ["the count"]}]),
            _group("There was a place waiting for him in the Archives, said the count.", [
                {"name": "the count", "description": "A place waited in the Archives."}]),
        ),
        domain,
    )  # fmt: skip
    count = _named(out, "the count")
    assert count["properties"][FLAG] is True
    assert "Archives" in count["description"]
    assert _named(out, "Ilyá")


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", BOTH)
async def test_a_reference_giving_a_name_is_no_second_giver_of_it(domain: str) -> None:
    """``the countess`` given ``Nataly`` (v4 generic) no longer keeps Countess Rostóva from her Nataly."""
    out = await _dedup(
        _aggregate(
            _group("Countess Rostóva, called Nataly, received.", [
                {"name": "Countess Rostóva", "aliases": ["Nataly"]}]),
            _group("Nataly smiled.", [{"name": "Nataly"}]),
            _group("“Nataly,” said the countess.", [
                {"name": "the countess", "aliases": ["Nataly"]}]),
        ),
        domain,
    )  # fmt: skip
    (countess,) = out
    assert countess["name"] == "Countess Rostóva"
    assert {"Nataly", "the countess"} <= set(countess["aliases"])


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", BOTH)
async def test_one_anonymous_mentions_alias_never_carries_the_others_into_a_name(
    domain: str,
) -> None:
    """``the countess`` given ``Mamma`` (an untyped record: no referent) takes no one into Mamma.

    The v4 generic arm: an alias that identified no one when its mention was
    read became the merged anonymous group's evidence, and the later step folded
    all four ``the countess`` mentions into ``Mamma`` on it.
    """
    out = await _dedup(
        _aggregate(
            _group("Mamma laughed.", [{"name": "Mamma", "type": "UNKNOWN"}]),
            _group("“Mamma!” she cried to the countess.", [
                {"name": "the countess", "aliases": ["Mamma"]}]),
            _group("The countess sighed.", [{"name": "the countess"}]),
            _group("The countess rose.", [{"name": "the countess"}]),
        ),
        domain,
    )  # fmt: skip
    countess = _named(out, "the countess")
    assert len(countess["source_chunk_indices"]) == 3
    assert countess["properties"][FLAG] is True


def test_the_parsers_placeholder_type_never_wins_a_merge() -> None:
    """``UNKNOWN`` (an empty type column) takes the stated type; a stated one keeps it."""
    from chaoscypher_core.services.sources.engine.deduplication.service import EntityProcessor

    processor = EntityProcessor(name_conventions=domain_conventions("generic"))
    unknown = {"name": "the count", "type": "UNKNOWN", "confidence": 1.0}
    person = {"name": "the count", "type": "Person", "confidence": 0.9}
    assert processor.merge_entities(unknown, person)["type"] == "Person"
    assert processor.merge_entities(person, unknown)["type"] == "Person"


# -- a reference is never dropped while it says something --------------------------------------


@pytest.mark.parametrize("domain", BOTH)
def test_the_name_filters_keep_a_reference_that_says_something(domain: str) -> None:
    """The plausibility score and the exclusion examples keep ``the count``; a bare record goes."""
    conv = domain_conventions(domain)
    entities = [
        {"name": "the count", "type": "Character", "description": "The Archives position."},
        {"name": "the count", "type": "Character", "description": ""},
        {"name": "the vicomte", "type": "Location", "description": "No person."},
    ]
    plausible, _ = filter_implausible_entities(
        [dict(e) for e in entities], ["The count laughed."], {"Character"}, conventions=conv
    )
    # the place is scored as any place (a lower bar); the bare person record is scored and goes
    assert [e["description"] for e in plausible] == ["The Archives position.", "No person."]
    rule = ExclusionRule(description="Bare titles", examples=["The Count"])
    excluded, _ = filter_excluded_entities([dict(e) for e in entities], [rule], conventions=conv)
    assert [e["description"] for e in excluded] == ["The Archives position.", "No person."]
    assert kept_as_reference(entities[0], conv)
    # every type since 2026-10-07: a place written without a proper name is a reference too
    assert kept_as_reference(entities[2], conv)
    assert not kept_as_reference({"name": "Moscow", "type": "Location", "description": "A city."})


# -- the flag is the survivor's own -----------------------------------------------------------


def test_a_named_survivor_never_takes_the_flag_from_a_reference_it_absorbed() -> None:
    """The one property policy (the deduplication's and the resolver's) keeps the kept node's flag."""
    assert FLAG not in merge_node_properties({"title": "Count"}, {FLAG: True})
    assert merge_node_properties({FLAG: True}, {"title": "Count"})[FLAG] is True


@pytest.mark.parametrize("domain", BOTH)
def test_the_resolver_joins_anonymous_references_written_alike_and_folds_none_into_a_name(
    domain: str,
) -> None:
    """``Emperor`` and ``the Emperor`` nodes are one; neither joins ``Emperor Alexander``."""
    nodes = [
        {"id": "a", "label": "Emperor", "type": "Character", "aliases": [],
         "properties": {FLAG: True}},
        {"id": "b", "label": "the Emperor", "type": "Character", "aliases": [],
         "properties": {FLAG: True}},
        {"id": "c", "label": "Emperor Alexander", "type": "Character", "aliases": [],
         "properties": {}},
    ]  # fmt: skip
    plan = plan_resolution(nodes, [], conventions=domain_conventions(domain))
    assert [(m.keep, m.drop) for m in plan.merges] in ([("a", "b")], [("b", "a")])


@pytest.mark.parametrize("domain", BOTH)
def test_a_name_always_survives_a_reference(domain: str) -> None:
    """A bare title that gave the alias does not out-rank the name on ``own`` or ``attested``."""
    conv = domain_conventions(domain)
    group = [
        ni.SurvivorCandidate("the vicomte", given=("Vicomte de Mortemart",),
                             attested=frozenset({"the vicomte"}), support=9),
        ni.SurvivorCandidate("Vicomte de Mortemart", support=1),
    ]  # fmt: skip
    assert ni.pick_survivor(group, conv) == 1
