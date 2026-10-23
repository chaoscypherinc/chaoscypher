# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The name rule's holder, gender and alias-key guards, through both real callers (audit W2-1).

Every case runs through the pre-commit deduplication (``run_deduplication``
with the literary domain: exact dedup, its alias keys, the hierarchical
pass) and through the post-commit resolver (``plan_merges`` under the same
domain's naming conventions), and both must give the expected groups. The
agreement test then runs every case under every registered domain: whatever
a domain's tables make of a case, the two passes make the same.

Every table the rule reads (title words, gendered titles, generation words,
Russian name forms) is the literary domain's ``.jsonld``, read through
``domain_name_conventions``: none of them lives in core.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from chaoscypher_core.services.graph.resolution.plan import plan_merges
from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    DEFAULT_NAME_CONVENTIONS,
    GIVEN_ALIASES_KEY,
    NameConventions,
    NameRecord,
    identity_word,
    plan_name_pairs,
)
from chaoscypher_core.services.sources.engine.extraction.domain_resolver import (
    DomainResolver,
    domain_name_conventions,
    domain_title_words,
)
from chaoscypher_core.services.sources.engine.extraction.domains import get_domain_registry
from chaoscypher_core.services.sources.engine.extraction.extractor import run_deduplication
from chaoscypher_core.settings import EngineSettings
from tests.fakes.domain_tables import domain_conventions


Spec = list[tuple[str, list[str]]]
"""Entities as ``(name, given aliases)``, all of type Character."""

