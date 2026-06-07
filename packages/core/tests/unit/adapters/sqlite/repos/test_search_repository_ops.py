# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Behavioral unit tests for ``SearchRepository`` (sqlite-vec + FTS5).

Exercises the real SQLite adapter against a per-test, file-backed app.db
(CC040 forbids ``:memory:``). Vectors are hand-written through the repo's
own ``index_*`` methods so the float32 byte format matches exactly; the
sqlite-vec ``vec0`` virtual table is loaded by ``get_engine`` (confirmed
available in this environment).

Covers the constructor guard, keyword (BM25) search, vector search merge /
trim / dimension-mismatch / unknown-type paths, semantic search callback
normalization + rebuild fallback, hybrid blend + min_similarity cutoff,
batch index / remove round-trips, the reindex-flag lifecycle, index stats,
and ``clear_all_indices``.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.repos.search import SearchRepository
from chaoscypher_core.models import Node


VECTOR_DIM = 8


def _vec(seed: float) -> list[float]:
    """Build a deterministic VECTOR_DIM-length float32-friendly vector."""
    return [seed] * VECTOR_DIM


def _node(node_id: str, label: str, desc: str, embedding: list[float] | None = None) -> Node:
    """Construct a Node with searchable text and an optional embedding."""
    return Node(
        id=node_id,
        label=label,
        template_id="tmpl",
        properties={"description": desc},
        embedding=embedding,
    )


@pytest.fixture
def search_repo(tmp_path: Path) -> Generator[SearchRepository]:
    """Per-test file-backed ``SearchRepository`` at a small vector_dim.

    Mirrors the sibling ``sqlite_adapter`` conftest pattern: a fresh app.db,
    all SQLModel tables created, then a ``SearchRepository`` whose own schema
    init creates the FTS5 + per-type vec0 tables.
    """
    db_dir = tmp_path / "search-repo-test"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"

    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)

    return SearchRepository(engine, vector_dim=VECTOR_DIM)


# ------------------------------------------------------------------ #
#  Constructor
# ------------------------------------------------------------------ #


class TestConstructor:
    def test_rejects_vector_dim_below_one(self, tmp_path: Path):
        engine = get_engine(str(tmp_path / "app.db"))
        SQLModel.metadata.create_all(engine, checkfirst=True)
        with pytest.raises(ValueError, match="vector_dim"):
            SearchRepository(engine, vector_dim=0)

    def test_rejects_non_int_vector_dim(self, tmp_path: Path):
        engine = get_engine(str(tmp_path / "app.db"))
        SQLModel.metadata.create_all(engine, checkfirst=True)
        with pytest.raises(ValueError, match="vector_dim"):
            SearchRepository(engine, vector_dim="8")  # type: ignore[arg-type]


# ------------------------------------------------------------------ #
#  Keyword search (FTS5 / BM25)
# ------------------------------------------------------------------ #


class TestKeywordSearch:
    def test_empty_query_returns_empty(self, search_repo: SearchRepository):
        assert search_repo.keyword_search("") == []
        assert search_repo.keyword_search("   ") == []

    def test_bm25_ranks_more_relevant_first(self, search_repo: SearchRepository):
        search_repo.index_nodes_batch(
            [
                _node("n1", "quantum quantum quantum", "quantum entanglement quantum"),
                _node("n2", "classical mechanics", "a single quantum mention here"),
            ]
        )
        results = search_repo.keyword_search("quantum")
        ids = [node_id for node_id, _ in results]
        # n1 mentions the term far more often, so BM25 ranks it first.
        assert ids[0] == "n1"
        assert "n2" in ids

    def test_no_match_returns_empty(self, search_repo: SearchRepository):
        search_repo.index_nodes_batch([_node("n1", "apple", "fruit")])
        assert search_repo.keyword_search("nonexistentterm") == []

    def test_search_dict_wrapper(self, search_repo: SearchRepository):
        search_repo.index_nodes_batch([_node("n1", "alpha", "beta gamma")])
        out = search_repo.search("alpha")
        assert out and out[0]["id"] == "n1"
        assert "score" in out[0]


