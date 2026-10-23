# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The resolution planner: merges on evidence, alias moves, family edges flagged for a reader."""

from __future__ import annotations

import pytest

from chaoscypher_core.services.graph.resolution import plan_resolution
from chaoscypher_core.services.graph.resolution.plan import (
    name_words,
    plan_alias_drops,
    plan_flags,
    plan_merges,
)
from tests.fakes.domain_tables import domain_conventions


LITERARY = domain_conventions("literary")
"""Book One's names, read by the literary domain's naming conventions."""


def _n(nid: str, label: str, aliases: list[str] | None = None, type_: str = "character") -> dict:
    return {"id": nid, "label": label, "type": type_, "aliases": aliases or [], "properties": {}}


def _e(eid: str, src: str, tgt: str, label: str, **props: object) -> dict:
    return {"id": eid, "source": src, "target": tgt, "label": label, "properties": props}


def test_name_words_drop_titles_and_accents() -> None:
    assert name_words("Princess Anna Mikháylovna Drubetskáya") == {
        "anna",
        "mikhaylovna",
        "drubetskaya",
    }
    assert name_words("le Vicomte de Mortemart") == {"mortemart"}  # vicomte: the floor's
    # a bare title names no one; a possessive names someone else (wave 4)
    assert name_words("the vicomte") == set()
    assert name_words("Drubetskáya's son, Vasíli\u2019s daughter") == {"daughter"}


def test_a_name_that_is_the_other_with_words_left_out_merges_into_the_fuller_one() -> None:
    """The Book One splits: Mlle Schérer, Count Bezúkhov, Princess Drubetskáya.

    The bare title "the vicomte" names no one and stays its own node (wave 4).
    """
    nodes = [
        _n("a", "Anna Pávlovna Schérer"),
        _n("b", "Mlle Schérer"),
        _n("c", "Cyril Vladímirovich Bezúkhov"),
        _n("d", "Count Bezúkhov"),
        _n("e", "le Vicomte de Mortemart"),
        _n("f", "the vicomte"),
        _n("g", "Princess Anna Mikháylovna Drubetskáya"),
        _n("h", "Princess Drubetskáya"),
    ]
    merges = {(m.keep, m.drop) for m in plan_merges(nodes, [], conventions=LITERARY)}
    assert merges == {("a", "b"), ("c", "d"), ("g", "h")}


def test_different_people_who_share_a_word_do_not_merge() -> None:
    """Mother and daughter, the three Máryas, the two Annas: shared words, distinct names."""
    nodes = [
        _n("a", "Julie Karágina"),
        _n("b", "Márya Lvóvna Karágina"),
        _n("c", "Dowager Empress Márya Fëdorovna"),
        _n("d", "Márya Ivánovna Dólokhova"),
        _n("e", "Anna Pávlovna Schérer"),
        _n("f", "Princess Anna Mikháylovna Drubetskáya"),
    ]
    assert plan_merges(nodes, [], conventions=LITERARY) == []


def test_one_nodes_name_as_the_others_alias_merges_and_the_own_name_survives() -> None:
    """``Bóry`` is a name ``Borís`` gave as an alias and gives none: the own name keeps the label.

    The one survivor ranking (``name_identity.pick_survivor``): own name,
    attested spelling, accents, the fuller name, and only then the
    better-connected node (until audit wave 6 the resolver ranked by degree
    right after naming-word count, so the nickname survived).
    """
    nodes = [_n("a", "Borís", aliases=["Bóry"]), _n("b", "Bóry")]
    edges = [_e("e1", "b", "x", "interacts_with"), _e("e2", "b", "y", "interacts_with")]
    (m,) = plan_merges(nodes, edges, conventions=LITERARY)
    assert (m.keep, m.drop) == ("a", "b")
    assert "alias" in m.why


def test_the_better_connected_node_survives_only_a_tie_on_every_name_rank() -> None:
    """Two own names equal on every name rank: the degree decides."""
    nodes = [_n("a", "Borís", aliases=["Drubetskóy"]), _n("b", "Borís", aliases=["Bóry"])]
    edges = [_e("e1", "b", "x", "interacts_with"), _e("e2", "b", "y", "interacts_with")]
    (m,) = plan_merges(nodes, edges, conventions=LITERARY)
    assert (m.keep, m.drop) == ("b", "a")


def test_a_different_type_never_merges() -> None:
    nodes = [_n("a", "Kurágin", type_="family"), _n("b", "Prince Vasíli Kurágin")]
    assert plan_merges(nodes, [], conventions=LITERARY) == []


def test_three_way_splits_merge_into_the_fullest_titled_name() -> None:
    nodes = [
        _n("a", "Princess Drubetskáya"),
        _n("b", "Princess Anna Mikháylovna Drubetskáya"),
        _n("c", "Anna Mikháylovna Drubetskáya"),
    ]
    merges = plan_merges(nodes, [], conventions=LITERARY)
    assert {m.keep for m in merges} == {"b"}  # the same name words as c, with the title kept
    assert {m.drop for m in merges} == {"a", "c"}


