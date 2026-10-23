# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""GraphRAG retrieval defects found by the third benchmark audit wave (2026-10-06), pinned.

A name of titles alone ("the prince") still seeded every question saying
"the prince" and ranked every chunk mentioning one; the matcher read names by
a title list of its own and dropped surname forms and plurals ("Rostóv" no
longer named ``Countess Rostóva`` or ``Rostóvs``); names under four letters
were dropped; the question gate matched substrings; and the hybrid rung was
unguarded.
"""

from __future__ import annotations

from pathlib import Path
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
from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    DEFAULT_NAME_CONVENTIONS,
    NameConventions,
)
from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
    GraphRAGToolHandlers,
    _naming_terms,
    _rank_by_seed_mentions,
    _shares_a_term,
)
from chaoscypher_core.settings import EngineSettings
from tests.fakes.domain_tables import domain_conventions


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
    _node("oldprince", "the prince", "the old prince", definition="The prince of Bald Hills."),
    _node("emperor", "Emperor Alexander", "the Emperor", definition="The Emperor of Russia."),
    _node("dowager", "Dowager Empress", definition="The Emperor's mother, a dowager empress."),
    _node("countess", "Countess Rostóva", definition="The mother of the family, a countess."),
    _node("rostovs", "Rostóvs", "the Rostóvs", definition="A rich family in Moscow."),
    _node("count", "Count Rostóv", "Ilyá Rostóv", definition="The father of the family."),
    _node("kuragins", "Kurágins", definition="A family of courtiers around Prince Vasíli."),
    _node("marya", "Márya Ivánovna Dólokhova", definition="The mother of Dólokhov."),
    _node("ann", "Ann", definition="A maid who carried letters."),
    _node("bob", "Bob Ward", definition="A groom who carried letters."),
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


def _seeded(handler: Any, question: str) -> list[str]:
    return [nid for nid, _ in handler._add_keyword_seeds(question, [], 10)]


# --------------------------------------------------------------------------- titles alone


def test_a_label_of_titles_alone_never_seeds(handler: Any) -> None:
    """The label "the prince" names no one: a question naming Prince Vasíli seeds Vasíli alone."""
    assert _seeded(handler, "What did Prince Vasíli want?") == ["vasili"]
    assert "oldprince" not in _seeded(handler, "What did the prince tell his son?")


def test_one_title_aliases_name_no_one(handler: Any) -> None:
    """The names "the Emperor" and "Dowager Empress" are titles: only "Alexander" names him."""
    assert _seeded(handler, "What did the Emperor say to the Dowager Empress?") == []
    assert _seeded(handler, "Whom did Emperor Alexander receive?") == ["emperor"]


def test_a_title_only_seed_does_not_reorder_the_chunks() -> None:
    """The real merge with ``rank_chunks_by_seeds``: "the prince" counts nothing in any chunk."""
    h = GraphRAGToolHandlers(
        MagicMock(),
        MagicMock(),
        settings=_settings(rank_chunks_by_seeds=True, vector_reserve=0),
        name_conventions=LITERARY,
    )
    chunks = [
        {"chunk_id": "a", "content": "Anna Pávlovna received her guests."},
        {"chunk_id": "b", "content": "The prince shouted at the old prince's son."},
        {"chunk_id": "c", "content": "It rained on the prince."},
    ]
    plain = h._merge_and_rank([], [], [dict(c) for c in chunks], 3)
    titled = h._merge_and_rank([], [], [dict(c) for c in chunks], 3, seed_labels=["the prince"])
    assert [c["chunk_id"] for c in titled] == [c["chunk_id"] for c in plain] == ["a", "b", "c"]
    named = h._merge_and_rank([], [], [dict(c) for c in chunks], 3, seed_labels=["Anna Pávlovna"])
    assert named[0]["chunk_id"] == "a"


# --------------------------------------------------------------------------- name forms


def test_a_family_name_seeds_its_plural_and_its_feminine_form(handler: Any) -> None:
    """The phrase "the Rostóv family" names ``Rostóvs``, ``Countess Rostóva`` and ``Count Rostóv``."""
    seeds = _seeded(handler, "What did the Rostóv family celebrate?")
    assert {"rostovs", "countess", "count"} <= set(seeds)


def test_a_surname_seeds_the_feminine_form_and_the_family(handler: Any) -> None:
    """The surname "Dólokhov" names Márya Ivánovna Dólokhova; "Kurágin" names the Kurágins."""
    assert "marya" in _seeded(handler, "Who is Dólokhov's mother?")
    assert "kuragins" in _seeded(handler, "What did Kurágin want for his son?")


def test_short_names_are_naming_terms() -> None:
    """Short words written as names (Ann, Bob) are terms; the short function words are not."""
    terms = _naming_terms("How did Ann meet Bob?", LITERARY)
    assert {"ann", "bob"} <= terms
    assert not {"how", "did"} & terms
    assert "her" not in _naming_terms("Her letters reached Ann.", LITERARY)


def test_a_short_name_seeds(handler: Any) -> None:
    assert set(_seeded(handler, "What did Ann give Bob?")) == {"ann", "bob"}


# --------------------------------------------------------------------------- the domain


class _Sources:
    """A source store whose rows name ``domains``."""

    def __init__(self, *domains: str | None) -> None:
        self.rows = [{"id": f"s{i}", "forced_domain": d} for i, d in enumerate(domains)]

    def list_sources(self, page: int = 1, page_size: int = 50) -> tuple[list[dict[str, Any]], int]:
        return self.rows, len(self.rows)


def _stack_conventions(*packs: str) -> NameConventions:
    """The floor's and ``packs``' conventions, read off the real registry."""
    from chaoscypher_core.services.sources.engine.extraction.domains import get_domain_registry
    from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack

    return DomainStack.resolve(get_domain_registry(EngineSettings()), list(packs)).conventions()


def test_the_conventions_are_the_domain_the_sources_were_extracted_under() -> None:
    """One pack on the rows: its conventions; none: the floor's; no source listing: the universal fallback."""
    literary = GraphRAGToolHandlers(MagicMock(), MagicMock(), source_storage=_Sources("literary"))
    assert literary.name_conventions == LITERARY == _stack_conventions("literary")
    for store in (_Sources(None), _Sources("generic", None)):
        handler = GraphRAGToolHandlers(MagicMock(), MagicMock(), source_storage=store)
        assert handler.name_conventions == _stack_conventions()
    for store in (MagicMock(), None):
        handler = GraphRAGToolHandlers(MagicMock(), MagicMock(), source_storage=store)
        assert handler.name_conventions == DEFAULT_NAME_CONVENTIONS