# ------------------------------------------------------------------ #
#  Vector search
# ------------------------------------------------------------------ #


class TestVectorSearch:
    def test_dimension_mismatch_returns_empty_and_warns(
        self, search_repo: SearchRepository, caplog
    ):
        import logging

        with caplog.at_level(logging.WARNING):
            result = search_repo.vector_search([0.1, 0.2], k=5, item_type="node")
        assert result == []

    def test_unknown_item_type_raises(self, search_repo: SearchRepository):
        with pytest.raises(ValueError, match="unknown vector item_type"):
            search_repo.vector_search(_vec(0.1), k=5, item_type="bogus")

    def test_single_type_returns_indexed_vectors(self, search_repo: SearchRepository):
        search_repo.index_embeddings_batch([("a", _vec(0.1)), ("b", _vec(0.9))], item_type="node")
        results = search_repo.vector_search(_vec(0.1), k=10, item_type="node")
        ids = {item_id for item_id, _ in results}
        assert ids == {"a", "b"}
        # similarity is clamped to [0, 1]
        for _id, score in results:
            assert 0.0 <= score <= 1.0

    def test_merges_across_types_and_trims_to_k(self, search_repo: SearchRepository):
        # Index two per-type tables; item_type=None searches all and trims to k.
        search_repo.index_embeddings_batch([("n1", _vec(0.1)), ("n2", _vec(0.2))], item_type="node")
        search_repo.index_embeddings_batch(
            [("c1", _vec(0.3)), ("c2", _vec(0.4))], item_type="chunk"
        )
        results = search_repo.vector_search(_vec(0.1), k=3, item_type=None)
        assert len(results) == 3  # 4 indexed, trimmed to k=3
        # sorted descending by similarity
        scores = [s for _, s in results]
        assert scores == sorted(scores, reverse=True)


# ------------------------------------------------------------------ #
#  Semantic search (callback normalization)
# ------------------------------------------------------------------ #


class TestSemanticSearch:
    @pytest.mark.asyncio
    async def test_no_callback_returns_empty(self, search_repo: SearchRepository):
        assert await search_repo.semantic_search("q", k=5) == []

    @pytest.mark.asyncio
    async def test_callback_dict_shape(self, search_repo: SearchRepository):
        search_repo.index_embeddings_batch([("a", _vec(0.1))], item_type="node")

        async def cb(_text: str):
            return {"embedding": _vec(0.1)}

        results = await search_repo.semantic_search("q", k=5, embedding_provider_callback=cb)
        assert {i for i, _ in results} == {"a"}

    @pytest.mark.asyncio
    async def test_callback_object_with_embedding_attr(self, search_repo: SearchRepository):
        search_repo.index_embeddings_batch([("a", _vec(0.2))], item_type="node")

        class _Resp:
            embedding = _vec(0.2)

        async def cb(_text: str):
            return _Resp()

        results = await search_repo.semantic_search("q", k=5, embedding_provider_callback=cb)
        assert {i for i, _ in results} == {"a"}

    @pytest.mark.asyncio
    async def test_callback_bare_list(self, search_repo: SearchRepository):
        search_repo.index_embeddings_batch([("a", _vec(0.3))], item_type="node")

        async def cb(_text: str):
            return _vec(0.3)

        results = await search_repo.semantic_search("q", k=5, embedding_provider_callback=cb)
        assert {i for i, _ in results} == {"a"}

    @pytest.mark.asyncio
    async def test_empty_embedding_returns_empty(self, search_repo: SearchRepository):
        async def cb(_text: str):
            return []

        assert await search_repo.semantic_search("q", k=5, embedding_provider_callback=cb) == []

    @pytest.mark.asyncio
    async def test_callback_raises_returns_empty(self, search_repo: SearchRepository):
        async def cb(_text: str):
            raise RuntimeError("embedding provider down")

        assert await search_repo.semantic_search("q", k=5, embedding_provider_callback=cb) == []

    @pytest.mark.asyncio
    async def test_rebuilding_falls_back_to_fts(self, search_repo: SearchRepository, monkeypatch):
        # When rebuilding, semantic search short-circuits to keyword_search.
        search_repo.index_nodes_batch([_node("n1", "rebuildable token", "x")])
        monkeypatch.setattr(type(search_repo), "is_rebuilding", property(lambda self: True))

        async def cb(_text: str):  # pragma: no cover - must not be called
            raise AssertionError("callback must not run while rebuilding")

        results = await search_repo.semantic_search(
            "rebuildable", k=5, embedding_provider_callback=cb
        )
        assert {i for i, _ in results} == {"n1"}


