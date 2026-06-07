# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for SearchService.

Covers hybrid_search, semantic_search, keyword_search, get_stats,
rebuild_indexes, and generate_embeddings. The underlying engine
SearchService is mocked at construction time so no real DB or LLM is
touched.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_cortex.features.search.models import (
    GenerateEmbeddingsResponse,
    RebuildIndexResponse,
    SearchResponse,
    SearchStatistics,
)
from chaoscypher_cortex.features.search.service import SearchService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(
    *,
    engine_mock: MagicMock | None = None,
    graph_repository: MagicMock | None = None,
    search_repository: MagicMock | None = None,
) -> tuple[SearchService, MagicMock]:
    """Return a SearchService with a mocked engine service.

    Patches EngineSearchService and build_engine_settings so the
    constructor does not require real adapters. Returns the service and
    the engine mock so tests can stub methods on it.
    """
    engine_mock = engine_mock or MagicMock()
    graph_repository = graph_repository or MagicMock()
    search_repository = search_repository or MagicMock()

    with (
        patch(
            "chaoscypher_cortex.features.search.service.EngineSearchService",
            return_value=engine_mock,
        ),
        patch(
            "chaoscypher_core.app_config.engine_factory.build_engine_settings",
            return_value=None,
        ),
    ):
        service = SearchService(
            search_repository=search_repository,
            graph_repository=graph_repository,
            indexing_repository=MagicMock(),
            source_repository=MagicMock(),
            sources_repository=MagicMock(),
            settings=None,
        )

    return service, engine_mock


def _chunk_result_dict(chunk_id: str = "chunk-1", score: float = 0.9) -> dict[str, Any]:
    """Return a minimal chunk result dict as produced by the engine."""
    return {
        "result_type": "chunk",
        "score": score,
        "chunk": {
            "chunk_id": chunk_id,
            "source_id": "src-1",
            "chunk_index": 0,
            "content": "hello world",
            "page_number": 1,
            "section": "intro",
            "filename": "doc.pdf",
        },
    }


def _node_result_dict(node_id: str = "node-1", score: float = 0.8) -> dict[str, Any]:
    """Return a minimal node result dict as produced by the engine."""
    return {
        "result_type": "node",
        "score": score,
        "node": {
            "id": node_id,
            "template_id": "tpl-1",
            "label": "Alice",
            "properties": {"name": "Alice"},
        },
    }


def _fake_node(
    node_id: str = "n-1",
    label: str = "Alice",
    properties: dict[str, Any] | None = None,
    embedding: list[float] | None = None,
) -> MagicMock:
    """Return a MagicMock simulating a core Node object."""
    node = MagicMock()
    node.id = node_id
    node.label = label
    node.properties = {"role": "engineer"} if properties is None else properties
    node.embedding = embedding
    return node


# ---------------------------------------------------------------------------
# TestKeywordSearch
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestKeywordSearch:
    """Tests for SearchService.keyword_search."""

    def test_returns_search_response_with_chunk_result(self) -> None:
        """keyword_search converts engine dict response into SearchResponse."""
        engine = MagicMock()
        engine.keyword_search.return_value = {
            "data": [_chunk_result_dict()],
            "type": "keyword",
        }
        service, _ = _make_service(engine_mock=engine)

        response = service.keyword_search("hello", limit=5)

        assert isinstance(response, SearchResponse)
        assert response.type == "keyword"
        assert len(response.data) == 1
        assert response.data[0].result_type == "chunk"
        assert response.data[0].chunk is not None
        assert response.data[0].chunk.chunk_id == "chunk-1"
        engine.keyword_search.assert_called_once_with("hello", limit=5)

    def test_returns_empty_response_when_engine_returns_no_results(self) -> None:
        """keyword_search returns empty data list when engine returns no results."""
        engine = MagicMock()
        engine.keyword_search.return_value = {"data": [], "type": "keyword"}
        service, _ = _make_service(engine_mock=engine)

        response = service.keyword_search("nothing")

        assert response.data == []
        assert response.type == "keyword"

    def test_uses_default_limit_when_not_provided(self) -> None:
        """keyword_search falls back to limit=10 when no settings and no limit."""
        engine = MagicMock()
        engine.keyword_search.return_value = {"data": [], "type": "keyword"}
        service, _ = _make_service(engine_mock=engine)

        service.keyword_search("query")

        engine.keyword_search.assert_called_once_with("query", limit=10)


