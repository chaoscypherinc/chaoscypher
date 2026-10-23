# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The GraphRAG handlers under audit wave 5.

- An NFD question split its words at the accents in every query tokenizer
  ("Bezúkhov" -> ``bezu``, ``khov``): 87 of 120 benchmark contexts changed.
- A source scope was applied to the hybrid arm after its cut, and the seeds'
  searches ignored it: a short scoped retrieval carried no fault.
- An unreadable ``needs_full_reindex`` flag read as an index in step.
"""

from __future__ import annotations

import unicodedata
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.adapters.sqlite.repos import search as search_repo
from chaoscypher_core.benchmark import retrieval_sample as rs
from chaoscypher_core.ports.search import SearchHits
from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    DEFAULT_NAME_CONVENTIONS,
)
from chaoscypher_core.services.workflows.tools.engine.handlers import graphrag_handlers as gh
from chaoscypher_core.utils.query_text import normalize_query
from chaoscypher_core.utils.retrieval_faults import (
    NEEDS_FULL_REINDEX_UNREADABLE,
    SCOPE_SHORT,
    collect_retrieval_faults,
)


_QUESTIONS = (
    "Who is Pierre Bezúkhov's father?",
    "What did Natásha Rostóva tell Princess Mary at Otrádnoe?",
    "Why did Фёдор Долохов go to the Bolkónskis' estate?",
    "Did Hélène Kurágina marry the Vicomte de Mortemart?",
    '"Anna Pávlovna Schérer" gave a soirée; who came?',
)


def _literary() -> Any:
    from chaoscypher_core.services.sources.engine.extraction.domain_resolver import (
        DomainResolver,
        domain_name_conventions,
    )
    from chaoscypher_core.settings import EngineSettings

    return domain_name_conventions("literary", DomainResolver(EngineSettings()))


_TOKENIZERS: dict[str, Any] = {
    "handler _query_words": gh._query_words,
    "handler _significant_terms": lambda q: sorted(
        gh._significant_terms(q, DEFAULT_NAME_CONVENTIONS)
    ),
    "handler _naming_terms (literary)": lambda q: sorted(gh._naming_terms(q, _literary())),
    "handler _text_keys": lambda q: sorted(gh._text_keys(q, DEFAULT_NAME_CONVENTIONS)),
    "handler _name_key_list": lambda q: gh._name_key_list(q, DEFAULT_NAME_CONVENTIONS),
    "search _fts5_query": search_repo._fts5_query,
    "search _name_rule_form": lambda q: [search_repo._name_rule_form(w) for w in q.split()],
    "search _tokenizer_form": lambda q: [search_repo._tokenizer_form(w) for w in q.split()],
}
"""Every function that splits a query into words, by where it lives (the keyword arm and the matcher)."""


@pytest.mark.parametrize("name", sorted(_TOKENIZERS))
@pytest.mark.parametrize("question", _QUESTIONS)
def test_every_query_tokenizer_reads_nfc_and_nfd_alike(name: str, question: str) -> None:
    nfd = unicodedata.normalize("NFD", question)
    assert nfd != question
    tokenize = _TOKENIZERS[name]
    assert tokenize(nfd) == tokenize(question)


def test_the_normaliser_composes_once_and_keeps_composed_text() -> None:
    composed = "Bezúkhov"
    assert normalize_query(unicodedata.normalize("NFD", composed)) == composed
    assert normalize_query(composed) is composed


def _sample_handlers(run: Any) -> tuple[Any, Any]:
    """The retrieval sample's world and the handlers of one of its runs (the engine to dispose)."""
    from chaoscypher_core.adapters.sqlite.repos.graph import GraphRepository

    engine = rs._memory_engine()
    rs._store_world(engine)
    search = rs._search_repository(engine)
    storage = rs._storage_adapter(engine)
    graph = GraphRepository(storage.session, "sample")
    rs._set_domain(engine, run.domain)
    return engine, rs._handlers(run, search, graph, storage, None)


@pytest.mark.parametrize(
    "run",
    [rs.RUNS[0], next(r for r in rs.RUNS if r.scope and not r.disabled and r.plain_graph)],
    ids=["defaults", "scoped"],
)
def test_a_decomposed_question_retrieves_the_composed_ones_context(run: Any) -> None:
    """End to end over the sample world: the same seeds, chunks and rendered context."""
    engine, handlers = _sample_handlers(run)
    scope = list(run.scope) if run.scope else None
    try:
        for question in rs.SAMPLE_QUESTIONS:
            nfd = unicodedata.normalize("NFD", question)
            composed = rs.run_without_loop(
                handlers.graphrag_search(query=question, source_ids=scope)
            )
            decomposed = rs.run_without_loop(handlers.graphrag_search(query=nfd, source_ids=scope))
            assert rs._canonical_graphrag(decomposed) == rs._canonical_graphrag(composed)
            assert rs._canonical_hybrid(handlers, nfd, scope) == rs._canonical_hybrid(
                handlers, question, scope
            )
    finally:
        engine.dispose()