CASES: dict[str, tuple[Spec, list[list[str]]]] = {
    "Mlle Schérer is the hostess": (
        [("Mlle Schérer", []), ("Anna Pávlovna Schérer", [])],
        [["Mlle Schérer", "Anna Pávlovna Schérer"]],
    ),
    # A bare title names no one (wave 4): "the vicomte" is not folded into the one vicomte
    # the graph names, as "the abbé" is not Abbé Morio; the prompt asks for the name.
    "a bare title is not Mortemart": (
        [("the vicomte", []), ("le Vicomte de Mortemart", [])],
        [["the vicomte"], ["le Vicomte de Mortemart"]],
    ),
    "Princess Drubetskáya is Anna Mikháylovna, not her son": (
        [
            ("Princess Drubetskáya", []),
            ("Princess Anna Mikháylovna Drubetskáya", []),
            ("Borís Drubetskóy", []),
        ],
        [["Princess Drubetskáya", "Princess Anna Mikháylovna Drubetskáya"], ["Borís Drubetskóy"]],
    ),
    "Count Bezúkhov is Cyril when no other Bezúkhov": (
        [("Count Bezúkhov", []), ("Cyril Vladímirovich Bezúkhov", [])],
        [["Count Bezúkhov", "Cyril Vladímirovich Bezúkhov"]],
    ),
    "Count Bezúkhov beside Pierre is withheld": (
        [("Count Bezúkhov", []), ("Cyril Vladímirovich Bezúkhov", []), ("Pierre Bezúkhov", [])],
        [["Count Bezúkhov"], ["Cyril Vladímirovich Bezúkhov"], ["Pierre Bezúkhov"]],
    ),
    "Count Rostóv is not his son": (
        [("Count Rostóv", []), ("Nicholas Rostóv", []), ("Natásha Rostóva", [])],
        [["Count Rostóv"], ["Nicholas Rostóv"], ["Natásha Rostóva"]],
    ),
    "Countess Rostóva is not her daughter": (
        [("Countess Rostóva", []), ("Natásha Rostóva", []), ("Nicholas Rostóv", [])],
        [["Countess Rostóva"], ["Natásha Rostóva"], ["Nicholas Rostóv"]],
    ),
    "Count and Countess Rostóv are two people": (
        [("Count Rostóv", []), ("Countess Rostóva", [])],
        [["Count Rostóv"], ["Countess Rostóva"]],
    ),
    "old and young Bolkónski are father and son": (
        [("old Prince Bolkónski", []), ("young Bolkónski", []), ("Prince Andrew Bolkónski", [])],
        [["old Prince Bolkónski"], ["young Bolkónski"], ["Prince Andrew Bolkónski"]],
    ),
    "old and young Bolkónski alone": (
        [("old Prince Bolkónski", []), ("young Bolkónski", [])],
        [["old Prince Bolkónski"], ["young Bolkónski"]],
    ),
    "Prince Bolkónski could be the old prince": (
        [("Prince Bolkónski", []), ("old Prince Bolkónski", []), ("Prince Andrew Bolkónski", [])],
        [["Prince Bolkónski"], ["old Prince Bolkónski"], ["Prince Andrew Bolkónski"]],
    ),
    "the King and the Queen of Prussia": (
        [("King of Prussia", []), ("Queen of Prussia", [])],
        [["King of Prussia"], ["Queen of Prussia"]],
    ),
    "the Emperor and the Empress of Austria": (
        [("Emperor of Austria", []), ("Empress of Austria", [])],
        [["Emperor of Austria"], ["Empress of Austria"]],
    ),
    "the little princess is not Princess Mary": (
        [("Princess Bolkónskaya", ["Lise"]), ("Princess Mary Bolkónskaya", [])],
        [["Princess Bolkónskaya"], ["Princess Mary Bolkónskaya"]],
    ),
    "Princess Mary's alias does not make her the little princess": (
        [("Princess Bolkónskaya", ["Lise"]), ("Princess Mary", ["Princess Bolkónskaya"])],
        [["Princess Bolkónskaya"], ["Princess Mary"]],
    ),
    "a surname two people were given keys neither": (
        [("Kurágin", []), ("Anatole", ["Kurágin"]), ("Vasíli", ["Kurágin"])],
        [["Kurágin"], ["Anatole"], ["Vasíli"]],
    ),
    "a surname a fuller name holds keys no one": (
        [("Kurágin", []), ("Anatole", ["Kurágin"]), ("Prince Vasíli Kurágin", [])],
        [["Kurágin"], ["Anatole"], ["Prince Vasíli Kurágin"]],
    ),
    "the prince is no one in particular": (
        [
            ("the prince", []),
            ("Prince Vasíli", ["the prince"]),
            ("Prince Andrew", ["the prince"]),
            ("Prince Hippolyte", []),
        ],
        [["the prince"], ["Prince Vasíli"], ["Prince Andrew"], ["Prince Hippolyte"]],
    ),
    "the son is not the mother": (
        [("Dólokhov", []), ("Márya Ivánovna Dólokhova", [])],
        [["Dólokhov"], ["Márya Ivánovna Dólokhova"]],
    ),
    "a diminutive and two short forms join the one full name": (
        [("Borís", ["Bóry"]), ("Bóry", []), ("Borís Drubetskóy", [])],
        [["Borís", "Bóry", "Borís Drubetskóy"]],
    ),
    "Andrew, Prince Andrew and the full name": (
        [("Andrew", []), ("Prince Andrew", []), ("Prince Andrew Bolkónski", [])],
        [["Andrew", "Prince Andrew", "Prince Andrew Bolkónski"]],
    ),
    # Descriptors (audit wave 3): a name for someone's relation never names that someone.
    "Natásha's mother is not Natásha": (
        [
            ("Countess Rostóva", ["Natásha's mother"]),
            ("Natásha Rostóva", []),
            ("Nicholas Rostóv", []),
        ],
        [["Countess Rostóva"], ["Natásha Rostóva"], ["Nicholas Rostóv"]],
    ),
    "Natásha's mother is not Natásha with no one else of the family": (
        [("Countess Rostóva", ["Natásha's mother"]), ("Natásha Rostóva", [])],
        [["Countess Rostóva"], ["Natásha Rostóva"]],
    ),
    "Pierre's father is not Pierre": (
        [("Pierre's father", []), ("Pierre", [])],
        [["Pierre's father"], ["Pierre"]],
    ),
    "the count's valet is not the count": (
        [("Count Rostóv's valet", []), ("Count Rostóv", [])],
        [["Count Rostóv's valet"], ["Count Rostóv"]],
    ),
    "Bezúkhov's son holds no Bezúkhov name": (
        [
            ("Count Bezúkhov", []),
            ("Cyril Vladímirovich Bezúkhov", []),
            ("Pierre", ["Bezúkhov\u2019s son"]),
        ],
        [["Count Bezúkhov", "Cyril Vladímirovich Bezúkhov"], ["Pierre"]],
    ),
    "the father of Pierre is not Pierre": (
        [("the father of Pierre", []), ("Pierre", [])],
        [["the father of Pierre"], ["Pierre"]],
    ),
    # The same-words step reads gender and generation as the subset step does.
    "the old countess is not her namesake": (
        [("old Countess Natálya Rostóva", []), ("Natálya Rostóva", [])],
        [["old Countess Natálya Rostóva"], ["Natálya Rostóva"]],
    ),
    "a countess named by her husband's name is not him": (
        [("Countess Bezúkhov", []), ("Bezúkhov", [])],
        [["Countess Bezúkhov"], ["Bezúkhov"]],
    ),
    "Mrs John Smith is not John Smith": (
        [("Mrs John Smith", []), ("John Smith", [])],
        [["Mrs John Smith"], ["John Smith"]],
    ),
    "Mrs and Miss Mary Smith are two women": (
        [("Mrs Mary Smith", []), ("Miss Mary Smith", [])],
        [["Mrs Mary Smith"], ["Miss Mary Smith"]],
    ),
    "one title's two spellings are one title": (
        [("Mme Schérer", []), ("Madame Schérer", [])],
        [["Mme Schérer", "Madame Schérer"]],
    ),
    # A title is compatible with a fuller name that states no gender.
    "Prince Vasíli is Vasíli Kurágin": (
        [("Prince Vasíli", []), ("Vasíli Kurágin", [])],
        [["Prince Vasíli", "Vasíli Kurágin"]],
    ),
    "Prince Hippolyte is Hippolyte Kurágin": (
        [("Prince Hippolyte", []), ("Hippolyte Kurágin", [])],
        [["Prince Hippolyte", "Hippolyte Kurágin"]],
    ),
    "Count Pierre is Pierre Bezúkhov": (
        [("Count Pierre", []), ("Pierre Bezúkhov", [])],
        [["Count Pierre", "Pierre Bezúkhov"]],
    ),
    "Mr Darcy is Fitzwilliam Darcy": (
        [("Mr Darcy", []), ("Fitzwilliam Darcy", [])],
        [["Mr Darcy", "Fitzwilliam Darcy"]],
    ),
    "Sir Walter is Walter Elliot": (
        [("Sir Walter", []), ("Walter Elliot", [])],
        [["Sir Walter", "Walter Elliot"]],
    ),
    "Princess Drubetskáya is the one Drubetskáya": (
        [("Princess Drubetskáya", []), ("Anna Mikháylovna Drubetskáya", [])],
        [["Princess Drubetskáya", "Anna Mikháylovna Drubetskáya"]],
    ),
    "the mother holds no son's name": (
        [("Dólokhov", []), ("Fédya Dólokhov", []), ("Márya Ivánovna Dólokhova", [])],
        [["Dólokhov", "Fédya Dólokhov"], ["Márya Ivánovna Dólokhova"]],
    ),
}
"""Book One's names, each with the groups the rule must give under the literary domain."""


