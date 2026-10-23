# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A flagged reference node (``descriptor_reference``) seeds by its written label or as the closest match (2026-10-07).

The descriptor fold keeps a reference record (``the count``, ``Prince
Vasíli's son``, ``the little princess``) as a node flagged
``properties.descriptor_reference``. On the B0 literary pack the vector seed
match seeded them on 84 of 120 questions ("the count" on 31, "Prince Vasíli's
son" on 25): the label and description of ``Prince Vasíli's son`` embed close
to every question about Prince Vasíli, and an anonymous reference written alike
across chunks is one well-connected node that PageRank then ranks over the
named person asked about. The rule (``graphrag_handlers._seedable``): a
reference never matches by name; it seeds when the question writes its whole
label, after every name match, or by similarity when no named candidate is
closer (``_similarity_seeds``: "Princess Mary's father" is ``Prince Andrew's
father``, whose description alone held the answer to B0 q088, lost on four
models while references never seeded by similarity); the graph walk still
reaches it, and it ranks after the named entities.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    DESCRIPTOR_REFERENCE_KEY,
    is_reference_record,
)
from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
    GraphRAGToolHandlers,
    _provenance_lookup_ids,
    _seedable,
    _similarity_seeds,
)
from chaoscypher_core.settings import EngineSettings
from tests.fakes.domain_tables import domain_conventions
from tests.fakes.graph_edges import EdgesByNode


LITERARY = domain_conventions("literary")


def _node(nid: str, label: str, *, reference: bool = False, **props: Any) -> SimpleNamespace:
    properties = {**props, DESCRIPTOR_REFERENCE_KEY: True} if reference else dict(props)
    return SimpleNamespace(
        id=nid, label=label, template_id="person", entity_type="Character", properties=properties
    )


def _edge(src: str, label: str, tgt: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"{src}-{label}-{tgt}", source_node_id=src, label=label, target_node_id=tgt
    )


NODES = {
    "vasili": _node("vasili", "Prince Vasíli Kurágin", definition="A courtier, Hélène's father."),
    "son": _node(
        "son", "Prince Vasíli\u2019s son", reference=True, definition="The son of Prince Vasíli."
    ),
    "helene": _node("helene", "Princess Hélène", definition="Prince Vasíli's daughter."),
    "count": _node(
        "count", "the count", reference=True, aliases=["Papa"], definition="A host in Moscow."
    ),
    "natasha": _node("natasha", "Natásha", definition="The count's daughter."),
    "little": _node("little", "the little princess", reference=True, definition="Andrew's wife."),
    "princess": _node("princess", "the princess", reference=True, definition="A princess."),
    "andrew": _node("andrew", "Prince Andrew", definition="Lise's husband."),
    "father": _node(
        "father",
        "Prince Andrew's father",
        reference=True,
        definition="The father of Prince Andrew, who calls women selfish, vain and stupid.",
    ),
    "bolkonski": _node(
        "bolkonski", "Prince Bolkónski", definition="Nicknamed the King of Prussia."
    ),
    "kutuzov": _node("kutuzov", "General Kutúzov", definition="A general."),
    "lise": _node("lise", "Lise Meinen", definition="Prince Andrew's wife."),
    # an old graph's bare title, from before the flag: unchanged
    "oldcount": _node("oldcount", "the old count", definition="A host."),
}
EDGES = [
    _edge("son", "child_of", "vasili"),
    _edge("helene", "child_of", "vasili"),
    _edge("count", "parent_of", "natasha"),
    _edge("little", "spouse_of", "andrew"),
]


class _Graph(EdgesByNode):
    def list_edges(self, **kw: Any) -> list[Any]:
        if "source_node_id" in kw:
            return [e for e in EDGES if e.source_node_id == kw["source_node_id"]]
        return [e for e in EDGES if e.target_node_id == kw["target_node_id"]]

    def get_nodes_batch(self, ids: list[str], **_: Any) -> list[Any]:
        return [NODES[i] for i in ids if i in NODES]


def _handler(search: Any = None, **graphrag: Any) -> GraphRAGToolHandlers:
    settings = EngineSettings()
    for k, v in graphrag.items():
        setattr(settings.graphrag, k, v)
    h = GraphRAGToolHandlers(
        graph_repository=_Graph(),
        search_repository=search or MagicMock(),
        indexing_repository=MagicMock(),
        source_storage=MagicMock(),
        embedding_callback=AsyncMock(),
        settings=settings,
        database_name="db",
        name_conventions=LITERARY,
    )
    h._resolve_template_names = lambda nodes: {}  # type: ignore[method-assign]
    return h