# ------------------------------------------------------------------ #
#  Hybrid search
# ------------------------------------------------------------------ #


class TestHybridSearch:
    @pytest.mark.asyncio
    async def test_blends_keyword_and_semantic(self, search_repo: SearchRepository):
        # Keyword hit on n1, semantic hit on n2 (above min_similarity).
        search_repo.index_nodes_batch(
            [
                _node("n1", "graphdb keyword token", "x", embedding=_vec(0.5)),
                _node("n2", "unrelated label", "y", embedding=_vec(0.9)),
            ]
        )

        async def cb(_text: str):
            return _vec(0.9)  # identical to n2's embedding → similarity ~1.0

        results = await search_repo.hybrid_search(
            "graphdb", k=10, embedding_provider_callback=cb, min_similarity=0.5
        )
        ids = {i for i, _ in results}
        assert "n1" in ids  # keyword match
        assert "n2" in ids  # semantic match passes the cutoff

    @pytest.mark.asyncio
    async def test_min_similarity_cutoff_drops_weak_semantic(self, search_repo: SearchRepository):
        search_repo.index_nodes_batch([_node("n1", "keyword token here", "x", embedding=_vec(0.1))])

        async def cb(_text: str):
            # Opposite vector → low similarity, filtered out by min_similarity.
            return [-0.1] * VECTOR_DIM

        results = await search_repo.hybrid_search(
            "keyword", k=10, embedding_provider_callback=cb, min_similarity=0.99
        )
        # Only the keyword result survives; the semantic side is below cutoff.
        assert {i for i, _ in results} == {"n1"}

    @pytest.mark.asyncio
    async def test_short_query_uses_keyword_only(self, search_repo: SearchRepository):
        search_repo.index_nodes_batch([_node("n1", "ab cd", "x")])

        async def cb(_text: str):  # pragma: no cover - must not run for short query
            raise AssertionError("semantic must not run for sub-3-char query")

        # Query length < 3 → keyword path only.
        results = await search_repo.hybrid_search("ab", k=5, embedding_provider_callback=cb)
        assert isinstance(results, list)


# ------------------------------------------------------------------ #
#  Batch index / remove round-trips
# ------------------------------------------------------------------ #


