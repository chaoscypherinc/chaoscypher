# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""GraphRAG retrieval defects found by the 2026-10-06 benchmark audit (R2-R6), pinned.

R3: nodes and templates took hybrid's chunk slots. R4: templates were seeds,
and seeds outside the graph ran PageRank unpersonalised. R5: hybrid's top-k
was not kept whole in graphrag's chunks. R6: render-only levers changed what
was retrieved. R2: keyword seeds never fired.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import SQLModel
from structlog.testing import capture_logs

from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
from chaoscypher_core.adapters.sqlite.repos.search import SearchRepository
from chaoscypher_core.models import Node
from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
    GraphRAGToolHandlers,
)
from chaoscypher_core.settings import EngineSettings
from tests.fakes.graph_edges import EdgesByNode
from tests.fakes.graphrag_world import pin_pagerank


DIM = 4
QUERY_VECTOR = [1.0, 0.0, 0.0, 0.0]


def _settings(**graphrag: Any) -> EngineSettings:
    """Engine settings with GraphRAG levers set (and validated) as an override would."""
    settings = EngineSettings()
    for key, value in graphrag.items():
        setattr(settings.graphrag, key, value)
    return settings


def _chunk_row(chunk_id: str) -> dict[str, Any]:
    """A stored chunk as ``get_chunks_by_ids_batch`` returns it."""
    return {
        "id": chunk_id,
        "content": f"Text of {chunk_id}. A second sentence.",
        "source_id": "s1",
        "database_name": "db",
        "chunk_index": 0,
    }


class _Indexing:
    """Hydrates any chunk id, in input order."""

    def get_chunks_by_ids_batch(self, ids: list[str], **_: Any) -> list[dict[str, Any]]:
        return [_chunk_row(i) for i in ids]


async def _embed(_text: str) -> dict[str, list[float]]:
    return {"embedding": QUERY_VECTOR}