def _search(vector: list[tuple[str, float]], keyword: list[str]) -> MagicMock:
    search = MagicMock()
    search.vector_search.return_value = vector
    search.keyword_search.return_value = [(nid, 1.0) for nid in keyword]
    return search


# --------------------------------------------------------------------------- the flag


def test_the_flag_is_read_as_the_extraction_wrote_it() -> None:
    assert is_reference_record({DESCRIPTOR_REFERENCE_KEY: True})
    assert not is_reference_record({DESCRIPTOR_REFERENCE_KEY: "true"})
    assert not is_reference_record({})
    assert not is_reference_record(None)
    assert not _seedable(NODES["count"])
    assert _seedable(NODES["oldcount"])
    assert _seedable(None)  # an id the graph does not hold: PageRank drops it


# --------------------------------------------------------------------------- vector seeds


def test_a_reference_a_name_outscores_takes_no_vector_seed_slot() -> None:
    """``Prince Vasíli's son`` embeds near a question about Vasíli, below him: a named node takes its slot."""
    search = _search([("vasili", 0.9), ("son", 0.8), ("helene", 0.7)], [])
    seeds = _handler(search)._match_seed_entities([0.1], seed_limit=2)
    assert [nid for nid, _ in seeds] == ["vasili", "helene"]


def test_a_reference_closer_than_every_name_seeds_by_similarity() -> None:
    """B0 q088: "Princess Mary's father" is closest to ``Prince Andrew's father``, which seeds.

    Its description alone holds what Prince Andrew said of women at supper;
    the looser reference ``the princess`` (below the named seeds) still gives
    its slot to a name.
    """
    search = _search(
        [
            ("father", 0.66),
            ("andrew", 0.60),
            ("bolkonski", 0.57),
            ("princess", 0.56),
            ("lise", 0.5),
        ],
        [],
    )
    seeds = _handler(search)._match_seed_entities([0.1], seed_limit=3)
    assert [nid for nid, _ in seeds] == ["father", "andrew", "bolkonski"]
    assert dict(seeds)["father"] == 0.66  # its similarity, as a named seed's


def test_an_anonymous_hub_does_not_crowd_out_the_named_person() -> None:
    """B0 q099: for "the little princess and General Kutúzov" the named Lise seeds, the hub by its label only.

    ``the little princess`` embeds second, below Kutúzov: no vector slot,
    so Lise keeps hers; the written label seeds it after the name match.
    """
    vector = [
        ("kutuzov", 0.67),
        ("little", 0.60),
        ("vasili", 0.56),
        ("andrew", 0.53),
        ("lise", 0.51),
    ]
    handler = _handler(_search(vector, ["kutuzov", "little"]))
    seeds = handler._match_seed_entities([0.1], seed_limit=4)
    assert [nid for nid, _ in seeds] == ["kutuzov", "vasili", "andrew", "lise"]
    merged = handler._add_keyword_seeds(
        "What is the connection between the little princess and General Kutúzov?", seeds, 10
    )
    assert [nid for nid, _ in merged] == ["kutuzov", "little", "vasili", "andrew", "lise"]


def test_the_similarity_rule_keeps_the_candidates_order() -> None:
    """Named candidates all kept; references at or above the best named one kept; the order is the match's."""
    nodes = {nid: NODES[nid] for nid in ("father", "princess", "andrew", "little")}
    candidates = [("father", 0.7), ("princess", 0.6), ("andrew", 0.6), ("little", 0.5)]
    assert _similarity_seeds(candidates, nodes) == [
        ("father", 0.7),
        ("princess", 0.6),
        ("andrew", 0.6),
    ]
    # only references above the threshold: none is outscored by a name
    assert _similarity_seeds([("little", 0.5)], nodes) == [("little", 0.5)]
    # an id the graph does not hold is no reference
    assert _similarity_seeds([("gone", 0.9), ("little", 0.5)], nodes) == [("gone", 0.9)]


def test_a_node_without_the_flag_still_seeds_by_similarity() -> None:
    search = _search([("oldcount", 0.9), ("natasha", 0.8)], [])
    seeds = _handler(search)._match_seed_entities([0.1], seed_limit=2)
    assert [nid for nid, _ in seeds] == ["oldcount", "natasha"]


# --------------------------------------------------------------------------- keyword seeds


def test_a_descriptors_inner_name_never_seeds_it() -> None:
    """A question naming Prince Vasíli keyword-seeds him, never ``Prince Vasíli's son``."""
    search = _search([], ["son", "vasili"])
    seeds = _handler(search)._add_keyword_seeds("What did Prince Vasíli want?", [], 10)
    assert [nid for nid, _ in seeds] == ["vasili"]