def test_mixed_packs_union_their_conventions() -> None:
    """Sources naming literary and historical: both packs' tables, the identity resolver's union.

    Until 2026-10-07 sources naming several domains fell back to the
    universal conventions, so the matcher read the names the resolver had
    read by literary's and historical's titles by neither.
    """
    from chaoscypher_core.services.sources.engine.extraction.domain_resolver import (
        conventions_of_sources,
    )

    store = _Sources("literary", "historical", None)
    handler = GraphRAGToolHandlers(MagicMock(), MagicMock(), source_storage=store)
    union = _stack_conventions("historical", "literary")
    assert handler.name_conventions == union
    assert handler.name_conventions == conventions_of_sources(store.rows, EngineSettings())
    assert handler.name_conventions != DEFAULT_NAME_CONVENTIONS
    assert {"character", "historical figure"} <= (union.person_types or frozenset())
    assert LITERARY.titles <= union.titles


def test_an_unreadable_source_store_is_a_fault() -> None:
    """A source listing that raises falls back to the universal conventions, and says so."""
    from chaoscypher_core.utils.retrieval_faults import collect_retrieval_faults

    store = MagicMock()
    store.list_sources.side_effect = RuntimeError("database is locked")
    handler = GraphRAGToolHandlers(MagicMock(), MagicMock(), source_storage=store)
    with collect_retrieval_faults() as faults:
        assert handler.name_conventions == DEFAULT_NAME_CONVENTIONS
    assert faults == ["name_conventions"]


def test_titles_are_the_domains_under_the_universal_fallback_prince_is_a_name() -> None:
    """The matcher reads titles as the domain does: a genre title is a name under the universal ones.

    Since audit wave 7 "prince" is a title of the universal floor every domain
    starts from; "dowager" is the literary domain's own.
    """
    assert "prince" not in _naming_terms("What did Prince Vasíli want?", LITERARY)
    assert "prince" not in _naming_terms("What did Prince Vasíli want?", DEFAULT_NAME_CONVENTIONS)
    assert "dowager" not in _naming_terms("What did the Dowager want?", LITERARY)
    assert "dowager" in _naming_terms("What did the Dowager want?", DEFAULT_NAME_CONVENTIONS)


# --------------------------------------------------------------------------- the question gate


def test_the_gate_matches_whole_words_with_plurals_folded() -> None:
    """The word "father" is not in "grandfather"; "brothers" is "brother"."""
    terms = {"father"}
    assert _shares_a_term("The father of Séverine.", terms, LITERARY)
    assert not _shares_a_term("Corvin's grandfather, a miller.", terms, LITERARY)
    assert _shares_a_term("He had two brothers.", {"brother"}, LITERARY)
    assert not _shares_a_term("anything", set(), LITERARY)