def _canon(groups: list[list[str]]) -> set[frozenset[str]]:
    """Groups as a comparable set."""
    return {frozenset(g) for g in groups}


async def _dedup_groups(spec: Spec, domain: str) -> set[frozenset[str]]:
    """The groups ``run_deduplication`` (the product's own domain resolver) makes of ``spec``."""
    entities = [
        {"name": n, "type": "Character", "aliases": list(a), "source_chunk_indices": [i]}
        for i, (n, a) in enumerate(spec)
    ]
    out, _rels, _emb, _log = await run_deduplication(
        entities=entities,
        relationships=[],
        detected_domain=domain,
        settings=EngineSettings(),
        embedding_service=None,
    )
    # a merge unions the members' chunk indices: each survivor's indices are its members
    return {frozenset(spec[i][0] for i in e["source_chunk_indices"]) for e in out}


def _resolver_groups(spec: Spec, domain: str) -> set[frozenset[str]]:
    """The groups ``plan_merges`` makes of ``spec`` as committed nodes, under the domain's conventions."""
    conventions = domain_name_conventions(domain, DomainResolver(EngineSettings()))
    nodes: list[dict[str, Any]] = [
        {"id": f"n{i}", "label": n, "type": "Character", "aliases": list(a), "properties": {}}
        for i, (n, a) in enumerate(spec)
    ]
    label = {node["id"]: node["label"] for node in nodes}
    groups = {node["id"]: {node["label"]} for node in nodes}
    for m in plan_merges(nodes, [], conventions=conventions):
        groups[m.keep] |= groups.pop(m.drop, {label[m.drop]})
    return {frozenset(g) for g in groups.values()}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", list(CASES))