def test_an_alias_that_is_another_nodes_name_comes_off_but_a_shared_surname_stays() -> None:
    nodes = [
        _n("a", "Anna Pávlovna Schérer", aliases=["Annette", "Anna Mikháylovna"]),
        _n("b", "Princess Anna Mikháylovna Drubetskáya", aliases=["Anna Mikháylovna"]),
        _n("c", "Anatole", aliases=["Kurágin"]),
        _n("d", "Prince Vasíli Kurágin"),
    ]
    drops = plan_alias_drops(nodes, conventions=LITERARY)
    assert [(d.node, d.alias, d.owner) for d in drops] == [("a", "Anna Mikháylovna", "b")]


def test_family_edges_are_flagged_from_their_justification_never_deleted() -> None:
    nodes = [
        _n("n", "Nicholas"),
        _n("p", "Pierre"),
        _n("v", "Prince Vasíli Kurágin"),
        _n("b", "Borís"),
        _n("m", "Márya Ivánovna Dólokhova"),
        _n("d", "Dólokhov"),
    ]
    sent = "Nicholas is the count's eldest son and Pétya is his youngest boy."
    edges = [
        # names Pétya, whom the graph lacks, and never Pierre: the endpoint is in doubt
        _e("e1", "n", "p", "sibling_of", justification=sent),
        # names both ends: left alone, whatever a reader may make of it
        _e("e2", "v", "b", "parent_of", justification="Prince Vasíli is the father of Borís."),
        # a pronoun for one end and the other named, no one else: left alone
        _e("e3", "m", "d", "parent_of", justification="She is the mother of Dólokhov."),
        _e("e4", "n", "p", "interacts_with", justification=sent),  # not a family edge
    ]
    flags = plan_flags(nodes, edges, conventions=LITERARY)
    assert [f.edge for f in flags] == ["e1"]
    assert "Pétya" in flags[0].why and "no node" in flags[0].why and "Pierre" in flags[0].why
    (flag,) = plan_flags(
        [*nodes, _n("y", "Pétya")], edges, conventions=LITERARY
    )  # with a Pétya node it is named as one
    assert "names Pétya and not Pierre" in flag.why
    plan = plan_resolution(nodes, edges, conventions=LITERARY)
    assert [f.edge for f in plan.flags] == ["e1"] and not plan.merges and not plan.alias_drops


def test_an_empty_graph_plans_nothing() -> None:
    assert plan_resolution([], []).empty


def test_an_alias_drop_reads_identity_words_and_only_the_given_aliases() -> None:
    """A feminine surname form names its family's node; an alias a merge brought is never dropped.

    "Natálya Rostóva" on the hostess names the one node "Countess Natálya
    Rostóv" (the surname fold); "Anna Mikháylovna" acquired by a merge (not in
    ``aliases_given``) is the merged-in name's and stays.
    """
    from chaoscypher_core.services.graph.resolution.plan import plan_alias_drops

    nodes = [
        {
            "id": "h",
            "label": "Anna Pávlovna Schérer",
            "type": "character",
            "aliases": ["Natálya Rostóva", "Anna Mikháylovna"],
            "aliases_given": ["Natálya Rostóva"],
            "properties": {},
        },
        _n("r", "Countess Natálya Rostóv"),
        _n("d", "Princess Anna Mikháylovna Drubetskáya"),
    ]
    drops = {(d.node, d.alias, d.owner) for d in plan_alias_drops(nodes, conventions=LITERARY)}
    assert drops == {("h", "Natálya Rostóva", "r")}


def test_a_scoped_pass_drops_aliases_from_its_own_nodes_only() -> None:
    """Audit wave 5: an older source's node keeps the alias it was given, whoever it names."""
    from chaoscypher_core.services.graph.resolution.plan import plan_resolution

    nodes = [
        # an older source's hostess, given the princess's name by mistake
        {"id": "old", "label": "Anna Pávlovna Schérer", "type": "Character",
         "aliases": ["Anna Mikháylovna"], "properties": {}},
        # the new source's princess and a new node with the same stray alias
        {"id": "new", "label": "Princess Anna Mikháylovna Drubetskáya", "type": "Character",
         "aliases": [], "properties": {}},
        {"id": "new2", "label": "Mlle Schérer", "type": "Character",
         "aliases": ["Anna Mikháylovna"], "properties": {}},
    ]  # fmt: skip
    whole = plan_resolution(nodes, [], titles={"mlle", "princess"})
    assert {(d.node, d.owner) for d in whole.alias_drops} == {("old", "new"), ("new2", "new")}
    scoped = plan_resolution(nodes, [], titles={"mlle", "princess"}, scope=["new", "new2"])
    assert {(d.node, d.owner) for d in scoped.alias_drops} == {("new2", "new")}


# -- audit wave 7: a tie never falls to the node id --------------------------------------------


@pytest.mark.parametrize("ids", [("n1", "n2"), ("n2", "n1"), ("zz", "aa")])
def test_a_tie_falls_to_the_label_never_to_the_node_id(ids: tuple[str, str]) -> None:
    """Two ranks equal on every step (no edges, same length, no accents): the label decides.

    The id depends on the database a graph was built in, so until wave 7 one
    graph kept "Captain Tushin" in one database and "Colonel Tushin" in another.
    """
    a, b = ids
    nodes = [_n(a, "Captain Tushin"), _n(b, "Colonel Tushin")]
    merges = plan_merges(nodes, [])
    assert len(merges) == 1
    kept = {a: "Captain Tushin", b: "Colonel Tushin"}[merges[0].keep]
    assert kept == "Colonel Tushin"