# ---------------------------------------------------------------------------
# TestSemanticSearch
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSemanticSearch:
    """Tests for SearchService.semantic_search."""

    @pytest.mark.asyncio
    async def test_delegates_to_engine_with_callback(self) -> None:
        """semantic_search forwards the embedding callback to the engine."""
        engine = MagicMock()
        engine.semantic_search = AsyncMock(
            return_value={"data": [_node_result_dict()], "type": "semantic"}
        )
        service, _ = _make_service(engine_mock=engine)

        async def callback(_: str) -> list[float]:
            return [0.1, 0.2]

        response = await service.semantic_search(
            "hello", limit=3, embedding_provider_callback=callback
        )

        assert isinstance(response, SearchResponse)
        assert response.type == "semantic"
        assert response.data[0].result_type == "node"
        assert response.data[0].node is not None
        assert response.data[0].node.id == "node-1"
        engine.semantic_search.assert_awaited_once_with(
            "hello", limit=3, embedding_provider_callback=callback
        )

    @pytest.mark.asyncio
    async def test_returns_empty_response_when_no_results(self) -> None:
        """semantic_search returns empty data list on empty engine response."""
        engine = MagicMock()
        engine.semantic_search = AsyncMock(return_value={"data": [], "type": "semantic"})
        service, _ = _make_service(engine_mock=engine)

        response = await service.semantic_search("query")

        assert response.data == []
        assert response.type == "semantic"


# ---------------------------------------------------------------------------
# TestHybridSearch
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHybridSearch:
    """Tests for SearchService.hybrid_search."""

    @pytest.mark.asyncio
    async def test_forwards_min_similarity_and_limit(self) -> None:
        """hybrid_search forwards min_similarity and limit to the engine."""
        engine = MagicMock()
        engine.hybrid_search = AsyncMock(
            return_value={"data": [_chunk_result_dict()], "type": "hybrid"}
        )
        service, _ = _make_service(engine_mock=engine)

        async def callback(_: str) -> list[float]:
            return [0.5]

        response = await service.hybrid_search(
            "query", limit=7, embedding_provider_callback=callback, min_similarity=0.75
        )

        assert isinstance(response, SearchResponse)
        assert response.type == "hybrid"
        engine.hybrid_search.assert_awaited_once_with(
            "query",
            limit=7,
            embedding_provider_callback=callback,
            min_similarity=0.75,
        )

    @pytest.mark.asyncio
    async def test_converts_mixed_node_and_chunk_results(self) -> None:
        """hybrid_search converts both chunk and node result_types correctly."""
        engine = MagicMock()
        engine.hybrid_search = AsyncMock(
            return_value={
                "data": [_chunk_result_dict(), _node_result_dict()],
                "type": "hybrid",
            }
        )
        service, _ = _make_service(engine_mock=engine)

        response = await service.hybrid_search("query")

        assert len(response.data) == 2
        assert response.data[0].result_type == "chunk"
        assert response.data[0].chunk is not None
        assert response.data[1].result_type == "node"
        assert response.data[1].node is not None

    @pytest.mark.asyncio
    async def test_uses_default_min_similarity(self) -> None:
        """hybrid_search uses min_similarity=0.55 by default."""
        engine = MagicMock()
        engine.hybrid_search = AsyncMock(return_value={"data": [], "type": "hybrid"})
        service, _ = _make_service(engine_mock=engine)

        await service.hybrid_search("query")

        call_kwargs = engine.hybrid_search.await_args.kwargs
        assert call_kwargs["min_similarity"] == 0.55


# ---------------------------------------------------------------------------
# TestGetStats
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetStats:
    """Tests for SearchService.get_stats."""

    def test_flattens_engine_stats_into_pydantic_model(self) -> None:
        """get_stats flattens nested engine dict into SearchStatistics model."""
        engine = MagicMock()
        engine.get_stats.return_value = {
            "fulltext": {"document_count": 42},
            "vector": {"vector_count": 100, "dimensions": 768},
        }
        service, _ = _make_service(engine_mock=engine)

        stats = service.get_stats()

        assert isinstance(stats, SearchStatistics)
        assert stats.fulltext_doc_count == 42
        assert stats.vector_index_size == 100
        assert stats.vector_dimension == 768

    def test_returns_zeros_when_engine_stats_empty(self) -> None:
        """get_stats returns zero fields when engine returns empty dict."""
        engine = MagicMock()
        engine.get_stats.return_value = {}
        service, _ = _make_service(engine_mock=engine)

        stats = service.get_stats()

        assert stats.fulltext_doc_count == 0
        assert stats.vector_index_size == 0
        assert stats.vector_dimension == 0


