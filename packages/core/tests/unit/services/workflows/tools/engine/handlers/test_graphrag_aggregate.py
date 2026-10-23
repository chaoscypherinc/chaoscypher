# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""graphrag_search on an aggregate question: the named entity's edges by relation, with evidence."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    INVERSE_OF_KEY,
    UNEVIDENCED_KEY,
)
from chaoscypher_core.services.workflows.tools.engine.handlers import graphrag_handlers
from chaoscypher_core.settings import EngineSettings
from tests.fakes.graph_edges import EdgesByNode
from tests.fakes.graphrag_world import batch_lookup, make_chunk, walk_handler


if TYPE_CHECKING:
    from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
        GraphRAGToolHandlers,
    )


AGGREGATE_QUESTION = "Which people are the children of Count Rostóv?"
"""Names one seed (Count Rostóv) and an aggregate cue: the rules call it ``aggregate``."""


def _node(nid: str, label: str, source_id: str = "s1") -> SimpleNamespace:
    return SimpleNamespace(
        id=nid, label=label, template_id="person", properties={}, source_id=source_id
    )


def _edge(eid: str, src: str, label: str, tgt: str, **props: Any) -> SimpleNamespace:
    return SimpleNamespace(
        id=eid, source_node_id=src, target_node_id=tgt, label=label, properties=dict(props)
    )


NODES = {
    n.id: n
    for n in (
        _node("R", "Count Rostóv"),
        _node("N", "Natásha"),
        _node("K", "Nicholas", source_id="s2"),  # the one node of another source
        _node("C", "the countess"),
    )
}
EDGES = [
    _edge("e1", "N", "child_of", "R"),
    _edge("e1i", "R", "parent_of", "N", **{INVERSE_OF_KEY: "child_of"}),
    _edge("e2", "K", "child_of", "R"),
    _edge("e4", "R", "spouse_of", "C"),
    _edge("e5", "R", "interacts_with", "K", **{UNEVIDENCED_KEY: True}),
]
CITATIONS = [
    {
        "edge_id": "e1",
        "chunk_id": "c2",
        "source_id": "s1",
        "justification": "",
        "sentences": [{"text": "She was the youngest girl.", "chunk_id": "c2", "local_index": 1}],
    }
]


class _Graph(EdgesByNode):
    def list_nodes_minimal(self, **_: Any) -> list[Any]:
        return list(NODES.values())

    def list_edges_minimal(self, **_: Any) -> list[Any]:
        return list(EDGES)

    def list_edges(self, *, limit: int, **kw: Any) -> list[Any]:
        if "source_node_id" in kw:
            return [e for e in EDGES if e.source_node_id == kw["source_node_id"]][:limit]
        return [e for e in EDGES if e.target_node_id == kw["target_node_id"]][:limit]

    def get_nodes_batch(self, ids: list[str], **_: Any) -> list[Any]:
        return [NODES[i] for i in ids if i in NODES]


def _handler(chat: Any = None, **graphrag: Any) -> GraphRAGToolHandlers:
    """The shared handler builder (``graphrag_world.walk_handler``) over the Rostóv graph, seeded on the count."""
    return walk_handler(
        chat,
        **{
            "graph": _Graph(),
            "ppr": {"R": 0.5, "N": 0.3, "K": 0.2},
            "keyword": [("R", 1.0)],
            "chunks": [make_chunk("c2", "Natásha ran in. She was the youngest girl.")],
            "citations": CITATIONS,
            "router": "rules",
            **graphrag,
        },
    )


@pytest.mark.asyncio
async def test_an_aggregate_question_lists_the_entitys_relation_with_its_sentence() -> None:
    h = _handler()
    result = await h.graphrag_search(AGGREGATE_QUESTION)
    assert result["shape"] == "aggregate"
    assert result["retrieval_stats"]["faults"] == []
    [block] = result["graph_context"]["aggregates"]
    assert block["entity"] == "Count Rostóv"
    assert [g["label"] for g in block["groups"]] == ["child_of"]  # the question's family only
    # every row the block left out is counted, the unevidenced one included
    assert block["skipped"] == {
        "inverse": 1,
        "unevidenced": 1,
        "other_labels": 1,
        "duplicate": 0,
        "past_per_label": 0,
        "listing_capped": 0,
    }
    natasha = block["groups"][0]["rows"][0]
    assert natasha["marker"] == "C0:S2"  # local index 1 is the chunk's [S2], under its final alias
    assert (
        '- Count Rostóv, child_of (2): Natásha child_of Count Rostóv "She was the youngest girl."'
        " [C0:S2]; Nicholas child_of Count Rostóv" in result["context_text"].splitlines()
    )
    asked = h.source_storage.get_relationship_citations_batch.call_args  # type: ignore[union-attr]
    assert asked.args[0] == "db" and set(asked.args[1]) == {"e1", "e1i", "e2", "e4", "e5"}