async def test_the_deduplication_gives_the_books_groups(case: str) -> None:
    """``run_deduplication`` under the literary domain."""
    spec, expected = CASES[case]
    assert await _dedup_groups(spec, "literary") == _canon(expected)


@pytest.mark.parametrize("case", list(CASES))
def test_the_resolver_gives_the_books_groups(case: str) -> None:
    """``plan_merges`` under the literary domain's naming conventions."""
    spec, expected = CASES[case]
    assert _resolver_groups(spec, "literary") == _canon(expected)


def _registered_domains() -> list[str]:
    """Every domain the registry knows."""
    registry = get_domain_registry(EngineSettings())
    return sorted(str(d["name"]) for d in registry.list_domain_info())


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", _registered_domains())
async def test_the_two_passes_agree_under_every_domain(domain: str) -> None:
    """Whatever a domain's title words make of a case, the dedup and the resolver make the same."""
    for case, (spec, _expected) in CASES.items():
        assert await _dedup_groups(spec, domain) == _resolver_groups(spec, domain), case


def test_the_domain_title_words_are_one_lookup() -> None:
    """A domain's own words over the universal floor; the floor alone without a domain or for an unknown one."""
    resolver = DomainResolver(EngineSettings())
    literary = domain_title_words("literary", resolver)
    assert {"princess", "mlle", "of", "dowager"} <= literary
    assert domain_title_words(None, resolver) == domain_title_words("no-such-domain", resolver)
    # the floor (audit wave 7): the language-level titles every domain starts from
    assert {"princess", "monsieur", "count"} <= domain_title_words(None, resolver)
    assert "dowager" not in domain_title_words(None, resolver)


def test_feminine_surname_forms_fold_to_one_family_stem() -> None:
    """The fold the holder count reads: the literary domain's Russian ``name_forms``."""
    ru = domain_conventions("literary")
    assert identity_word("rostova", ru) == identity_word("rostov", ru) == "rostov"
    assert identity_word("bolkonskaya", ru) == identity_word("bolkonski", ru)
    assert identity_word("drubetskaya", ru) == identity_word("drubetskoy", ru)
    assert identity_word("nina", ru) == "nina"  # too short to be a form
    # without a domain's forms no ending is a form
    assert identity_word("rostova") == "rostova"


def test_the_literary_domain_supplies_every_table() -> None:
    """Each table the rule reads is declared by the domain, none taken from the fallback."""
    literary = domain_conventions("literary")
    assert {"princess", "mlle", "of"} <= literary.titles
    assert {"countess", "queen", "mlle"} <= literary.feminine_titles
    assert {"count", "king", "prince"} <= literary.masculine_titles
    assert literary.generation["old"] == "elder" and literary.generation["young"] == "younger"
    assert [lang for lang, _ in literary.name_forms] == ["ru"]
    assert literary.feminine_titles != DEFAULT_NAME_CONVENTIONS.feminine_titles
    assert literary.masculine_titles != DEFAULT_NAME_CONVENTIONS.masculine_titles
    assert literary.generation_words != DEFAULT_NAME_CONVENTIONS.generation_words
    assert not DEFAULT_NAME_CONVENTIONS.name_forms  # core ships no language's forms