def test_the_gate_drops_an_entity_that_holds_the_word_only_inside_another() -> None:
    """The real assembly: the grandfather is not kept by a question about a father."""
    nodes = {
        "mira": _node("mira", "Princess Mira", definition="Corvin's sister."),
        "teodor": _node("teodor", "Teodór", definition="The father of Séverine."),
        "hesk": _node("hesk", "Hesk", definition="Corvin's grandfather, a miller."),
    }
    graph = MagicMock()
    graph.list_edges.return_value = []
    graph.get_nodes_batch.side_effect = lambda ids, **_: [nodes[i] for i in ids if i in nodes]
    graph.get_template.return_value = None
    h = GraphRAGToolHandlers(graph, MagicMock(), settings=_settings(), name_conventions=LITERARY)
    ppr = {"mira": 0.5, "teodor": 0.3, "hesk": 0.2}
    context = h._assemble_graph_context(ppr, [("mira", 0.9)], "Who is Mira's father?")
    assert [e["id"] for e in context["related_entities"]] == ["teodor"]


# --------------------------------------------------------------------------- the hybrid rung


def _chunk_rows(ids: list[str], **_: Any) -> list[dict[str, Any]]:
    return [{"id": i, "content": f"Text of {i}.", "source_id": "s1"} for i in ids]


@pytest.mark.asyncio
async def test_a_hybrid_retrieval_whose_keyword_search_raised_is_refused(tmp_path: Path) -> None:
    """With the chunk keyword index dropped the vector half still fills the budget: refused."""
    path = tmp_path / "app.db"
    engine = get_engine(str(path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    repo = SearchRepository(engine, vector_dim=DIM, embedding_model="m")
    try:
        repo.index_embeddings_batch(
            [(f"chunk:c{i}", [1.0, 0.1 * i, 0.0, 0.0]) for i in range(3)], item_type="chunk"
        )
        indexing = MagicMock()
        indexing.get_chunks_by_ids_batch.side_effect = _chunk_rows
        handlers = GraphRAGToolHandlers(
            MagicMock(),
            repo,
            indexing_repository=indexing,
            embedding_callback=AsyncMock(return_value={"embedding": [1.0, 0.0, 0.0, 0.0]}),
            settings=_settings(),
        )
        clean = await handlers.hybrid_retrieval("Who wrote the letter?", 3)
        assert clean["retrieval_stats"] == {
            "mode": "hybrid",
            "vector_chunks": 3,
            "total_chunks_returned": 3,
            "faults": [],
        }
        require_complete_retrieval(clean, mode="hybrid", chunk_limit=3, indexed_chunks=3)
        with engine.begin() as conn:
            conn.exec_driver_sql("DROP TABLE fulltext_index_chunks")
        degraded = await handlers.hybrid_retrieval("Who wrote the letter?", 3)
        assert len(degraded["chunks"]) == 3  # the count alone would pass it
        assert "keyword_search:chunk" in degraded["retrieval_stats"]["faults"]
        with pytest.raises(DegradedRetrievalError, match="hybrid retrieval is degraded"):
            require_complete_retrieval(degraded, mode="hybrid", chunk_limit=3, indexed_chunks=3)
        # the chunks-only list keeps its shape for the callers that render chunks alone
        listed = await handlers.hybrid_chunk_search("Who wrote the letter?", 3)
        assert [c["chunk_id"] for c in listed] == [c["chunk_id"] for c in degraded["chunks"]]
    finally:
        evict_engine(path)


def test_a_hybrid_record_of_another_mode_or_without_faults_is_refused() -> None:
    chunks = [{"chunk_id": "c1"}]
    for stats, why in (
        ({"mode": "vector_only", "faults": []}, "mode 'vector_only'"),
        ({"mode": "hybrid"}, "no fault record"),
    ):
        with pytest.raises(DegradedRetrievalError, match=why):
            require_complete_retrieval(
                {"chunks": chunks, "retrieval_stats": stats},
                mode="hybrid",
                chunk_limit=1,
                indexed_chunks=1,
            )
    # a chunks-only dict (hybrid_chunk_search's bare list) carries no record: refused too,
    # now that the CLI's live hybrid run and the MCP suite both build hybrid_retrieval's dict
    with pytest.raises(DegradedRetrievalError, match="no fault record"):
        require_complete_retrieval(
            {"chunks": chunks}, mode="hybrid", chunk_limit=1, indexed_chunks=1
        )
    require_complete_retrieval(
        {"chunks": chunks, "retrieval_stats": {"mode": "hybrid", "faults": []}},
        mode="hybrid",
        chunk_limit=1,
        indexed_chunks=1,
    )


def test_rank_by_seed_mentions_counts_whole_names_and_surnames() -> None:
    chunks = [
        {"chunk_id": "a", "text": "Count Rostóv slept."},
        {"chunk_id": "b", "text": "Natásha Rostóva and Count Rostóv danced."},
        {"chunk_id": "c", "text": "Natásha laughed."},
    ]
    ranked = _rank_by_seed_mentions(chunks, ["Natásha Rostóva", "the count"], LITERARY)
    assert [c["chunk_id"] for c in ranked] == ["b", "a", "c"]