@pytest.mark.asyncio
async def test_a_lookup_question_has_no_aggregate_block() -> None:
    h = _handler()
    result = await h.graphrag_search("Who is Count Rostóv?")
    assert result["shape"] == "lookup"
    assert "aggregates" not in result["graph_context"]
    h.source_storage.get_relationship_citations_batch.assert_not_called()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_unreadable_citations_are_a_fault_and_the_rows_render_without_sentences() -> None:
    h = _handler()
    h.source_storage.get_relationship_citations_batch.side_effect = RuntimeError("gone")  # type: ignore[union-attr]
    result = await h.graphrag_search(AGGREGATE_QUESTION)
    assert result["retrieval_stats"]["faults"] == ["relationship_citations"]
    rows = result["graph_context"]["aggregates"][0]["groups"][0]["rows"]
    assert [r["sentence"] for r in rows] == [None, None]


def test_families_are_the_question_relations_labels() -> None:
    h = _handler()
    assert h._families(["parent"]) == {"parent": frozenset({"parent_of", "child_of"})}
    assert h._families([]) == {}


def test_citations_by_edge_group_the_rows_and_ignore_a_store_without_them() -> None:
    h = _handler()
    assert h._citations_by_edge(["e1"], None) == {"e1": CITATIONS}
    h.source_storage = MagicMock()  # a double whose method returns no list
    assert h._citations_by_edge(["e1"], None) == {}
    h.source_storage = None
    assert h._citations_by_edge(["e1"], None) == {}


@pytest.mark.asyncio
async def test_under_a_source_scope_a_row_to_another_source_is_absent_and_counted_nowhere() -> None:
    h = _handler()
    result = await h.graphrag_search(AGGREGATE_QUESTION, source_ids=["s1"])
    [block] = result["graph_context"]["aggregates"]
    [child] = block["groups"]
    assert [r["source"] for r in child["rows"]] == ["Natásha"]  # Nicholas is of source s2
    assert child["count"] == 1
    # neither Nicholas's child_of nor the unevidenced edge to him is counted as left out
    assert block["skipped"] == {
        "inverse": 1,
        "unevidenced": 0,
        "other_labels": 1,
        "duplicate": 0,
        "past_per_label": 0,
        "listing_capped": 0,
    }
    asked = h.source_storage.get_relationship_citations_batch.call_args  # type: ignore[union-attr]
    assert asked.args[2] == ["s1"]


@pytest.mark.asyncio
async def test_a_listing_that_hits_its_limit_renders_the_count_as_a_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capped = SimpleNamespace(batching=SimpleNamespace(graphrag_edge_query_limit=2))
    monkeypatch.setattr(graphrag_handlers, "get_settings", lambda: capped)
    result = await _handler().graphrag_search(AGGREGATE_QUESTION)
    [block] = result["graph_context"]["aggregates"]
    assert block["skipped"]["listing_capped"] == 1
    assert all(g["count_capped"] for g in block["groups"])
    assert "- Count Rostóv, child_of (2+): " in result["context_text"]


@pytest.mark.asyncio
async def test_under_the_default_levers_only_the_named_seed_gets_a_block() -> None:
    """Two seeds, the question names one: question_entities_first is off, the block still follows the name."""
    assert EngineSettings().graphrag.question_entities_first is False
    h = _handler()
    h._embed_query = AsyncMock(return_value=[0.1, 0.2])  # type: ignore[method-assign]
    h.search.vector_search.return_value = [("N", 0.9)]  # type: ignore[attr-defined]  # a likeness
    result = await h.graphrag_search(AGGREGATE_QUESTION)
    assert {e["id"] for e in result["graph_context"]["seed_entities"]} >= {"R", "N"}
    assert [b["entity"] for b in result["graph_context"]["aggregates"]] == ["Count Rostóv"]