# ---------------------------------------------------------------------------
# TestRebuildIndexes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRebuildIndexes:
    """Tests for SearchService.rebuild_indexes."""

    def test_returns_rebuild_response_from_engine(self) -> None:
        """rebuild_indexes wraps the engine dict in RebuildIndexResponse."""
        engine = MagicMock()
        engine.rebuild_indexes.return_value = {
            "success": True,
            "total_nodes": 10,
            "nodes_with_embeddings": 8,
            "chunks_indexed": 20,
            "message": "Rebuilt",
        }
        service, _ = _make_service(engine_mock=engine)

        response = service.rebuild_indexes()

        assert isinstance(response, RebuildIndexResponse)
        assert response.success is True
        assert response.total_nodes == 10
        assert response.nodes_with_embeddings == 8
        assert response.chunks_indexed == 20


# ---------------------------------------------------------------------------
# TestGenerateEmbeddings
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGenerateEmbeddings:
    """Tests for SearchService.generate_embeddings."""

    @pytest.mark.asyncio
    async def test_returns_zero_when_all_nodes_have_embeddings(self) -> None:
        """generate_embeddings returns processed_count=0 when no nodes need them."""
        graph_repo = MagicMock()
        graph_repo.count_nodes.return_value = 5
        graph_repo.list_nodes_without_embeddings.return_value = []

        service, _ = _make_service(graph_repository=graph_repo)

        response = await service.generate_embeddings()

        assert isinstance(response, GenerateEmbeddingsResponse)
        assert response.success is True
        assert response.total_nodes == 5
        assert response.processed_count == 0
        assert "already have embeddings" in response.message

    @pytest.mark.asyncio
    async def test_generates_embeddings_for_nodes_without_them(self) -> None:
        """generate_embeddings processes each node, updates it, and indexes it."""
        node_a = _fake_node("n-1", label="Alice")
        node_b = _fake_node("n-2", label="Bob")

        graph_repo = MagicMock()
        graph_repo.count_nodes.return_value = 2
        graph_repo.list_nodes_without_embeddings.return_value = [node_a, node_b]

        updated_node = MagicMock()
        updated_node.id = "n-1"
        updated_node.embedding = [0.1, 0.2, 0.3]
        graph_repo.update_node.return_value = updated_node

        search_repo = MagicMock()
        service, _ = _make_service(graph_repository=graph_repo, search_repository=search_repo)

        embedding_service = MagicMock()
        embedding_service.embed = AsyncMock(return_value=MagicMock(embedding=[0.1, 0.2, 0.3]))

        with patch(
            "chaoscypher_core.repo_factories.get_embedding_service",
            return_value=embedding_service,
        ):
            response = await service.generate_embeddings()

        assert response.success is True
        assert response.total_nodes == 2
        assert response.processed_count == 2
        assert graph_repo.update_node.call_count == 2
        assert search_repo.index_node_embedding.call_count == 2

    @pytest.mark.asyncio
    async def test_counts_failures_when_embedding_empty(self) -> None:
        """generate_embeddings increments failed_count when embedding is empty."""
        node = _fake_node("n-1")
        graph_repo = MagicMock()
        graph_repo.count_nodes.return_value = 1
        graph_repo.list_nodes_without_embeddings.return_value = [node]

        service, _ = _make_service(graph_repository=graph_repo)

        embedding_service = MagicMock()
        embedding_service.embed = AsyncMock(return_value=MagicMock(embedding=[]))

        with patch(
            "chaoscypher_core.repo_factories.get_embedding_service",
            return_value=embedding_service,
        ):
            response = await service.generate_embeddings()

        assert response.success is True
        assert response.processed_count == 0
        assert "1 failed" in response.message
        graph_repo.update_node.assert_not_called()

    @pytest.mark.asyncio
    async def test_counts_failures_when_update_returns_none(self) -> None:
        """generate_embeddings increments failed_count when update_node returns None."""
        node = _fake_node("n-1")
        graph_repo = MagicMock()
        graph_repo.count_nodes.return_value = 1
        graph_repo.list_nodes_without_embeddings.return_value = [node]
        graph_repo.update_node.return_value = None

        search_repo = MagicMock()
        service, _ = _make_service(graph_repository=graph_repo, search_repository=search_repo)

        embedding_service = MagicMock()
        embedding_service.embed = AsyncMock(return_value=MagicMock(embedding=[0.1, 0.2]))

        with patch(
            "chaoscypher_core.repo_factories.get_embedding_service",
            return_value=embedding_service,
        ):
            response = await service.generate_embeddings()

        assert response.processed_count == 0
        assert "1 failed" in response.message
        search_repo.index_node_embedding.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_during_embed_is_counted_as_failure(self) -> None:
        """generate_embeddings catches embed exceptions and marks the node failed."""
        node = _fake_node("n-1")
        graph_repo = MagicMock()
        graph_repo.count_nodes.return_value = 1
        graph_repo.list_nodes_without_embeddings.return_value = [node]

        service, _ = _make_service(graph_repository=graph_repo)

        embedding_service = MagicMock()
        embedding_service.embed = AsyncMock(side_effect=RuntimeError("boom"))

        with patch(
            "chaoscypher_core.repo_factories.get_embedding_service",
            return_value=embedding_service,
        ):
            response = await service.generate_embeddings()

        assert response.success is True
        assert response.processed_count == 0
        assert "1 failed" in response.message

    @pytest.mark.asyncio
    async def test_mixed_success_and_failure(self) -> None:
        """generate_embeddings reports both processed_count and failed_count together."""
        node_ok = _fake_node("n-ok")
        node_bad = _fake_node("n-bad")
        graph_repo = MagicMock()
        graph_repo.count_nodes.return_value = 2
        graph_repo.list_nodes_without_embeddings.return_value = [node_ok, node_bad]

        updated = MagicMock()
        updated.id = "n-ok"
        updated.embedding = [0.1, 0.2]
        graph_repo.update_node.return_value = updated

        service, _ = _make_service(graph_repository=graph_repo)

        embedding_service = MagicMock()
        embedding_service.embed = AsyncMock(
            side_effect=[
                MagicMock(embedding=[0.1, 0.2]),
                RuntimeError("boom"),
            ]
        )

        with patch(
            "chaoscypher_core.repo_factories.get_embedding_service",
            return_value=embedding_service,
        ):
            response = await service.generate_embeddings()

        assert response.processed_count == 1
        assert "1 failed" in response.message
        assert response.total_nodes == 2