def _bare_domain_resolver() -> Any:
    """A resolver whose one pack, ``bare``, declares no naming table at all (on the real floor)."""
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        NameConventions,
    )
    from chaoscypher_core.services.sources.engine.extraction.domain_resolver import (
        name_tables_of_domain,
    )
    from chaoscypher_core.services.sources.engine.extraction.domains import get_domain_registry
    from chaoscypher_core.services.sources.engine.extraction.domains.configurable import (
        ConfigurableDomain,
    )
    from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack

    bare = ConfigurableDomain({"name": "bare"})
    assert NameConventions.build(name_tables_of_domain(bare)) == DEFAULT_NAME_CONVENTIONS
    floor = DomainStack.resolve(get_domain_registry(EngineSettings()), [])
    resolver = DomainResolver(EngineSettings())
    resolver._stacks["bare"] = DomainStack(floor.floor, (bare,))
    return resolver


@pytest.mark.asyncio
async def test_a_domain_that_declares_no_tables_runs_the_rule_on_the_universal_ones() -> None:
    """No title words, gendered titles, generations or forms: both passes run, on the floor's.

    The floor declares only its own title words (universal ones) and the
    Person type, so a pack declaring nothing reads names as the universal
    tables do, but for which types name a person.
    """
    from chaoscypher_core.services.sources.engine.extraction.domains import get_domain_registry
    from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack

    resolver = _bare_domain_resolver()
    floor = DomainStack.resolve(get_domain_registry(EngineSettings()), []).conventions()
    assert domain_name_conventions("bare", resolver) == floor
    assert floor == dataclasses.replace(
        DEFAULT_NAME_CONVENTIONS, person_types=frozenset({"person"})
    )
    spec: Spec = [("Mr Smith", []), ("Mr John Smith", []), ("Mrs Smith", []), ("Rostova", [])]
    expected = _canon([["Mr Smith", "Mr John Smith"], ["Mrs Smith"], ["Rostova"]])
    entities = [
        {"name": n, "type": "Character", "aliases": list(a), "source_chunk_indices": [i]}
        for i, (n, a) in enumerate(spec)
    ]
    out, _rels, _emb, _log = await run_deduplication(
        entities=entities,
        relationships=[],
        detected_domain="bare",
        settings=EngineSettings(),
        embedding_service=None,
        domain_resolver=resolver,
    )
    # the universal gendered titles keep Mrs Smith apart; no language's forms apply
    assert {frozenset(spec[i][0] for i in e["source_chunk_indices"]) for e in out} == expected
    nodes: list[dict[str, Any]] = [
        {"id": f"n{i}", "label": n, "type": "Character", "aliases": list(a), "properties": {}}
        for i, (n, a) in enumerate(spec)
    ]
    groups = {node["id"]: {node["label"]} for node in nodes}
    for m in plan_merges(nodes, [], conventions=domain_name_conventions("bare", resolver)):
        groups[m.keep] |= groups.pop(m.drop)
    assert {frozenset(g) for g in groups.values()} == expected


def test_a_domain_table_moves_the_verdict() -> None:
    """The verdict follows the domain's tables, not a list in core: drop a gendered title, it merges."""
    literary = domain_conventions("literary")
    records = [NameRecord("King of Prussia", "C"), NameRecord("Queen of Prussia", "C")]
    assert plan_name_pairs(records, conventions=literary) == []
    ungendered = NameConventions(
        titles=literary.titles,
        feminine_titles=literary.feminine_titles - {"queen"},
        masculine_titles=literary.masculine_titles - {"king"},
        generation_words=literary.generation_words,
        name_forms=literary.name_forms,
    )
    assert [(i, j) for i, j, _ in plan_name_pairs(records, conventions=ungendered)] == [(0, 1)]


def test_every_gendered_title_a_domain_drops_says_its_gender() -> None:
    """A gendered word in a domain's ``title_words`` is in that domain's gendered tables.

    Otherwise the word is dropped from the name and nothing says the gender,
    and ``King of Prussia`` joins ``Queen of Prussia`` under that domain.
    """
    gendered: set[str] = set()
    for name in _registered_domains():
        conv = domain_conventions(name)
        gendered |= conv.feminine_titles | conv.masculine_titles
    for name in _registered_domains():
        conv = domain_conventions(name)
        dropped = {t for t in conv.titles if t in gendered}
        missing = dropped - conv.feminine_titles - conv.masculine_titles
        assert not missing, (name, sorted(missing))