def _handlers(search: Any, **kw: Any) -> gh.GraphRAGToolHandlers:
    indexing = MagicMock()
    indexing.get_chunks_by_ids_batch.side_effect = lambda ids: [
        {"id": cid, "content": f"text {cid}", "source_id": "a"} for cid in ids
    ]
    indexing.get_source_filenames_by_ids.side_effect = lambda ids, _db: dict.fromkeys(ids, "a.txt")
    return gh.GraphRAGToolHandlers(
        graph_repository=MagicMock(),
        search_repository=search,
        indexing_repository=indexing,
        name_conventions=DEFAULT_NAME_CONVENTIONS,
        **kw,
    )


@pytest.mark.asyncio
async def test_the_scope_reaches_the_hybrid_search() -> None:
    search = MagicMock()
    search.needs_full_reindex = False
    search.hybrid_search = AsyncMock(
        return_value=SearchHits([("chunk:c1", 0.9)], k=6, candidates_seen=1)
    )
    result = await _handlers(search).hybrid_retrieval("Kessel", 3, source_ids=["a"])
    assert search.hybrid_search.call_args.kwargs["source_ids"] == ["a"]
    assert result["retrieval_stats"]["faults"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("returned", "seen", "short"),
    [(1, 5, True), (1, 1, False), (3, 5, False), (2, 2, False), (0, 1, True), (0, 0, False)],
)
async def test_a_scoped_hybrid_arm_short_of_its_candidates_is_a_fault(
    returned: int, seen: int, *, short: bool
) -> None:
    """Fewer chunks than ``min(budget, candidates the search saw)``: recorded, so the benchmark refuses it.

    The candidates are the search's own (:class:`SearchHits`); until wave 6
    the handler compared with the chunks in the scope (``count_in_scope``),
    which ignores the similarity floor and keyword misses.
    """
    search = MagicMock()
    search.needs_full_reindex = False
    search.hybrid_search = AsyncMock(
        return_value=SearchHits(
            [(f"chunk:c{i}", 1.0 - i / 10) for i in range(returned)], k=6, candidates_seen=seen
        )
    )
    result = await _handlers(search).hybrid_retrieval("Kessel", 3, source_ids=["a"])
    assert (SCOPE_SHORT in result["retrieval_stats"]["faults"]) is short
    search.count_in_scope.assert_not_called()
    # unscoped, the benchmark's own completeness check counts the chunks
    unscoped = await _handlers(search).hybrid_retrieval("Kessel", 3)
    assert SCOPE_SHORT not in unscoped["retrieval_stats"]["faults"]


@pytest.mark.asyncio
async def test_a_search_that_does_not_say_what_it_saw_is_not_checked() -> None:
    search = MagicMock()
    search.needs_full_reindex = False
    search.hybrid_search = AsyncMock(return_value=[])
    result = await _handlers(search).hybrid_retrieval("Kessel", 3, source_ids=["a"])
    assert SCOPE_SHORT not in result["retrieval_stats"]["faults"]


def test_the_seed_searches_take_the_scope() -> None:
    search = MagicMock()
    search.vector_search.return_value = [("n1", 0.9)]
    search.keyword_search.return_value = []
    handlers = _handlers(search)
    handlers._match_seed_entities([0.1, 0.2], 5, ["a"])
    assert search.vector_search.call_args.kwargs["source_ids"] == ["a"]
    handlers._add_keyword_seeds("Who is Corvin?", [], 5, ["a"])
    assert search.keyword_search.call_args.kwargs["source_ids"] == ["a"]


class _UnreadableFlag:
    """A search repository whose ``needs_full_reindex`` read fails."""

    @property
    def needs_full_reindex(self) -> bool:
        raise RuntimeError("database is locked")


def test_an_unreadable_reindex_flag_is_a_fault() -> None:
    handlers = _handlers(_UnreadableFlag())
    with collect_retrieval_faults() as faults:
        handlers._check_index_ready()
    assert faults == [NEEDS_FULL_REINDEX_UNREADABLE]


@pytest.mark.asyncio
async def test_the_embedder_and_the_search_get_the_composed_query() -> None:
    asked: list[str] = []

    async def embed(text: str) -> dict[str, Any]:
        asked.append(text)
        return {"embedding": None}

    search = MagicMock()
    search.needs_full_reindex = False
    search.keyword_search.return_value = []
    search.hybrid_search = AsyncMock(return_value=[])
    handlers = _handlers(search, embedding_callback=embed)
    composed = "Who is Bezúkhov's father?"
    await handlers.graphrag_search(query=unicodedata.normalize("NFD", composed))
    await handlers.hybrid_retrieval(unicodedata.normalize("NFD", composed))
    assert asked == [composed]
    assert {call.args[0] for call in search.hybrid_search.call_args_list} == {composed}
    assert search.keyword_search.call_args.args[0] == composed