@pytest.fixture
def repo(tmp_path: Path) -> Any:
    """A real SearchRepository over a fresh app.db."""
    path = tmp_path / "app.db"
    engine = get_engine(str(path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    yield SearchRepository(engine, vector_dim=DIM, embedding_model="m")
    evict_engine(path)


# --------------------------------------------------------------------------- R3


@pytest.mark.asyncio
async def test_the_chunk_budget_is_filled_when_nodes_and_templates_score_higher(
    repo: SearchRepository,
) -> None:
    """chunk_limit chunks come back although every node and template is closer to the query."""
    repo.index_embeddings_batch([(f"n{i}", QUERY_VECTOR) for i in range(20)], item_type="node")
    for i in range(20):
        repo.index_template(f"t{i}", QUERY_VECTOR)
    repo.index_embeddings_batch(
        [(f"chunk:c{i}", [1.0, 0.3 + i / 50, 0.0, 0.0]) for i in range(15)], item_type="chunk"
    )
    handler = GraphRAGToolHandlers(
        graph_repository=MagicMock(),
        search_repository=repo,
        indexing_repository=_Indexing(),  # type: ignore[arg-type]
        embedding_callback=_embed,
        settings=_settings(chunk_limit=10),
    )
    chunks = await handler.hybrid_chunk_search("a question", limit=10)
    assert [c["chunk_id"] for c in chunks] == [f"c{i}" for i in range(10)]


# --------------------------------------------------------------------------- R4


def test_seeds_are_graph_nodes_filtered_before_the_cut() -> None:
    """Template and chunk hits never take a seed slot, and the search asks for nodes only."""
    search = MagicMock()
    search.vector_search.return_value = [
        *[(f"template:t{i}", 0.99) for i in range(5)],
        ("chunk:c1", 0.98),
        *[(f"n{i}", 0.9 - i / 100) for i in range(12)],
    ]
    handler = GraphRAGToolHandlers(MagicMock(), search, settings=_settings())
    seeds = handler._match_seed_entities(QUERY_VECTOR, seed_limit=10)
    assert [s for s, _ in seeds] == [f"n{i}" for i in range(10)]
    assert search.vector_search.call_args.kwargs["item_type"] == "node"


def test_templates_closer_than_every_node_still_leave_the_seeds_to_nodes(
    repo: SearchRepository,
) -> None:
    """Over the real index: relation-type template vectors are not in the seed search space."""
    for i in range(5):
        repo.index_template(f"rel{i}", QUERY_VECTOR)
    repo.index_embeddings_batch([("n-pierre", [1.0, 0.5, 0.0, 0.0])], item_type="node")
    handler = GraphRAGToolHandlers(MagicMock(), repo, settings=_settings())
    assert [s for s, _ in handler._match_seed_entities(QUERY_VECTOR, seed_limit=10)] == ["n-pierre"]


def test_seeds_outside_the_graph_fall_back_explicitly_instead_of_global_pagerank() -> None:
    """No seed among the loaded nodes: no PageRank at all, and a warning says so."""
    graph = MagicMock()
    graph.list_nodes_minimal.return_value = [SimpleNamespace(id="a", label="A", source_id=None)]
    graph.list_edges_minimal.return_value = []
    handler = GraphRAGToolHandlers(graph, MagicMock(), settings=_settings())
    graph_lists = handler._load_graph(None)
    with capture_logs() as logs:
        scores = handler._run_ppr([("template:t1", 0.9), ("gone", 0.8)], *graph_lists, None)
    assert scores == {}
    assert any(e["event"] == "graphrag_seeds_not_in_graph" for e in logs)
    assert handler._run_ppr([("a", 0.9)], *graph_lists, None) == {"a": pytest.approx(1.0)}


# --------------------------------------------------------------------------- R2


@pytest.mark.asyncio
async def test_keyword_seeds_produce_seeds_for_a_name_the_question_spells(
    repo: SearchRepository,
) -> None:
    """keyword_seeds=true seeds the node the question names, with no vector at all."""
    dolokhov = Node(
        id="n-dolokhov",
        label="Dolokhov",
        template_id="t",
        properties={"definition": "an officer"},
    )
    natasha = Node(
        id="n-natasha",
        label="Natasha",
        template_id="t",
        properties={"definition": "she heard about the bet Dolokhov won"},
    )
    repo.index_node(dolokhov)
    repo.index_node(natasha)
    graph = MagicMock()
    graph.get_nodes_batch.side_effect = lambda ids, **_: [
        n for n in (dolokhov, natasha) if n.id in ids
    ]
    handler = GraphRAGToolHandlers(graph, repo, settings=_settings(keyword_seeds=True))
    seeds = handler._add_keyword_seeds("What bet did Dolokhov win?", [], seed_limit=10)
    # Natasha's description names Dolokhov, her name does not: only Dolokhov seeds
    assert seeds == [("n-dolokhov", 1.0)]


# --------------------------------------------------------------------------- R5


def _chunk(cid: str, origin: str) -> dict[str, Any]:
    """A merged chunk with its fusion origin."""
    return {"chunk_id": cid, "retrieval_origin": origin, "content": f"[CHUNK C0 | f]\n{cid}"}


def test_the_reserve_keeps_hybrids_whole_top_k_even_against_chunks_both_arms_found() -> None:
    """With reserve == budget, hybrid's top-k is a subset of the kept chunks (audit R5).

    Two chunks both arms found sit high in the fused list but low in the hybrid
    arm (ranks 11 and 12); the old reserve could not evict them and dropped two
    of hybrid's top ten.
    """
    hybrid = [f"h{i}" for i in range(1, 11)] + ["b1", "b2"]
    provenance = ["b1", "b2", *[f"p{i}" for i in range(1, 9)]]
    handler = GraphRAGToolHandlers(
        MagicMock(), MagicMock(), settings=_settings(vector_reserve=10, rank_chunks_by_seeds=False)
    )
    merged = handler._merge_and_rank(
        [],
        [{"chunk_id": c, "score": 1.0, "content": f"[CHUNK C0 | f]\n{c}"} for c in provenance],
        [{"chunk_id": c, "score": 0.5, "content": f"[CHUNK C0 | f]\n{c}"} for c in hybrid],
        limit=10,
    )
    assert {c["chunk_id"] for c in merged} == set(hybrid[:10])

    # the same guarantee on the reserve step alone, at a partial reserve
    kept = [_chunk("b1", "both"), _chunk("h1", "vector"), _chunk("p1", "provenance")]
    rest = [_chunk("h2", "vector"), _chunk("h3", "vector")]
    out = GraphRAGToolHandlers._reserve_hybrid_hits(kept, rest, ["h1", "h2", "h3", "b1"], 3)
    assert [c["chunk_id"] for c in out] == ["h1", "h2", "h3"]


# --------------------------------------------------------------------------- R6


_FIRST = (
    "A hostess of the capital whose evening gatherings were attended by every diplomat and "
    "courtier worth knowing, and feared."
)  # about 125 characters, no word of the question
NODES = {
    "S": SimpleNamespace(
        id="S", label="Prince Vasili", template_id="p", properties={"aliases": ["Vasili"]}
    ),
    "R1": SimpleNamespace(
        id="R1",
        label="Anna Pavlovna",
        template_id="p",
        properties={
            "definition": f"{_FIRST} Natasha came to her soiree with her mother and sister Vera.",
            "aliases": ["Annette"],
        },
    ),
    "R2": SimpleNamespace(
        id="R2",
        label="Hélène",
        template_id="p",
        properties={"definition": "the daughter of a prince."},
    ),
    "R4": SimpleNamespace(
        id="R4", label="Natasha Rostova", template_id="p", properties={"definition": "a girl."}
    ),
}
EDGES = [
    SimpleNamespace(source_node_id="S", label="knows", target_node_id=t) for t in ("R1", "R2", "R4")
]
PPR = {"S": 0.5, "R1": 0.4, "R2": 0.3, "R4": 0.2}


class _Graph(EdgesByNode):
    def list_nodes_minimal(self, **_: Any) -> list[Any]:
        return list(NODES.values())

    def list_edges_minimal(self, **_: Any) -> list[Any]:
        return list(EDGES)

    def list_edges(self, **kw: Any) -> list[Any]:
        if "source_node_id" in kw:
            return [e for e in EDGES if e.source_node_id == kw["source_node_id"]]
        return [e for e in EDGES if e.target_node_id == kw["target_node_id"]]

    def get_nodes_batch(self, ids: list[str], **_: Any) -> list[Any]:
        return [NODES[i] for i in ids if i in NODES]

    def get_template(self, _tid: str) -> None:
        return None


async def _retrieve(**levers: Any) -> dict[str, Any]:
    """graphrag_search over the fixed graph, one chunk cited per entity, a budget of two."""
    search = MagicMock()
    search.vector_search.return_value = [("S", 0.9)]
    search.keyword_search.return_value = []
    search.hybrid_search = AsyncMock(return_value=[])
    storage = MagicMock()
    storage.get_citations_batch.side_effect = lambda _db, entity_uris, **_: [
        {"chunk_id": f"c{uri}", "entity_uri": uri} for uri in entity_uris
    ]
    handler = GraphRAGToolHandlers(
        graph_repository=_Graph(),  # type: ignore[arg-type]
        search_repository=search,
        indexing_repository=_Indexing(),  # type: ignore[arg-type]
        source_storage=storage,
        embedding_callback=_embed,
        settings=_settings(chunk_limit=2, vector_reserve=0, **levers),
        database_name="db",
    )
    pin_pagerank(handler, PPR)
    return await handler.graphrag_search("Who did Natasha meet with Prince Vasili?")


def _ids(result: dict[str, Any]) -> tuple[set[str], set[str], list[str]]:
    """Seed ids, related ids, chunk ids (in order) of a retrieval."""
    gc = result["graph_context"]
    return (
        {e["id"] for e in gc["seed_entities"]},
        {e["id"] for e in gc["related_entities"]},
        [c["chunk_id"] for c in result["chunks"]],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lever",
    ["entity_aliases", "clean_descriptions", "relationship_evidence", "question_entities_first"],
)
async def test_render_only_levers_leave_every_retrieved_id_unchanged(lever: str) -> None:
    """Toggling a render-only lever keeps the seed, related and chunk ids (audit R6).

    ``clean_descriptions`` ends Anna Pavlovna's line before the sentence naming
    Natasha: the gate used to read that line and drop her. ``question_entities_first``
    puts Natasha Rostova first: the provenance lookup used to follow that order
    and fetch another chunk into the budget of two.
    """
    baseline = _ids(await _retrieve())
    assert baseline == ({"S"}, {"R1", "R2", "R4"}, ["cR1", "cR2"])
    flipped = not getattr(EngineSettings().graphrag, lever)  # clean_descriptions is on by default
    assert _ids(await _retrieve(**{lever: flipped})) == baseline