def test_a_join_never_puts_two_people_the_rule_keeps_apart_in_one_group() -> None:
    """A chain of evidence stops before it would join a man and a woman."""
    records = [
        NameRecord("Prince Bolkónski", "C", ("Bolkónski",)),
        NameRecord("Bolkónski", "C", ("Princess Bolkónskaya",)),
        NameRecord("Princess Bolkónskaya", "C"),
    ]
    parent = list(range(3))
    for i, j, _ev in plan_name_pairs(records, conventions=domain_conventions("literary")):
        parent[j] = parent[i]
    assert len({parent[0], parent[2]}) == 2


@pytest.mark.asyncio
async def test_the_given_aliases_leave_the_dedup_and_only_they_are_resolver_evidence() -> None:
    """A merge's acquired alias is never evidence, before or after the commit.

    "Mlle Schérer" (given "Anna Pávlovna Schérer" and "Annette Schérer")
    folds into Anna Pávlovna; her committed node keeps ``aliases_given``
    without "Annette Schérer", so the resolver never joins her to a later
    node called that on the strength of an alias a merge brought.
    """
    entities = [
        {"name": "Anna Pávlovna Schérer", "type": "Character", "aliases": ["Annette"]},
        {
            "name": "Mlle Schérer",
            "type": "Character",
            "aliases": ["Anna Pávlovna Schérer", "Annette Schérer"],
        },
    ]
    out, _r, _e, _l = await run_deduplication(
        entities=entities,
        relationships=[],
        detected_domain="literary",
        settings=EngineSettings(),
        embedding_service=None,
    )
    (hostess,) = out
    assert "Annette Schérer" in hostess["aliases"]
    assert hostess[GIVEN_ALIASES_KEY] == ["Annette"]
    nodes = [
        {
            "id": "a",
            "label": hostess["name"],
            "type": "Character",
            "aliases": hostess["aliases"],
            GIVEN_ALIASES_KEY: hostess[GIVEN_ALIASES_KEY],
            "properties": {},
        },
        {
            "id": "b",
            "label": "Annette Schérer",
            "type": "Character",
            "aliases": [],
            "properties": {},
        },
    ]
    literary = domain_conventions("literary")
    assert plan_merges(nodes, [], conventions=literary) == []
    nodes[0].pop(GIVEN_ALIASES_KEY)  # a node no merge touched: every alias is given
    assert [(m.keep, m.drop) for m in plan_merges(nodes, [], conventions=literary)] == [("a", "b")]


# --- Audit wave 4: each case under both arms of the domain A/B, through both callers.

BOTH = ("literary", "generic")