# ---------------------------------------------------------------------------
# TestNodeToEmbeddingText
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNodeToEmbeddingText:
    """Tests for SearchService._node_to_embedding_text."""

    def test_builds_label_and_properties_string(self) -> None:
        """_node_to_embedding_text joins label and non-reserved properties."""
        service, _ = _make_service()
        node = _fake_node(
            "n-1",
            label="Alice",
            properties={"role": "engineer", "team": "alpha"},
        )

        text = service._node_to_embedding_text(node)

        assert text.startswith("Label: Alice")
        assert "role: engineer" in text
        assert "team: alpha" in text

    def test_skips_reserved_and_none_property_keys(self) -> None:
        """_node_to_embedding_text skips id, embedding, created_at, None values."""
        service, _ = _make_service()
        node = _fake_node(
            "n-1",
            label="Bob",
            properties={
                "id": "should-skip",
                "embedding": [0.1, 0.2],
                "created_at": "skip",
                "updated_at": "skip",
                "empty": None,
                "keep": "yes",
            },
        )

        text = service._node_to_embedding_text(node)

        assert "id: should-skip" not in text
        assert "embedding:" not in text
        assert "created_at:" not in text
        assert "updated_at:" not in text
        assert "empty:" not in text
        assert "keep: yes" in text

    def test_handles_empty_properties(self) -> None:
        """_node_to_embedding_text works when properties is empty dict."""
        service, _ = _make_service()
        node = _fake_node("n-1", label="Solo", properties={})

        text = service._node_to_embedding_text(node)

        assert text == "Label: Solo"
