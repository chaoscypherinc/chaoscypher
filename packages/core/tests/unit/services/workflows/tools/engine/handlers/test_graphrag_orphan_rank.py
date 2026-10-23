# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A flagged orphan (``properties.orphan``) seeds like any named node and ranks after the connected ones.

Keep, flag, never delete (2026-10-07): the commit keeps an edgeless entity
that carries evidence, flagged ``orphan``, where it used to delete it
("Márya Lvóvna Karágina", Julie's mother, on the v4b generic arm). Retrieval
reads the flag through ``name_identity.node_standing``, the one reading of
the pipeline flags: the seed rules (``_seedable``) read only the reference
flag, so an orphan seeds by name, by written label or by vector; the entity
lines and the provenance lookup sort by the whole standing (``_by_standing``):
named before reference, connected before orphan, PageRank order kept within
each.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    DESCRIPTOR_REFERENCE_KEY,
    ORPHAN_KEY,
    NodeStanding,
    is_orphan_record,
    node_standing,
)
from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
    GraphRAGToolHandlers,
    _by_standing,
    _provenance_lookup_ids,
    _seedable,
    _similarity_seeds,
)
from chaoscypher_core.settings import EngineSettings
from tests.fakes.domain_tables import domain_conventions
from tests.fakes.graph_edges import EdgesByNode


LITERARY = domain_conventions("literary")


def _node(
    nid: str, label: str, *, orphan: bool = False, reference: bool = False, **props: Any
) -> SimpleNamespace:
    properties = dict(props)
    if orphan:
        properties[ORPHAN_KEY] = True
    if reference:
        properties[DESCRIPTOR_REFERENCE_KEY] = True
    return SimpleNamespace(
        id=nid, label=label, template_id="person", entity_type="Character", properties=properties
    )


NODES = {
    "julie": _node("julie", "Julie Karágina", definition="Nicholas's friend."),
    "nicholas": _node("nicholas", "Nicholas Rostóv", definition="The count's son."),
    "marya": _node(
        "marya", "Márya Lvóvna Karágina", orphan=True, definition="Julie's mother, a visitor."
    ),
    "visitor": _node(
        "visitor", "the visitor", orphan=True, reference=True, definition="A lady who called."
    ),
    "count": _node("count", "the count", reference=True, definition="A host."),
}
EDGES = [
    SimpleNamespace(
        id="e1", source_node_id="julie", label="interacts_with", target_node_id="nicholas"
    ),
    SimpleNamespace(id="e2", source_node_id="count", label="parent_of", target_node_id="nicholas"),
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
        graph_repository=_Graph(),  # type: ignore[arg-type]  # the two listings it reads
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


def test_the_flag_is_read_as_the_commit_wrote_it() -> None:
    assert is_orphan_record({ORPHAN_KEY: True})
    assert not is_orphan_record({ORPHAN_KEY: "true"})
    assert not is_orphan_record({ORPHAN_KEY: "yes"})  # an extracted value is the text's
    assert not is_orphan_record({})
    assert not is_orphan_record(None)
    assert node_standing({}) == NodeStanding(reference=False, orphan=False)
    assert node_standing(NODES["visitor"].properties) == (True, True, False)


def test_an_orphan_seeds_like_any_named_node() -> None:
    """By name (keyword seeds), and by vector even when a connected candidate is closer."""
    assert _seedable(NODES["marya"])
    assert not _seedable(NODES["visitor"])  # an orphan reference is a reference
    candidates = [("julie", 0.9), ("marya", 0.8)]
    assert _similarity_seeds(candidates, NODES) == candidates
    search = MagicMock()
    search.keyword_search.return_value = [("marya", 1.0), ("julie", 1.0)]
    seeds = _handler(search)._add_keyword_seeds("Who is Márya Lvóvna's daughter?", [], 10)
    assert [nid for nid, _ in seeds] == ["marya"]


def test_the_ordering_is_named_then_reference_connected_then_orphan_order_kept() -> None:
    entries: list[dict[str, Any]] = [
        {"id": "visitor", "standing": NodeStanding(True, True)},
        {"id": "marya", "standing": NodeStanding(False, True)},
        {"id": "count", "standing": NodeStanding(True, False)},
        {"id": "julie"},
        {"id": "nicholas"},
    ]
    assert [e["id"] for e in _by_standing(entries)] == [
        "julie",
        "nicholas",
        "marya",
        "count",
        "visitor",
    ]


def test_an_orphan_seed_ranks_after_the_connected_seeds_and_its_chunks_after_theirs() -> None:
    """PageRank keeps a seeded orphan's mass on itself; the ranking puts it after the connected seeds."""
    handler = _handler(question_gate=False)
    ppr = {"marya": 0.6, "julie": 0.3, "nicholas": 0.1}
    ctx = handler._assemble_graph_context(ppr, [("marya", 1.0), ("julie", 0.9)])
    assert [e["id"] for e in ctx["seed_entities"]] == ["julie", "marya"]
    assert ctx["seed_entities"][1]["standing"] == (False, True, False)
    assert "standing" not in ctx["seed_entities"][0]
    assert [e["id"] for e in ctx["related_entities"]] == ["nicholas"]
    assert _provenance_lookup_ids(ctx, ppr) == ["nicholas", "julie", "marya"]
    # the orphan's own description is still in the context
    assert "Julie's mother" in ctx["seed_entities"][1].get("description", "")
