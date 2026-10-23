# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""GraphRAG retrieval defects found by the fourth benchmark audit wave (2026-10-06), pinned.

The provenance arm took the citation lookup's row order (by node id) for its
PageRank order; the reserve protected hybrid chunks past the budget; a
retrieval over an index flagged for a full reindex was complete as far as its
record said; a descriptor alias ("Bezúkhov's son") named the person it is a
relation of; and a scoped chat read relationships to entities out of scope.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.benchmark.retrieval import (
    DegradedRetrievalError,
    require_complete_retrieval,
)
from chaoscypher_core.models import Edge, Node
from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
    GraphRAGToolHandlers,
    _in_lookup_order,
    _names_a_term,
    _naming_terms,
    _ppr_ranked,
)
from chaoscypher_core.settings import EngineSettings
from tests.fakes.domain_tables import domain_conventions
from tests.fakes.graph_edges import edges_by_node_via


LITERARY = domain_conventions("literary")


def _settings(**graphrag: Any) -> EngineSettings:
    settings = EngineSettings()
    for key, value in graphrag.items():
        setattr(settings.graphrag, key, value)
    return settings


class _Chunks:
    """Chunk rows for any id, returned sorted by id (never in the order asked for)."""

    def get_chunks_by_ids_batch(self, ids: list[str], **_: Any) -> list[dict[str, Any]]:
        return [
            {"id": i, "content": f"Text {i}.", "source_id": "s1", "database_name": "db"}
            for i in sorted(ids)
        ]


def _provenance_handler(citations: list[dict[str, Any]]) -> GraphRAGToolHandlers:
    storage = MagicMock()
    storage.get_citations_batch.return_value = citations
    return GraphRAGToolHandlers(
        MagicMock(),
        MagicMock(),
        indexing_repository=_Chunks(),  # type: ignore[arg-type]
        source_storage=storage,
        settings=_settings(),
        database_name="db",
    )


@pytest.mark.asyncio
async def test_provenance_chunks_follow_the_lookup_order_not_the_rows() -> None:
    """The adapter returns rows by ``entity_uri``; the chunks keep the PageRank order asked for."""
    rows = [  # as SQLite's IN walk returns them: by entity id
        {"entity_uri": "a_low", "chunk_id": "c_a"},
        {"entity_uri": "m_top", "chunk_id": "c_m2", "confidence": 0.5},
        {"entity_uri": "m_top", "chunk_id": "c_m1", "confidence": 0.9},
        {"entity_uri": "z_mid", "chunk_id": "c_z"},
    ]
    handler = _provenance_handler(rows)
    chunks = await handler._retrieve_provenance_chunks(["m_top", "z_mid", "a_low"], None)
    assert [c["chunk_id"] for c in chunks] == ["c_m1", "c_m2", "c_z", "c_a"]
    reversed_handler = _provenance_handler(list(reversed(rows)))
    again = await reversed_handler._retrieve_provenance_chunks(["m_top", "z_mid", "a_low"], None)
    assert [c["chunk_id"] for c in again] == ["c_m1", "c_m2", "c_z", "c_a"]


def test_rows_are_ordered_by_lookup_position_and_unknown_keys_drop() -> None:
    rows = [{"k": "b", "n": 2}, {"k": "x"}, {"k": "a", "n": 1}, {"k": "b", "n": 1}]
    out = _in_lookup_order(rows, ["a", "b"], key=lambda r: r["k"], then=lambda r: r["n"])
    assert out == [{"k": "a", "n": 1}, {"k": "b", "n": 1}, {"k": "b", "n": 2}]


def test_pagerank_ties_break_by_node_id_not_by_listing_order() -> None:
    assert _ppr_ranked({"z": 0.2, "b": 0.5, "a": 0.2}) == ["b", "a", "z"]
    assert _ppr_ranked({"a": 0.2, "b": 0.5, "z": 0.2}) == ["b", "a", "z"]


def test_the_reserve_is_cut_to_the_budget() -> None:
    """Budget 3, reserve 5: hybrid's own top 3 is kept, whatever its 4th and 5th hold."""

    def chunk(cid: str, origin: str) -> dict[str, Any]:
        return {"chunk_id": cid, "retrieval_origin": origin}

    kept = [chunk("h4", "both"), chunk("h5", "both"), chunk("h1", "both")]
    rest = [chunk("h2", "vector"), chunk("h3", "vector")]
    out = GraphRAGToolHandlers._reserve_hybrid_hits(kept, rest, ["h1", "h2", "h3", "h4", "h5"], 5)
    assert {c["chunk_id"] for c in out} == {"h1", "h2", "h3"}