WAVE4_CASES: dict[str, tuple[Spec, list[list[str]]]] = {
    # W4-2: a woman's title on given names joins the bare given name, as a man's does,
    # where the names show the word to be a woman's given name (wave 5).
    "Princess Mary is Mary": (
        [("Princess Mary", []), ("Mary", []), ("Princess Mary Bolkónskaya", [])],
        [["Princess Mary", "Mary", "Princess Mary Bolkónskaya"]],
    ),
    "Countess Natásha is Natásha": (
        [("Countess Natásha", []), ("Natásha", []), ("Countess Natásha Rostóva", [])],
        [["Countess Natásha", "Natásha", "Countess Natásha Rostóva"]],
    ),
    "Princess Hélène is Hélène": (
        [("Princess Hélène", []), ("Hélène", []), ("Princess Hélène Kurágina", [])],
        [["Princess Hélène", "Hélène", "Princess Hélène Kurágina"]],
    ),
    "Countess Véra is Véra": (
        [("Countess Véra", []), ("Véra", []), ("Countess Véra Rostóva", [])],
        [["Countess Véra", "Véra", "Countess Véra Rostóva"]],
    ),
    "Lady Catherine is Catherine": (
        [("Lady Catherine", []), ("Catherine", []), ("Lady Catherine de Bourgh", [])],
        [["Lady Catherine", "Catherine", "Lady Catherine de Bourgh"]],
    ),
    "Princess Mary, Mary and the full name": (
        [("Princess Mary", []), ("Mary", []), ("Princess Mary Bolkónskaya", [])],
        [["Princess Mary", "Mary", "Princess Mary Bolkónskaya"]],
    ),
    # ... and the husband's-name rule still holds on family names.
    "Countess Rostóva is not Count Rostóv": (
        [("Countess Rostóva", []), ("Count Rostóv", [])],
        [["Countess Rostóva"], ["Count Rostóv"]],
    ),
    "Mrs John Smith is not John Smith (both arms)": (
        [("Mrs John Smith", []), ("John Smith", [])],
        [["Mrs John Smith"], ["John Smith"]],
    ),
    "Mrs and Miss Mary Smith are two women (both arms)": (
        [("Mrs Mary Smith", []), ("Miss Mary Smith", [])],
        [["Mrs Mary Smith"], ["Miss Mary Smith"]],
    ),
    # W4-6: a possessive names someone else, whatever word follows it.
    "Kutúzov's envoy is not Kutúzov": (
        [("Kutúzov's envoy", []), ("General Kutúzov", [])],
        [["Kutúzov's envoy"], ["General Kutúzov"]],
    ),
    "Prince Bolkónski's architect is not the prince": (
        [("Prince Bolkónski's architect", []), ("Prince Bolkónski", [])],
        [["Prince Bolkónski's architect"], ["Prince Bolkónski"]],
    ),
    "Count Rostóv's huntsman is not the count": (
        [("Count Rostóv's huntsman", []), ("Count Rostóv", [])],
        [["Count Rostóv's huntsman"], ["Count Rostóv"]],
    ),
    "an envoy given as an alias is no key": (
        [("Kutúzov", ["Kutúzov's envoy"]), ("Kutúzov's envoy", [])],
        [["Kutúzov"], ["Kutúzov's envoy"]],
    ),
    "the orderly of a general is a universal role": (
        [("the orderly of General Kutúzov", []), ("General Kutúzov", [])],
        [["the orderly of General Kutúzov"], ["General Kutúzov"]],
    ),
    # A bare title or role names no one, under every domain's tables (the q131 trap).
    "the abbé is not Abbé Morio": (
        [("the abbé", []), ("Abbé Morio", [])],
        [["the abbé"], ["Abbé Morio"]],
    ),
    "the abbé given as an alias is no key": (
        [("Abbé Morio", ["the abbé"]), ("the abbé", [])],
        [["Abbé Morio"], ["the abbé"]],
    ),
    # The fold: a hyphenated given name is one word; any script's letters are words.
    "Anne-Marie Dupont holds no Marie Dupont": (
        [("Anne-Marie Dupont", []), ("Marie Dupont", [])],
        [["Anne-Marie Dupont"], ["Marie Dupont"]],
    ),
    "a Cyrillic name is a name": (
        [("Наташа Ростова", []), ("Наташа", [])],
        [["Наташа Ростова", "Наташа"]],
    ),
    "a stroked letter does not split a word": (
        [("Bjørn Borg", []), ("Bj Borg", [])],
        [["Bjørn Borg"], ["Bj Borg"]],
    ),
}
"""Wave-4 cases with the groups both arms of the domain A/B must give (no genre table decides them)."""


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", BOTH)
@pytest.mark.parametrize("case", list(WAVE4_CASES))
async def test_the_wave4_cases_through_the_deduplication(case: str, domain: str) -> None:
    """``run_deduplication`` under each arm's domain."""
    spec, expected = WAVE4_CASES[case]
    assert await _dedup_groups(spec, domain) == _canon(expected)


@pytest.mark.parametrize("domain", BOTH)
@pytest.mark.parametrize("case", list(WAVE4_CASES))
def test_the_wave4_cases_through_the_resolver(case: str, domain: str) -> None:
    """``plan_merges`` under each arm's naming conventions."""
    spec, expected = WAVE4_CASES[case]
    assert _resolver_groups(spec, domain) == _canon(expected)