def _near_and_far(
    evidence: int, citations: list[dict[str, Any]] = CITATIONS
) -> GraphRAGToolHandlers:
    """Budget 2; the hybrid arm finds two near chunks (c5, c6), the aggregate row cites the far c2."""
    h = _handler(
        chunk_limit=2,
        shape_levers={"aggregate": {"vector_reserve": 2, "evidence_chunks": evidence}},
    )
    h.search.hybrid_search = AsyncMock(return_value=[("chunk:c5", 0.9), ("chunk:c6", 0.8)])  # type: ignore[method-assign]
    h.indexing.get_chunks_by_ids_batch.side_effect = batch_lookup(  # type: ignore[union-attr]
        make_chunk("c2", "Natásha ran in. She was the youngest girl."),
        make_chunk("c5", "The children of the count sat down."),
        make_chunk("c6", "The count's children were many."),
    )
    h.indexing.get_source_filenames_by_ids.return_value = {}  # type: ignore[union-attr]
    h.source_storage.get_relationship_citations_batch.return_value = citations  # type: ignore[union-attr]
    return h


@pytest.mark.asyncio
async def test_a_far_cited_chunk_enters_ahead_of_a_near_vector_chunk_under_evidence_one() -> None:
    """Lever 1: the row's chunk takes a slot from the hybrid arm's second hit, and its marker resolves."""
    result = await _near_and_far(1).graphrag_search(AGGREGATE_QUESTION)
    assert result["retrieval_stats"]["faults"] == []
    assert [c["chunk_id"] for c in result["chunks"]] == ["c2", "c5"]
    assert [c["retrieval_origin"] for c in result["chunks"]] == ["evidence", "vector"]
    stats = result["retrieval_stats"]
    # c6 is reserved but has no victim (c2 is evidence, c5 reserved): kept out, counted
    assert (stats["evidence_chunks"], stats["reserve_shortfall"], stats["evidence_left_out"]) == (
        1,
        1,
        0,
    )
    natasha = result["graph_context"]["aggregates"][0]["groups"][0]["rows"][0]
    assert natasha["marker"] == "C0:S2"


@pytest.mark.asyncio
async def test_under_evidence_zero_the_far_chunk_stays_out_and_the_chunks_are_todays() -> None:
    """Lever 0: the hybrid arm's two hits, exactly the chunks of a graph that cites nothing."""
    result = await _near_and_far(0).graphrag_search(AGGREGATE_QUESTION)
    uncited = await _near_and_far(0, citations=[]).graphrag_search(AGGREGATE_QUESTION)
    assert [c["chunk_id"] for c in result["chunks"]] == ["c5", "c6"]
    assert result["chunks"] == uncited["chunks"]
    stats = result["retrieval_stats"]
    assert (stats["evidence_chunks"], stats["reserve_shortfall"], stats["evidence_left_out"]) == (
        0,
        0,
        1,
    )
    natasha = result["graph_context"]["aggregates"][0]["groups"][0]["rows"][0]
    assert "marker" not in natasha  # its chunk is not in the context


@pytest.mark.asyncio
async def test_an_aggregate_question_naming_no_entity_assembles_as_a_lookup_and_says_so() -> None:
    """The model calls it an aggregate, but the question names no seed: no block, never every seed's.

    Until 2026-10-09 the block fell back to every seed's edges.
    """

    async def chat(messages: list[dict[str, Any]], **_: Any) -> dict[str, Any]:
        return {"content": '{"shape": "aggregate", "reason": "asks for a list"}'}

    question = "Which of them were children?"
    h = _handler(chat, router="llm")
    h._embed_query = AsyncMock(return_value=[0.1, 0.2])  # type: ignore[method-assign]
    h.search.vector_search.return_value = [("R", 0.9)]  # type: ignore[attr-defined]  # a likeness
    result = await h.graphrag_search(question)
    stats = result["retrieval_stats"]
    assert (result["shape"], stats["shape_source"]) == ("aggregate", "llm")
    assert stats["shape_reason"] == "asks for a list; no named entity, lookup assembly"
    assert "aggregates" not in result["graph_context"]
    assert result["graph_context"]["seed_entities"]  # the lookup's assembly stands
    off = _handler(router="off")
    off._embed_query = AsyncMock(return_value=[0.1, 0.2])  # type: ignore[method-assign]
    off.search.vector_search.return_value = [("R", 0.9)]  # type: ignore[attr-defined]
    lookup = await off.graphrag_search(question)
    assert result["graph_context"]["relationships"] == lookup["graph_context"]["relationships"]


@pytest.mark.asyncio
async def test_a_global_question_runs_the_lookup_assembly_and_says_the_block_waits() -> None:
    async def chat(messages: list[dict[str, Any]], **_: Any) -> dict[str, Any]:
        return {"content": '{"shape": "global", "reason": "the big picture"}'}

    result = await _handler(chat, router="llm").graphrag_search(AGGREGATE_QUESTION)
    stats = result["retrieval_stats"]
    assert result["shape"] == "global"
    assert stats["shape_reason"] == "the big picture; no community block until phase B"
    assert "aggregates" not in result["graph_context"]