def test_a_references_alias_is_no_name_match() -> None:
    """``the count`` stores the alias ``Papa``: a question saying Papa does not seed him by it."""
    search = _search([], ["count", "natasha"])
    seeds = _handler(search)._add_keyword_seeds("What did Papa tell Natásha?", [], 10)
    assert [nid for nid, _ in seeds] == ["natasha"]


def test_a_question_writing_the_whole_label_seeds_the_reference_after_the_names() -> None:
    """A written-out "the little princess" seeds that node, below the named match and above similarity."""
    search = _search([], ["princess", "little", "andrew"])
    vector = [("helene", 0.6)]
    seeds = _handler(search)._add_keyword_seeds(
        "What did the little princess say to Prince Andrew?", vector, 10
    )
    assert [nid for nid, _ in seeds] == ["andrew", "little", "helene"]
    weights = dict(seeds)
    # never more teleport weight than a named seed
    assert weights["little"] <= min(weights["andrew"], weights["helene"])


def test_a_reference_seeds_only_on_its_whole_label() -> None:
    """A label seeds whole: "the little princess" is not "the princess", "the countess" not "the count"."""
    search = _search([], ["princess", "count", "little"])
    handler = _handler(search)
    assert [nid for nid, _ in handler._add_keyword_seeds("Who is the countess?", [], 10)] == []
    seeds = handler._add_keyword_seeds("Who is the little princess?", [], 10)
    assert [nid for nid, _ in seeds] == ["little"]
    assert dict(seeds)["little"] == 1.0  # no named seed to stay below


def test_a_reference_seed_in_a_possessive_is_the_label_written() -> None:
    """A possessive writes the label: "the count's daughter" seeds the count, after the named Natásha."""
    search = _search([], ["natasha", "count"])
    seeds = _handler(search)._add_keyword_seeds("Did the count's daughter Natásha dance?", [], 10)
    assert [nid for nid, _ in seeds] == ["natasha", "count"]


def test_a_reference_label_written_into_a_name_is_that_name() -> None:
    """A label written into a name is that name: "the Count Rostóv", not ``the count``; a sentence break ends the label."""
    search = _search([], ["count"])
    handler = _handler(search)
    assert handler._add_keyword_seeds("Where does the Count Rostóv live?", [], 10) == []
    seeds = handler._add_keyword_seeds("Who is the count? Natásha knows.", [], 10)
    assert [nid for nid, _ in seeds] == ["count"]


# --------------------------------------------------------------------------- the graph walk


def test_the_graph_walk_still_reaches_a_reference_and_ranks_it_after_the_names() -> None:
    """From the named seed Vasíli, his son's node is a related entity, after the named Hélène."""
    handler = _handler(question_gate=False)
    ppr = {"vasili": 0.5, "son": 0.4, "helene": 0.1}
    ctx = handler._assemble_graph_context(
        ppr, [("vasili", 0.9)], query="Who are Vasíli's children?"
    )
    assert [e["id"] for e in ctx["seed_entities"]] == ["vasili"]
    assert [e["id"] for e in ctx["related_entities"]] == ["helene", "son"]
    assert ctx["related_entities"][1]["standing"] == (True, False, False)  # a reference, connected
    assert "standing" not in ctx["related_entities"][0]
    assert ("Prince Vasíli\u2019s son", "child_of", "Prince Vasíli Kurágin") in {
        (t["source"], t["label"], t["target"]) for t in ctx["relationships"]
    }
    # the provenance lookup, too: the named entities' chunks first
    assert _provenance_lookup_ids(ctx, ppr) == ["helene", "vasili", "son"]


def test_a_reference_seed_ranks_after_the_named_seeds() -> None:
    handler = _handler(question_gate=False)
    ppr = {"little": 0.6, "andrew": 0.3}
    ctx = handler._assemble_graph_context(ppr, [("andrew", 1.0), ("little", 1.0)])
    assert [e["id"] for e in ctx["seed_entities"]] == ["andrew", "little"]


def test_nodes_without_the_flag_keep_the_pagerank_order() -> None:
    handler = _handler(question_gate=False)
    ppr = {"vasili": 0.5, "oldcount": 0.4, "helene": 0.1}
    ctx = handler._assemble_graph_context(ppr, [("vasili", 0.9)])
    assert [e["id"] for e in ctx["related_entities"]] == ["oldcount", "helene"]
    assert _provenance_lookup_ids(ctx, ppr) == ["oldcount", "helene", "vasili"]
