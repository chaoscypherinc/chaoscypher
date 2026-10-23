# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""GraphRAG retrieval defects found by the second benchmark audit wave (2026-10-06), pinned.

Keyword seeds matched titles by substring ("prince" in "princess", 63 of 120
questions seeded someone through a title word) and never matched a
possessive; the reserve's behaviour at ``vector_reserve == chunk_limit`` was
undocumented; and a stage that swallowed an exception left a retrieval that
looked complete.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
from chaoscypher_core.adapters.sqlite.repos.search import SearchRepository
from chaoscypher_core.benchmark.retrieval import (
    DegradedRetrievalError,
    require_complete_retrieval,
)
from chaoscypher_core.models import Node
from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
    GraphRAGToolHandlers,
)
from chaoscypher_core.settings import EngineSettings
from tests.fakes.domain_tables import domain_conventions
from tests.fakes.graphrag_world import pin_pagerank


DIM = 4
LITERARY = domain_conventions("literary")
"""The literary domain's naming conventions, the ones a Book One graph is read by."""


def _settings(**graphrag: Any) -> EngineSettings:
    """Engine settings with GraphRAG levers set (and validated) as an override would."""
    settings = EngineSettings()
    for key, value in graphrag.items():
        setattr(settings.graphrag, key, value)
    return settings


def _node(nid: str, label: str, *aliases: str, definition: str = "") -> Node:
    return Node(
        id=nid,
        label=label,
        template_id="t",
        properties={"aliases": list(aliases), "definition": definition},
    )


CAST = [
    _node("vasili", "Prince Vasíli", "Vasíli Kurágin", definition="A courtier, Hélène's father."),
    _node("mary", "Princess Mary", "Mary", definition="Andrew's sister."),
    _node("andrew", "Prince Andrew", "Andrew", "Bolkónski", definition="The old prince's son."),
    _node("countess", "Countess Rostóva", definition="Natasha's mother."),
    _node("rostov", "Count Rostóv", "Ilyá Rostóv", definition="Natasha's father."),
    _node("lise", "Lise Meinen", "the little princess", definition="Andrew's wife."),
]


