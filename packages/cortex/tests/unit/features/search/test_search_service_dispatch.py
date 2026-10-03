# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 4 Task E: ``SearchService.search()`` owns the keyword/semantic/hybrid branch."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_cortex.features.search.service import SearchService


SEARCH_API = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "chaoscypher_cortex"
    / "features"
    / "search"
    / "api.py"
)
SEARCH_SERVICE = SEARCH_API.parent / "service.py"


def test_search_service_defines_dispatch_method() -> None:
    tree = ast.parse(SEARCH_SERVICE.read_text(encoding="utf-8"))
    method_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            method_names.add(node.name)
    assert "search" in method_names, (
        "SearchService must define a unified `search(query, limit, search_type)` dispatch method"
    )


def test_handler_has_no_branching_or_embedding_wiring() -> None:
    """The route handler must not build the embedding callback or branch on search_type.

    Both responsibilities moved into ``SearchService.search()``. The handler
    delegates and translates errors.
    """
    tree = ast.parse(SEARCH_API.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "search":
            dump = ast.unparse(node)
            assert "get_embedding_service" not in dump, (
                "search handler still constructs an embedding callback — "
                "move this into SearchService.search()"
            )
            assert "semantic_search" not in dump, (
                "search handler still branches to semantic_search — "
                "move the branch into SearchService.search()"
            )
            assert "hybrid_search" not in dump, (
                "search handler still branches to hybrid_search — "
                "move the branch into SearchService.search()"
            )
            return
    msg = "search handler not found"
    raise AssertionError(msg)


# ---------------------------------------------------------------------------
# Behavioural dispatch tests: SearchService.search() routes each search_type
# to the right engine call and wires the operator's similarity threshold.
# ---------------------------------------------------------------------------

_CONFIGURED_MIN_SIMILARITY = 0.73
_QUERY_VECTOR = [0.1, 0.2, 0.3]


def _dispatch_service() -> tuple[SearchService, MagicMock]:
    """SearchService over a mocked engine whose three search modes are distinguishable."""
    engine = MagicMock()
    engine.keyword_search = MagicMock(return_value={"data": [], "type": "keyword"})
    engine.semantic_search = AsyncMock(return_value={"data": [], "type": "semantic"})
    engine.hybrid_search = AsyncMock(return_value={"data": [], "type": "hybrid"})

    settings = MagicMock()
    settings.search.min_similarity_threshold = _CONFIGURED_MIN_SIMILARITY
    settings.pagination.default_search_results = 7

    with (
        patch(
            "chaoscypher_cortex.features.search.service.EngineSearchService",
            return_value=engine,
        ),
        patch(
            "chaoscypher_core.app_config.engine_factory.build_engine_settings",
            return_value=None,
        ),
    ):
        service = SearchService(
            search_repository=MagicMock(),
            graph_repository=MagicMock(),
            indexing_repository=MagicMock(),
            source_repository=MagicMock(),
            sources_repository=MagicMock(),
            settings=settings,
        )
    return service, engine


@pytest.fixture
def embedding_service() -> Iterator[MagicMock]:
    stub = MagicMock()
    stub.embed = AsyncMock(return_value=SimpleNamespace(embedding=_QUERY_VECTOR))
    with patch("chaoscypher_core.repo_factories.get_embedding_service", return_value=stub):
        yield stub


@pytest.mark.asyncio
async def test_search_keyword_dispatches_to_keyword_only(embedding_service: MagicMock) -> None:
    service, engine = _dispatch_service()

    response = await service.search("q", limit=3, search_type="keyword")

    assert response.type == "keyword"
    engine.keyword_search.assert_called_once_with("q", limit=3)
    engine.semantic_search.assert_not_awaited()
    engine.hybrid_search.assert_not_awaited()


@pytest.mark.asyncio
async def test_search_semantic_dispatches_with_embedding_callback(
    embedding_service: MagicMock,
) -> None:
    service, engine = _dispatch_service()

    response = await service.search("q", limit=3, search_type="semantic")

    assert response.type == "semantic"
    engine.keyword_search.assert_not_called()
    engine.hybrid_search.assert_not_awaited()
    engine.semantic_search.assert_awaited_once()
    callback = engine.semantic_search.await_args.kwargs["embedding_provider_callback"]
    assert await callback("q") == _QUERY_VECTOR
    embedding_service.embed.assert_awaited_once_with("q")


@pytest.mark.asyncio
async def test_search_hybrid_passes_configured_min_similarity(
    embedding_service: MagicMock,
) -> None:
    service, engine = _dispatch_service()

    response = await service.search("q", limit=3, search_type="hybrid")

    assert response.type == "hybrid"
    engine.keyword_search.assert_not_called()
    engine.semantic_search.assert_not_awaited()
    engine.hybrid_search.assert_awaited_once()
    kwargs = engine.hybrid_search.await_args.kwargs
    assert kwargs["min_similarity"] == _CONFIGURED_MIN_SIMILARITY
    assert kwargs["limit"] == 3
    assert await kwargs["embedding_provider_callback"]("q") == _QUERY_VECTOR


@pytest.mark.asyncio
async def test_search_invalid_type_raises_value_error(embedding_service: MagicMock) -> None:
    service, engine = _dispatch_service()

    with pytest.raises(ValueError, match="Invalid search type"):
        await service.search("q", search_type="fuzzy")

    engine.keyword_search.assert_not_called()
    engine.semantic_search.assert_not_awaited()
    engine.hybrid_search.assert_not_awaited()
