# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""graphrag_search on a path question: the shortest chains between the named entities, over the whole graph."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from chaoscypher_core.services.sources.engine.deduplication.name_identity import UNEVIDENCED_KEY
from tests.fakes.graphrag_world import (
    NODES,
    WALK_EDGES,
    WalkGraph,
    make_chunk,
    walk_edge,
    walk_handler,
)


if TYPE_CHECKING:
    from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
        GraphRAGToolHandlers,
    )


PATH_QUESTION = "How is Lise connected to General Kutúzov?"
"""Names two seeds (Lise Meinen, General Kutúzov) and a path cue: the rules call it ``path``."""

SENTENCE = "Lise was the wife of Prince Andrew."


CITATIONS = [
    {
        "edge_id": "e1",
        "chunk_id": "c1",
        "source_id": "s1",
        "justification": "",
        "sentences": [{"text": SENTENCE, "chunk_id": "c1", "local_index": 1}],
    }
]


def _handler(chat: Any = None, **kw: Any) -> GraphRAGToolHandlers:
    """The shared walk world (``graphrag_world.walk_handler``) under the rules, Lise's chunk and her edge's citation."""
    return walk_handler(
        chat,
        **{
            "router": "rules",
            "chunks": [make_chunk("c1", f"The little princess came in. {SENTENCE}")],
            "citations": CITATIONS,
            **kw,
        },
    )