@pytest.fixture
def handler(tmp_path: Path) -> Any:
    """Handlers over a real search repository whose node index holds the cast."""
    path = tmp_path / "app.db"
    engine = get_engine(str(path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    repo = SearchRepository(engine, vector_dim=DIM, embedding_model="m")
    repo.index_nodes_batch(CAST)
    graph = MagicMock()
    by_id = {n.id: n for n in CAST}
    graph.get_nodes_batch.side_effect = lambda ids, **_: [by_id[i] for i in ids if i in by_id]
    yield GraphRAGToolHandlers(
        graph, repo, settings=_settings(keyword_seeds=True), name_conventions=LITERARY
    )
    evict_engine(path)


# --------------------------------------------------------------------------- keyword seeds


def test_a_question_naming_prince_vasili_does_not_seed_princess_mary(handler: Any) -> None:
    """The title "prince" is a whole word: it neither names Vasíli nor is in "princess"."""
    seeds = handler._add_keyword_seeds("What did Prince Vasíli want from Pierre?", [], 10)
    assert [nid for nid, _ in seeds] == ["vasili"]


def test_a_possessive_seeds_the_person_it_names(handler: Any) -> None:
    """The possessive "Andrew's" is the word "andrew" (the name rule's fold): Prince Andrew."""
    seeds = handler._add_keyword_seeds("What hung in Prince Andrew's study?", [], 10)
    assert [nid for nid, _ in seeds] == ["andrew"]


def test_a_title_word_alone_never_makes_a_keyword_seed_ahead_of_a_vector_seed(
    handler: Any,
) -> None:
    """The words "the count" and "the princess" name nobody: the vector seed stays first."""
    vector = [("natasha", 0.6)]
    seeds = handler._add_keyword_seeds("What did the count tell the princess?", vector, 10)
    assert seeds == vector


def test_an_alias_of_titles_alone_never_seeds(handler: Any) -> None:
    """The alias "the little princess" has no naming word: it names no one, quoted or not.

    Until wave 3 it matched as a whole phrase, which made the v3 node "the
    prince" a keyword seed for every question saying "the prince".
    """
    assert handler._add_keyword_seeds('Whom did "the little princess" marry?', [], 10) == []
    assert handler._add_keyword_seeds("Was the princess little?", [], 10) == []


def test_question_entities_first_uses_the_same_matcher() -> None:
    """The entities a question names are the keyword seeds' matches: titles never name anyone."""
    nodes = {n.id: n for n in CAST}
    graph = MagicMock()
    graph.list_edges.return_value = []
    graph.get_nodes_batch.side_effect = lambda ids, **_: [nodes[i] for i in ids if i in nodes]
    graph.get_template.return_value = None
    h = GraphRAGToolHandlers(
        graph,
        MagicMock(),
        settings=_settings(question_entities_first=True, question_gate=False),
        name_conventions=LITERARY,
    )
    ppr = {"mary": 0.5, "countess": 0.4, "vasili": 0.3, "andrew": 0.2}
    seeds = [("mary", 0.9), ("countess", 0.8), ("vasili", 0.7), ("andrew", 0.6)]
    context = h._assemble_graph_context(ppr, seeds, "Did Prince Vasíli visit Andrew's sister?")
    order = [e["id"] for e in context["seed_entities"]]
    # the two the question names first, in PageRank order; the princess and the
    # countess (whose titles hold "prince" and "count") after them
    assert order == ["vasili", "andrew", "mary", "countess"]


# --------------------------------------------------------------------------- the reserve


def _chunk_row(chunk_id: str) -> dict[str, Any]:
    return {"id": chunk_id, "content": f"Text of {chunk_id}.", "source_id": "s1"}


class _Indexing:
    def get_chunks_by_ids_batch(self, ids: list[str], **_: Any) -> list[dict[str, Any]]:
        return [_chunk_row(i) for i in ids]


async def _embed(_text: str) -> dict[str, list[float]]:
    return {"embedding": [1.0, 0.0, 0.0, 0.0]}


def _graph_handlers(**levers: Any) -> GraphRAGToolHandlers:
    """graphrag_search over one seed citing five chunks the hybrid arm never finds.

    The hybrid arm returns twenty chunks ``h1``..``h20`` (``k`` is twice the
    budget); the seed's provenance chunks are ``p1``..``p5``.
    """
    search = MagicMock()
    search.vector_search.return_value = [("S", 0.9)]
    search.keyword_search.return_value = []
    search.hybrid_search = AsyncMock(return_value=[(f"chunk:h{i}", 1.0) for i in range(1, 21)])
    graph = MagicMock()
    graph.list_edges.return_value = []
    graph.get_nodes_batch.return_value = [SimpleNamespace(id="S", label="Seed", template_id="t")]
    graph.get_template.return_value = None
    storage = MagicMock()
    storage.get_citations_batch.return_value = [
        {"chunk_id": f"p{i}", "entity_uri": "S"} for i in range(1, 6)
    ]
    handlers = GraphRAGToolHandlers(
        graph_repository=graph,
        search_repository=search,
        indexing_repository=_Indexing(),  # type: ignore[arg-type]
        source_storage=storage,
        embedding_callback=_embed,
        settings=_settings(rank_chunks_by_seeds=False, **levers),
        database_name="db",
    )
    pin_pagerank(handlers, {"S": 1.0})
    return handlers


@pytest.mark.asyncio
async def test_at_the_default_reserve_graphrag_returns_exactly_hybrids_chunks() -> None:
    """``vector_reserve`` 10 == ``chunk_limit`` 10: every slot is the hybrid arm's (decision recorded).

    The graph adds its entity and relationship lines, not chunks, while the
    hybrid arm fills the budget; the T=0 re-run measures this against 8.
    """
    defaults = EngineSettings().graphrag
    assert (defaults.vector_reserve, defaults.chunk_limit) == (10, 10)
    handlers = _graph_handlers()
    full = await handlers.graphrag_search("Who is the seed?")
    hybrid = await handlers.hybrid_chunk_search("Who is the seed?")
    assert {c["chunk_id"] for c in full["chunks"]} == {c["chunk_id"] for c in hybrid}
    assert {c["chunk_id"] for c in hybrid} == {f"h{i}" for i in range(1, 11)}
    assert full["graph_context"]["seed_entities"][0]["id"] == "S"


@pytest.mark.asyncio
async def test_below_the_budget_a_chunk_only_the_graph_found_enters() -> None:
    """At reserve 8 of 10 the two free slots go to fusion, where the seed's best citations rank."""
    full = await _graph_handlers(vector_reserve=8).graphrag_search("Who is the seed?")
    ids = [c["chunk_id"] for c in full["chunks"]]
    assert len(ids) == 10
    assert {f"h{i}" for i in range(1, 9)} <= set(ids)  # the reserve, whole
    assert {"p1", "p2"} <= set(ids)  # graph-only chunks in the budget
    assert next(c for c in full["chunks"] if c["chunk_id"] == "p1")["retrieval_origin"] == (
        "provenance"
    )


# --------------------------------------------------------------------------- swallowed faults


@pytest.mark.asyncio
async def test_a_swallowed_exception_is_named_on_the_stats_and_the_retrieval_is_refused() -> None:
    """A PageRank that raises falls back to vector_only, fills the budget, and says so."""
    handlers = _graph_handlers()
    del handlers._run_ppr  # the real one, over a graph whose node listing fails
    handlers.graph.list_nodes_minimal.side_effect = RuntimeError("database is locked")
    full = await handlers.graphrag_search("Who is the seed?")
    assert full["retrieval_stats"]["faults"] == ["ppr"]
    assert full["retrieval_stats"]["mode"] == "vector_only"
    assert len(full["chunks"]) == 10  # the chunk count alone would pass it
    with pytest.raises(DegradedRetrievalError, match="swallowed exceptions in ppr"):
        require_complete_retrieval(full, mode="graphrag", chunk_limit=10, indexed_chunks=100)


@pytest.mark.asyncio
async def test_a_fault_inside_the_search_repository_reaches_the_stats(tmp_path: Path) -> None:
    """The repository swallows a failed keyword query; the pipeline still names it."""
    path = tmp_path / "app.db"
    engine = get_engine(str(path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    repo = SearchRepository(engine, vector_dim=DIM, embedding_model="m")
    try:
        handlers = _graph_handlers()
        handlers.search = repo
        handlers._graphrag.keyword_seeds = True
        repo.keyword_search = MagicMock(wraps=repo.keyword_search)  # type: ignore[method-assign]
        with engine.begin() as conn:
            conn.exec_driver_sql("DROP TABLE fulltext_index")  # the node keyword query fails
        full = await handlers.graphrag_search("Who is the seed?")
        assert "keyword_search:node" in full["retrieval_stats"]["faults"]
        clean = await _graph_handlers().graphrag_search("Who is the seed?")
        assert clean["retrieval_stats"]["faults"] == []
        require_complete_retrieval(clean, mode="graphrag", chunk_limit=10, indexed_chunks=100)
    finally:
        evict_engine(path)