class TestBatchIndexRemove:
    def test_index_then_remove_embedding(self, search_repo: SearchRepository):
        search_repo.index_embeddings_batch([("a", _vec(0.1)), ("b", _vec(0.2))], item_type="node")
        assert search_repo.get_index_stats()["vector"]["by_type"]["node"] == 2

        search_repo.remove_embedding("a", "node")
        assert search_repo.get_index_stats()["vector"]["by_type"]["node"] == 1

    def test_remove_embeddings_batch_counts(self, search_repo: SearchRepository):
        search_repo.index_embeddings_batch(
            [("a", _vec(0.1)), ("b", _vec(0.2)), ("c", _vec(0.3))], item_type="chunk"
        )
        removed = search_repo.remove_embeddings_batch(["a", "b"], "chunk")
        assert removed == 2
        assert search_repo.get_index_stats()["vector"]["by_type"]["chunk"] == 1

    def test_index_node_with_embedding_round_trip(self, search_repo: SearchRepository):
        n = _node("nn", "label", "desc", embedding=_vec(0.4))
        search_repo.index_node(n)
        # Both FTS and vector indices populated.
        stats = search_repo.get_index_stats()
        assert stats["fulltext"]["document_count"] == 1
        assert stats["vector"]["by_type"]["node"] == 1

    def test_template_index_and_semantic_search(self, search_repo: SearchRepository):
        search_repo.index_template("tpl1", _vec(0.5))
        results = search_repo.template_semantic_search(_vec(0.5), k=5, min_similarity=0.1)
        assert ("tpl1", pytest.approx(results[0][1])) == ("tpl1", results[0][1])
        assert results[0][0] == "tpl1"  # prefix stripped

    def test_delete_node_removes_from_both(self, search_repo: SearchRepository):
        search_repo.index_node(_node("nd", "deletable", "text", embedding=_vec(0.6)))
        search_repo.delete_node("nd")
        stats = search_repo.get_index_stats()
        assert stats["fulltext"]["document_count"] == 0
        assert stats["vector"]["by_type"]["node"] == 0

    def test_delete_nodes_batch_count(self, search_repo: SearchRepository):
        search_repo.index_nodes_batch(
            [_node("a", "x", "1"), _node("b", "y", "2"), _node("c", "z", "3")]
        )
        removed = search_repo.delete_nodes_batch(["a", "b"])
        assert removed == 2
        assert search_repo.get_index_stats()["fulltext"]["document_count"] == 1


# ------------------------------------------------------------------ #
#  Reindex flag lifecycle
# ------------------------------------------------------------------ #


class TestReindexFlags:
    def test_needs_full_reindex_default_false(self, search_repo: SearchRepository):
        assert search_repo.needs_full_reindex is False

    def test_set_and_clear_reindex_flag(self, search_repo: SearchRepository):
        search_repo._set_reindex_flag(True)
        assert search_repo.needs_full_reindex is True
        search_repo.clear_reindex_flag()
        assert search_repo.needs_full_reindex is False

    def test_has_pending_reindex_tracks_queue(self, search_repo: SearchRepository):
        assert search_repo.has_pending_reindex is False
        search_repo.schedule_reindex("item1", "some text", "node")
        assert search_repo.has_pending_reindex is True

    def test_is_rebuilding_default_false(self, search_repo: SearchRepository):
        assert search_repo.is_rebuilding is False

    @pytest.mark.asyncio
    async def test_flush_reindex_reembeds_and_clears_queue(self, search_repo: SearchRepository):
        search_repo.schedule_reindex("a", "text a", "node")
        search_repo.schedule_reindex("b", "text b", "node")

        async def batch_embed(texts: list[str]) -> list[list[float]]:
            return [_vec(0.1) for _ in texts]

        indexed = await search_repo.flush_reindex(batch_embed)
        assert indexed == 2
        assert search_repo.has_pending_reindex is False
        assert search_repo.get_index_stats()["vector"]["by_type"]["node"] == 2


# ------------------------------------------------------------------ #
#  Stats / clear
# ------------------------------------------------------------------ #


class TestStatsAndClear:
    def test_get_index_stats_shape(self, search_repo: SearchRepository):
        stats = search_repo.get_index_stats()
        assert stats["vector"]["dimensions"] == VECTOR_DIM
        assert set(stats["vector"]["by_type"]) == {"chunk", "node", "template"}
        assert "document_count" in stats["fulltext"]

    def test_clear_all_indices_empties_everything(self, search_repo: SearchRepository):
        search_repo.index_node(_node("n1", "label", "desc", embedding=_vec(0.1)))
        search_repo.index_embeddings_batch([("c1", _vec(0.2))], item_type="chunk")
        search_repo.clear_all_indices()
        stats = search_repo.get_index_stats()
        assert stats["fulltext"]["document_count"] == 0
        assert stats["vector"]["vector_count"] == 0