def _hops(walk: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [(h["source"], h["label"], h["target"]) for h in walk["hops"]]


@pytest.mark.asyncio
async def test_a_path_question_states_the_chain_between_the_named_entities_with_evidence() -> None:
    h = _handler()
    result = await h.graphrag_search(PATH_QUESTION)
    stats = result["retrieval_stats"]
    assert result["shape"] == "path"
    assert stats["faults"] == []
    gc = result["graph_context"]
    [walk] = gc["walks"]  # the inverse twin folded: one chain, not two
    assert (walk["from"], walk["to"]) == ("Lise Meinen", "General Kutúzov")
    assert _hops(walk) == [
        ("Lise Meinen", "spouse_of", "Prince Andrew"),
        ("Prince Andrew", "serves", "General Kutúzov"),
    ]
    first, second = walk["hops"]
    assert (first["sentence"], first["chunk_id"], first["marker"]) == (SENTENCE, "c1", "C0:S2")
    assert second["sentence"] is None and "marker" not in second
    assert "walk_gaps" not in gc
    assert stats["walk_skipped"] == {
        "outside_graph": 0,
        "inverse": 1,
        "unevidenced": 0,
        "parallel": 0,
        "degree_capped": 0,
        "listing_capped": 0,
        "pairs": 0,
        "past_max_walks": 0,
    }
    assert stats["graph_listing_capped"] == 0
    lines = result["context_text"].splitlines()
    walk_line = (
        f'- walk: Lise Meinen spouse_of Prince Andrew "{SENTENCE}" [C0:S2]; '
        "Prince Andrew serves General Kutúzov"
    )
    assert walk_line in lines
    path_lines = [i for i, line in enumerate(lines) if line.startswith("- path:")]
    assert path_lines and lines.index(walk_line) < min(path_lines)
    asked = h.source_storage.get_relationship_citations_batch.call_args_list  # type: ignore[union-attr]
    assert any(set(c.args[1]) == {"e1", "e2"} for c in asked)  # the hops' edges, no inverse
    assert h.loaded == [(len(NODES), len(WALK_EDGES))]  # type: ignore[attr-defined]  # loaded once, whole


@pytest.mark.asyncio
async def test_the_real_pagerank_orders_the_pair_and_each_hop_keeps_its_stored_direction() -> None:
    """PageRank ranks Kutúzov over Lise here: the walk runs Kutúzov → Lise, its facts as stated."""
    result = await _handler(ppr=None).graphrag_search(PATH_QUESTION)
    assert result["retrieval_stats"]["faults"] == []
    [walk] = result["graph_context"]["walks"]
    assert (walk["from"], walk["to"]) == ("General Kutúzov", "Lise Meinen")
    assert _hops(walk) == [
        ("Prince Andrew", "serves", "General Kutúzov"),
        ("Lise Meinen", "spouse_of", "Prince Andrew"),
    ]


@pytest.mark.asyncio
async def test_a_connector_beyond_the_top_entities_neighbours_is_found_and_its_label_fetched() -> (
    None
):
    """Lise, Andrew, Moscow, Hélène, Kutúzov: four hops, Moscow no top entity's neighbour.

    The seed paths (one or two hops over the top entities' edges) cannot state
    it; the walk searches the whole graph and fetches Moscow for its label.
    """
    chain = [
        walk_edge("e1", "L", "spouse_of", "A"),
        walk_edge("e5", "A", "visits", "Z"),
        walk_edge("e6", "X", "lives_in", "Z"),
        walk_edge("e7", "X", "knows", "K"),
    ]
    ppr = {"L": 0.5, "K": 0.4, "A": 0.3, "X": 0.2}
    expected = [
        ("Lise Meinen", "spouse_of", "Prince Andrew"),
        ("Prince Andrew", "visits", "Moscow"),
        ("Hélène", "lives_in", "Moscow"),
        ("Hélène", "knows", "General Kutúzov"),
    ]
    # the loaded nodes carry their labels: Moscow's is read there, no fetch
    h = _handler(ppr=ppr, edges=chain, ppr_top_k=2, max_hops=4)
    gc = (await h.graphrag_search(PATH_QUESTION))["graph_context"]
    assert "paths" not in gc
    [walk] = gc["walks"]
    assert _hops(walk) == expected
    assert not any("Z" in ids for ids in h.graph.fetched)  # type: ignore[attr-defined]
    # loaded without labels: Moscow is fetched for its label
    bare = _handler(ppr=ppr, graph=WalkGraph(chain, minimal_labels=False), ppr_top_k=2, max_hops=4)
    [walk] = (await bare.graphrag_search(PATH_QUESTION))["graph_context"]["walks"]
    assert _hops(walk) == expected
    assert bare.graph.fetched[-1] == ["Z"]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_a_pair_no_chain_joins_within_the_hops_is_a_gap_line() -> None:
    """The walk folded Kutúzov's inverse, which cuts no route: the plain wording stands."""
    result = await _handler(max_hops=1).graphrag_search(PATH_QUESTION)
    gc = result["graph_context"]
    assert "walks" not in gc
    assert result["retrieval_stats"]["walk_skipped"]["inverse"] == 1
    assert gc["walk_gaps"] == [
        {"from": "Lise Meinen", "to": "General Kutúzov", "max_hops": 1, "left_out": False}
    ]
    assert (
        "- no chain of up to 1 relations connects Lise Meinen and General Kutúzov in the graph"
        in result["context_text"]
    )
    # an unevidenced edge left out could have been the route: the gap claims only what it read
    edges = [*WALK_EDGES, walk_edge("e9", "L", "knows", "Z", **{UNEVIDENCED_KEY: True})]
    hedged = await _handler(max_hops=1, edges=edges).graphrag_search(PATH_QUESTION)
    assert hedged["retrieval_stats"]["walk_skipped"]["unevidenced"] == 1
    assert hedged["graph_context"]["walk_gaps"][0]["left_out"] is True
    assert "among the relations the walk read" in hedged["context_text"]


@pytest.mark.asyncio
async def test_a_gap_over_a_walk_that_read_every_edge_says_no_chain_in_the_graph() -> None:
    edges = [walk_edge("e1", "L", "spouse_of", "A"), walk_edge("e4", "A", "references", "Z")]
    result = await _handler(edges=edges).graphrag_search(PATH_QUESTION)
    assert not any(result["retrieval_stats"]["walk_skipped"].values())
    assert result["graph_context"]["walk_gaps"][0]["left_out"] is False
    assert (
        "- no chain of up to 3 relations connects Lise Meinen and General Kutúzov in the graph"
        in result["context_text"]
    )


class _CappedListing(WalkGraph):
    """A whole-graph edge listing that returns its limit: the edges past it were never read."""

    def list_edges_minimal(self, **kw: Any) -> list[Any]:
        pad = [walk_edge(f"p{i}", "Z", "references", "Z") for i in range(kw["limit"])]
        return [*self.edges, *pad][: kw["limit"]]


@pytest.mark.asyncio
async def test_a_capped_graph_listing_is_recorded_and_the_gap_says_what_the_walk_read() -> None:
    edges = [walk_edge("e1", "L", "spouse_of", "A"), walk_edge("e4", "A", "references", "Z")]
    h = _handler(graph=_CappedListing(edges), max_graph_nodes=1000)
    result = await h.graphrag_search(PATH_QUESTION)
    stats = result["retrieval_stats"]
    assert stats["graph_listing_capped"] == 1
    assert stats["walk_skipped"]["listing_capped"] == 1
    assert result["graph_context"]["walk_gaps"][0]["left_out"] is True
    assert "among the relations the walk read" in result["context_text"]


@pytest.mark.asyncio
async def test_the_walks_are_capped_for_the_whole_question_and_the_rest_counted() -> None:
    """Lise to Kutúzov through Andrew, through Hélène, through Moscow: room for two walks."""
    edges = [
        walk_edge("e1", "L", "spouse_of", "A"),
        walk_edge("e2", "A", "serves", "K"),
        walk_edge("e5", "L", "knows", "X"),
        walk_edge("e6", "X", "knows", "K"),
        walk_edge("e7", "L", "visits", "Z"),
        walk_edge("e8", "Z", "references", "K"),
    ]
    result = await _handler(edges=edges, max_walks=2).graphrag_search(PATH_QUESTION)
    walks = result["graph_context"]["walks"]
    assert [[h["edge_id"] for h in w["hops"]] for w in walks] == [["e1", "e2"], ["e5", "e6"]]
    assert result["retrieval_stats"]["walk_skipped"]["past_max_walks"] == 0  # searched for two
    assert "walk_gaps" not in result["graph_context"]


@pytest.mark.asyncio
async def test_a_path_shape_with_one_named_entity_assembles_as_lookup_and_says_so() -> None:
    async def chat(messages: list[dict[str, Any]], **_: Any) -> dict[str, Any]:
        return {"content": '{"shape": "path", "reason": "asks how"}'}

    result = await _handler(chat=chat, router="llm").graphrag_search("What did Lise say?")
    stats = result["retrieval_stats"]
    assert result["shape"] == "path"
    assert stats["shape_reason"] == "asks how; fewer than two named entities, lookup assembly"
    assert "walks" not in result["graph_context"]
    assert "walk_gaps" not in result["graph_context"]
    assert "walk_skipped" not in stats


@pytest.mark.asyncio
async def test_a_lookup_question_runs_no_walk() -> None:
    result = await _handler().graphrag_search("Who is Lise?")
    assert result["shape"] == "lookup"
    assert "walks" not in result["graph_context"]
    assert "walk_skipped" not in result["retrieval_stats"]


@pytest.mark.asyncio
async def test_a_failed_walk_records_its_fault_and_the_rest_of_the_context_stands() -> None:
    class _FailingFetch(WalkGraph):
        def get_nodes_batch(self, ids: list[str], **kw: Any) -> list[Any]:
            if "Z" in ids:  # only the walk asks for Moscow
                raise RuntimeError("database is locked")
            return super().get_nodes_batch(ids, **kw)

    chain = [
        walk_edge("e1", "L", "spouse_of", "A"),
        walk_edge("e5", "A", "visits", "Z"),
        walk_edge("e6", "X", "lives_in", "Z"),
        walk_edge("e7", "X", "knows", "K"),
    ]
    graph = _FailingFetch(chain, minimal_labels=False)
    h = _handler(ppr={"L": 0.5, "K": 0.4, "A": 0.3, "X": 0.2}, graph=graph, ppr_top_k=2, max_hops=4)
    result = await h.graphrag_search(PATH_QUESTION)
    assert result["retrieval_stats"]["faults"] == ["walk"]
    gc = result["graph_context"]
    assert {e["id"] for e in gc["seed_entities"]} == {"L", "K"}
    assert "walks" not in gc and "walk_gaps" not in gc


@pytest.mark.asyncio
async def test_a_node_the_graph_did_not_load_is_never_walked_through() -> None:
    """Prince Andrew is outside the loaded graph (a disabled source): no chain, a gap, and counted."""
    graph = WalkGraph(loaded=["L", "K", "X", "Z"])
    result = await _handler(graph=graph).graphrag_search(PATH_QUESTION)
    gc = result["graph_context"]
    assert "walks" not in gc
    assert gc["walk_gaps"] == [
        {"from": "Lise Meinen", "to": "General Kutúzov", "max_hops": 3, "left_out": True}
    ]
    assert result["retrieval_stats"]["walk_skipped"]["outside_graph"] == len(WALK_EDGES)


@pytest.mark.asyncio
async def test_a_named_seed_ranked_below_the_top_entities_is_still_a_walk_end() -> None:
    """With one top entity (Lise) Kutúzov is no entity line, but the question names him."""
    h = _handler(ppr={"L": 0.5, "A": 0.4, "X": 0.3, "K": 0.2}, ppr_top_k=1)
    result = await h.graphrag_search(PATH_QUESTION)
    assert [e["id"] for e in result["graph_context"]["seed_entities"]] == ["L"]
    [walk] = result["graph_context"]["walks"]
    assert (walk["from"], walk["to"]) == ("Lise Meinen", "General Kutúzov")
    assert "fewer than two" not in result["retrieval_stats"]["shape_reason"]
