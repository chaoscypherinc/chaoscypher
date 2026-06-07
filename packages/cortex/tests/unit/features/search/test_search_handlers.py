# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for search API handler logic.

Verifies that each handler calls the correct SearchService method with
the correct arguments and transforms the response correctly. FastAPI DI
is bypassed by passing the service mock directly as a function argument.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from chaoscypher_cortex.features.search.api import (
    generate_embeddings,
    get_index_status,
    get_search_stats,
    rebuild_search_indexes,
    search,
)
from chaoscypher_cortex.features.search.models import (
    GenerateEmbeddingsResponse,
    QueuedRebuildResponse,
    RebuildIndexResponse,
    SearchResponse,
    SearchStatistics,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _empty_search_response(search_type: str = "keyword") -> SearchResponse:
    """Return an empty SearchResponse of the given type."""
    return SearchResponse(data=[], type=search_type)  # type: ignore[arg-type]


def _fake_settings(min_similarity: float = 0.6) -> MagicMock:
    """Return a MagicMock settings object with search.min_similarity_threshold."""
    settings = MagicMock()
    settings.search.min_similarity_threshold = min_similarity
    settings.priorities.background = 50
    settings.current_database = "default"
    return settings


# ---------------------------------------------------------------------------
# TestSearch
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSearch:
    """Tests for the search handler.

    The unified ``search`` handler now delegates the full mode-dispatch
    (keyword / semantic / hybrid) to ``SearchService.search``; the route
    no longer touches ``settings`` or builds an embedding callback. We
    just verify the handler hands ``q``, ``limit``, and ``search_type``
    to the service.
    """

    @pytest.mark.asyncio
    async def test_keyword_search_delegates_to_service(self) -> None:
        """Route forwards keyword search to ``service.search``."""
        mock_service = MagicMock()
        mock_service.search = AsyncMock(return_value=_empty_search_response("keyword"))

        response = await search(
            _="test-user",
            search_service=mock_service,
            limit=10,
            q="hello",
            search_type="keyword",
        )

        mock_service.search.assert_awaited_once_with("hello", limit=10, search_type="keyword")
        assert response.type == "keyword"

    @pytest.mark.asyncio
    async def test_semantic_search_delegates_to_service(self) -> None:
        """Route forwards semantic search to ``service.search``."""
        mock_service = MagicMock()
        mock_service.search = AsyncMock(return_value=_empty_search_response("semantic"))

        response = await search(
            _="test-user",
            search_service=mock_service,
            limit=5,
            q="query",
            search_type="semantic",
        )

        mock_service.search.assert_awaited_once_with("query", limit=5, search_type="semantic")
        assert response.type == "semantic"

    @pytest.mark.asyncio
    async def test_hybrid_search_delegates_to_service(self) -> None:
        """Route forwards hybrid search to ``service.search``."""
        mock_service = MagicMock()
        mock_service.search = AsyncMock(return_value=_empty_search_response("hybrid"))

        await search(
            _="test-user",
            search_service=mock_service,
            limit=8,
            q="query",
            search_type="hybrid",
        )

        mock_service.search.assert_awaited_once_with("query", limit=8, search_type="hybrid")


# ---------------------------------------------------------------------------
# TestGetSearchStats
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetSearchStats:
    """Tests for the get_search_stats handler."""

    @pytest.mark.asyncio
    async def test_returns_stats_from_service(self) -> None:
        """get_search_stats delegates to service.get_stats()."""
        mock_service = MagicMock()
        mock_service.get_stats.return_value = SearchStatistics(
            fulltext_doc_count=10,
            vector_index_size=20,
            vector_dimension=768,
        )

        response = await get_search_stats(_="test-user", search_service=mock_service)

        mock_service.get_stats.assert_called_once_with()
        assert response.fulltext_doc_count == 10
        assert response.vector_index_size == 20
        assert response.vector_dimension == 768


# ---------------------------------------------------------------------------
# TestGetIndexStatus
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetIndexStatus:
    """Tests for the get_index_status handler."""

    @pytest.mark.asyncio
    async def test_returns_rebuild_flag_and_index_metadata(self) -> None:
        """get_index_status merges repo stats with needs_rebuild/model fields."""
        mock_repo = MagicMock()
        mock_repo.get_index_stats.return_value = {
            "fulltext": {"document_count": 5},
            "vector": {"vector_count": 4, "dimensions": 768},
        }
        mock_repo.needs_full_reindex = False
        mock_repo.embedding_model = "nomic-embed-text"
        mock_repo.vector_dim = 768

        with patch(
            "chaoscypher_cortex.features.search.api.get_search_repository",
            return_value=mock_repo,
        ):
            response = await get_index_status(_="test-user", settings=_fake_settings())

        assert response.needs_rebuild is False
        assert response.embedding_model == "nomic-embed-text"
        assert response.vector_dimensions == 768
        assert response.fulltext == {"document_count": 5}
        assert response.vector == {"vector_count": 4, "dimensions": 768}


# ---------------------------------------------------------------------------
# TestRebuildSearchIndexes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRebuildSearchIndexes:
    """Tests for the rebuild_search_indexes handler."""

    @pytest.mark.asyncio
    async def test_fast_rebuild_when_no_regeneration_needed(self) -> None:
        """rebuild_search_indexes calls service.rebuild_indexes when no regen needed."""
        mock_repo = MagicMock()
        mock_repo.needs_full_reindex = False

        mock_service = MagicMock()
        mock_service.rebuild_indexes.return_value = RebuildIndexResponse(
            success=True,
            total_nodes=10,
            nodes_with_embeddings=10,
            chunks_indexed=5,
            message="Rebuilt",
        )

        with (
            patch(
                "chaoscypher_cortex.features.search.api.get_search_repository",
                return_value=mock_repo,
            ),
            patch("chaoscypher_core.repo_factories.search_factory.invalidate_search_repository"),
        ):
            response = await rebuild_search_indexes(
                _="test-user",
                response=MagicMock(),
                search_service=mock_service,
                settings=_fake_settings(),
            )

        assert isinstance(response, RebuildIndexResponse)
        assert response.success is True
        mock_service.rebuild_indexes.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_queues_regeneration_when_reindex_needed(self) -> None:
        """rebuild_search_indexes queues task and returns QueuedRebuildResponse."""
        mock_repo = MagicMock()
        mock_repo.needs_full_reindex = True

        mock_service = MagicMock()
        mock_queue = AsyncMock(return_value="task-123")

        with (
            patch(
                "chaoscypher_cortex.features.search.api.get_search_repository",
                return_value=mock_repo,
            ),
            patch(
                "chaoscypher_core.operations.queue_utils.queue_rebuild_search_indexes",
                mock_queue,
            ),
        ):
            fake_response = MagicMock()
            response = await rebuild_search_indexes(
                _="test-user",
                response=fake_response,
                search_service=mock_service,
                settings=_fake_settings(),
            )

        assert isinstance(response, QueuedRebuildResponse)
        assert response.task_id == "task-123"
        assert fake_response.status_code == 202
        mock_queue.assert_awaited_once_with(database_name="default", regenerate=True, priority=50)
        mock_service.rebuild_indexes.assert_not_called()


# ---------------------------------------------------------------------------
# TestGenerateEmbeddings
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGenerateEmbeddingsHandler:
    """Tests for the generate_embeddings handler."""

    @pytest.mark.asyncio
    async def test_delegates_to_service_with_no_trigger(self) -> None:
        """generate_embeddings calls service.generate_embeddings(trigger_service=None)."""
        mock_service = MagicMock()
        mock_service.generate_embeddings = AsyncMock(
            return_value=GenerateEmbeddingsResponse(
                success=True,
                total_nodes=3,
                processed_count=2,
                message="Generated embeddings for 2 nodes",
            )
        )

        response = await generate_embeddings(
            _="test-user",
            search_service=mock_service,
        )

        mock_service.generate_embeddings.assert_awaited_once_with(trigger_service=None)
        assert response.success is True
        assert response.processed_count == 2
        assert response.total_nodes == 3

    @pytest.mark.asyncio
    async def test_returns_zero_when_all_nodes_have_embeddings(self) -> None:
        """generate_embeddings forwards processed_count=0 from the service."""
        mock_service = MagicMock()
        mock_service.generate_embeddings = AsyncMock(
            return_value=GenerateEmbeddingsResponse(
                success=True,
                total_nodes=5,
                processed_count=0,
                message="All nodes already have embeddings",
            )
        )

        response = await generate_embeddings(
            _="test-user",
            search_service=mock_service,
        )

        assert response.processed_count == 0
        assert "already have embeddings" in response.message


# ---------------------------------------------------------------------------
# TestSearchInvalidType
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSearchInvalidType:
    """Tests for the search handler with an invalid search_type value."""

    @pytest.mark.asyncio
    async def test_raises_400_for_unknown_search_type(self) -> None:
        """Raise HTTP 400 when service.search rejects the search_type."""
        mock_service = MagicMock()
        mock_service.search = AsyncMock(side_effect=ValueError("Unknown search_type 'bogus'"))

        with pytest.raises(HTTPException) as exc_info:
            await search(
                _="test-user",
                search_service=mock_service,
                limit=10,
                q="hello",
                search_type="bogus",  # type: ignore[arg-type]
            )

        assert exc_info.value.status_code == 400
