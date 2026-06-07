# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Exception-contract tests for search/engine/{search,index}.py.

Pins the exception types raised at each validation and operation-failure site
so that the Cortex error mapper can produce structured 4xx/422 envelopes
instead of generic 500s.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core import EngineSettings
from chaoscypher_core.exceptions import ChaosCypherException, OperationError, ValidationError
from chaoscypher_core.services.search.engine.index import IndexingService
from chaoscypher_core.services.search.engine.search import SearchService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_search_service(**kwargs: Any) -> SearchService:
    """Return a SearchService with minimal fake dependencies."""
    search_repo = MagicMock()
    graph_repo = MagicMock()
    indexing_repo = MagicMock()
    source_repo = MagicMock()
    return SearchService(
        search_repository=search_repo,
        graph_repository=graph_repo,
        indexing_repository=indexing_repo,
        source_repository=source_repo,
        **kwargs,
    )


def _make_indexing_service(
    *,
    embedding_service: Any = None,
) -> IndexingService:
    """Return an IndexingService with minimal fake dependencies."""
    repo = MagicMock()
    settings = EngineSettings()
    return IndexingService(
        repository=repo,
        settings=settings,
        embedding_service=embedding_service,
    )


# ---------------------------------------------------------------------------
# search.py:140 — from_adapter() session guard
# OperationError when adapter.session is None (connect() not called).
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSearchServiceFromAdapterSessionNone:
    """OperationError is raised when the adapter session is None in from_adapter()."""

    def test_raises_operation_error_when_session_is_none(self) -> None:
        adapter = MagicMock()
        adapter.session = None  # Simulate not calling adapter.connect()
        settings = EngineSettings()
        search_repo = MagicMock()

        with pytest.raises(OperationError) as exc_info:
            SearchService.from_adapter(adapter, settings, search_repository=search_repo)
        exc = exc_info.value
        assert isinstance(exc, ChaosCypherException)
        assert exc.code == "OPERATION_ERROR"
        assert exc.details.get("operation") == "connect"

    def test_operation_error_is_chaoscypher_exception(self) -> None:
        adapter = MagicMock()
        adapter.session = None
        settings = EngineSettings()
        search_repo = MagicMock()

        with pytest.raises(ChaosCypherException):
            SearchService.from_adapter(adapter, settings, search_repository=search_repo)


# ---------------------------------------------------------------------------
# index.py:138 — create_index() no-chunks guard
# ValidationError when no chunks exist for the given source_id.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIndexingServiceCreateIndexNoChunks:
    """ValidationError is raised when no chunks are found for the source_id."""

    @pytest.mark.asyncio
    async def test_raises_validation_error_when_no_chunks(self) -> None:
        service = _make_indexing_service()
        # Repository returns empty list for chunks
        service.repository.get_chunks_by_source.return_value = ([], 0)

        with pytest.raises(ValidationError) as exc_info:
            await service.create_index("source-abc-123")
        exc = exc_info.value
        assert isinstance(exc, ChaosCypherException)
        assert exc.code == "VALIDATION_ERROR"
        assert exc.details.get("field") == "source_id"

    @pytest.mark.asyncio
    async def test_validation_error_is_chaoscypher_exception(self) -> None:
        service = _make_indexing_service()
        service.repository.get_chunks_by_source.return_value = ([], 0)

        with pytest.raises(ChaosCypherException):
            await service.create_index("source-abc-123")


# ---------------------------------------------------------------------------
# index.py:321 — _generate_chunk_embeddings() no-provider guard
# OperationError when embedding_service is None.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIndexingServiceNoEmbeddingProvider:
    """OperationError is raised when the embedding provider is not configured."""

    @pytest.mark.asyncio
    async def test_raises_operation_error_when_no_embedding_service(self) -> None:
        # embedding_service=None → no provider configured
        service = _make_indexing_service(embedding_service=None)

        with pytest.raises(OperationError) as exc_info:
            await service._generate_chunk_embeddings(["some text"], batch_size=1, concurrency=1)
        exc = exc_info.value
        assert isinstance(exc, ChaosCypherException)
        assert exc.code == "OPERATION_ERROR"
        assert exc.details.get("operation") == "embed"

    @pytest.mark.asyncio
    async def test_operation_error_is_chaoscypher_exception_no_provider(self) -> None:
        service = _make_indexing_service(embedding_service=None)

        with pytest.raises(ChaosCypherException):
            await service._generate_chunk_embeddings(["text"], batch_size=1, concurrency=1)


# ---------------------------------------------------------------------------
# index.py:367 — _generate_chunk_embeddings() empty-embedding guard
# ValidationError when an empty embedding is returned from the provider.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIndexingServiceEmptyEmbedding:
    """ValidationError is raised when the embedding provider returns an empty embedding."""

    @pytest.mark.asyncio
    async def test_raises_validation_error_for_empty_embedding(self) -> None:
        embedding_svc = AsyncMock()

        # batch_embed returns a result whose .embeddings contains an empty list
        batch_result = MagicMock()
        batch_result.embeddings = [[]]  # empty embedding vector
        batch_result.total = 1
        embedding_svc.batch_embed = AsyncMock(return_value=batch_result)

        service = _make_indexing_service(embedding_service=embedding_svc)

        with pytest.raises(ValidationError) as exc_info:
            await service._generate_chunk_embeddings(["some text"], batch_size=1, concurrency=1)
        exc = exc_info.value
        assert isinstance(exc, ChaosCypherException)
        assert exc.code == "VALIDATION_ERROR"
        assert exc.details.get("field") == "embedding"

    @pytest.mark.asyncio
    async def test_validation_error_is_chaoscypher_exception_empty_embedding(self) -> None:
        embedding_svc = AsyncMock()
        batch_result = MagicMock()
        batch_result.embeddings = [[]]
        batch_result.total = 1
        embedding_svc.batch_embed = AsyncMock(return_value=batch_result)

        service = _make_indexing_service(embedding_service=embedding_svc)

        with pytest.raises(ChaosCypherException):
            await service._generate_chunk_embeddings(["text"], batch_size=1, concurrency=1)