class _FlaggedSearch:
    """A search repository whose index is flagged for a full reindex."""

    needs_full_reindex = True

    def __init__(self) -> None:
        self.hybrid_search = AsyncMock(return_value=[("chunk:c1", 0.9)])

    def vector_search(self, *_: Any, **__: Any) -> list[Any]:
        return []

    def keyword_search(self, *_: Any, **__: Any) -> list[Any]:
        return []


@pytest.mark.asyncio
async def test_a_retrieval_over_an_index_flagged_for_reindex_is_refused() -> None:
    async def embed(_: str) -> dict[str, list[float]]:
        return {"embedding": [1.0, 0.0]}

    handler = GraphRAGToolHandlers(
        MagicMock(),
        _FlaggedSearch(),  # type: ignore[arg-type]
        indexing_repository=_Chunks(),  # type: ignore[arg-type]
        embedding_callback=embed,
        settings=_settings(chunk_limit=1),
    )
    hybrid = await handler.hybrid_retrieval("Who?", 1)
    assert "needs_full_reindex" in hybrid["retrieval_stats"]["faults"]
    with pytest.raises(DegradedRetrievalError):
        require_complete_retrieval(hybrid, mode="hybrid", chunk_limit=1, indexed_chunks=1)
    full = await handler.graphrag_search("Who?", limit=1)
    assert "needs_full_reindex" in full["retrieval_stats"]["faults"]


@pytest.mark.asyncio
async def test_an_unflagged_or_unknown_index_records_nothing() -> None:
    search = _FlaggedSearch()
    search.needs_full_reindex = False
    handler = GraphRAGToolHandlers(
        MagicMock(),
        search,
        indexing_repository=_Chunks(),
        settings=_settings(),  # type: ignore[arg-type]
    )
    assert (await handler.hybrid_retrieval("Who?", 1))["retrieval_stats"]["faults"] == []
    mock_search = MagicMock()  # a double whose attribute is a truthy mock: not "flagged"
    mock_search.hybrid_search = AsyncMock(return_value=[])
    handler = GraphRAGToolHandlers(MagicMock(), mock_search, settings=_settings())
    assert (await handler.hybrid_retrieval("Who?", 1))["retrieval_stats"]["faults"] == []


def test_a_descriptor_alias_names_no_one() -> None:
    """Pierre's alias "Bezúkhov's son" names the old count, not Pierre (q049/q050)."""
    pierre = Node(
        id="pierre",
        label="Pierre",
        template_id="t",
        properties={"aliases": ["Bezúkhov's son", "Pierre Bezúkhov"]},
    )
    son = Node(
        id="son", label="Pierre", template_id="t", properties={"aliases": ["Bezúkhov's son"]}
    )
    terms = _naming_terms("What did Count Bezúkhov leave in his will?", LITERARY)
    assert _names_a_term(pierre, terms, LITERARY)  # "Pierre Bezúkhov" still names him
    assert not _names_a_term(son, terms, LITERARY)


def test_a_scoped_graph_context_drops_edges_leaving_the_scope() -> None:
    nodes = {
        "a": Node(id="a", label="Anna", template_id="t", properties={}, source_id="s1"),
        "b": Node(id="b", label="Boris", template_id="t", properties={}, source_id="s1"),
        "x": Node(id="x", label="Xavier", template_id="t", properties={}, source_id="s9"),
    }
    edges = [
        Edge(id="e1", template_id="r", source_node_id="a", target_node_id="b", label="knows"),
        Edge(id="e2", template_id="r", source_node_id="a", target_node_id="x", label="knows"),
    ]
    graph = MagicMock()
    graph.list_edges.side_effect = lambda source_node_id=None, target_node_id=None, **_: [
        e
        for e in edges
        if (source_node_id is None or e.source_node_id == source_node_id)
        and (target_node_id is None or e.target_node_id == target_node_id)
    ]
    graph.list_edges_by_node.side_effect = edges_by_node_via(graph.list_edges)
    graph.get_nodes_batch.side_effect = lambda ids, **_: [nodes[i] for i in ids if i in nodes]
    graph.get_template.return_value = SimpleNamespace(name="Person")
    handler = GraphRAGToolHandlers(
        graph,
        MagicMock(),
        settings=_settings(question_gate=False, triple_selection="all_edges"),
        name_conventions=LITERARY,
    )
    ppr = {"a": 0.6, "b": 0.4}
    whole = handler._assemble_graph_context(ppr, [("a", 1.0)])
    scoped = handler._assemble_graph_context(ppr, [("a", 1.0)], source_ids=["s1"])
    assert {t["target_id"] for t in whole["relationships"]} == {"b", "x"}
    assert {t["target_id"] for t in scoped["relationships"]} == {"b"}
