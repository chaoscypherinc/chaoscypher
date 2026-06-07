# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the cortex ``NodeService`` wrapper.

The service delegates core CRUD to an engine ``NodeService`` and adds
SQLModel-specific extensions (stats merge, source_id enrichment, citations,
connections, embedding regen). ``EngineNodeService`` and
``build_engine_settings`` are patched at the service module path so the
constructor builds a fully mocked engine; repositories are injected as plain
mocks.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.exceptions import NotFoundError
from chaoscypher_core.models import Node, NodeCreate, NodePosition, NodeUpdate
from chaoscypher_cortex.features.nodes.models import (
    CitationListResponse,
    ConnectionsResponse,
    NodePositionUpdateRequest,
    NodeResponse,
    PaginatedNodesResponse,
)
from chaoscypher_cortex.features.nodes.service import NodeService


_NOW = datetime.now(UTC)
_SERVICE_MODULE = "chaoscypher_cortex.features.nodes.service"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(
    default_page_size: int = 50,
    max_page_size: int = 1000,
    max_citation_page_size: int = 20,
) -> MagicMock:
    settings = MagicMock()
    settings.pagination.default_page_size = default_page_size
    settings.pagination.max_page_size = max_page_size
    settings.pagination.max_citation_page_size = max_citation_page_size
    return settings


def _node_dict(node_id: str = "node-1", embedding=None) -> dict:
    return {
        "id": node_id,
        "template_id": "tpl-1",
        "label": "Alice",
        "properties": {"definition": "A person"},
        "embedding": embedding,
        "created_at": _NOW,
        "updated_at": _NOW,
    }


def _stats(
    edge_count: int = 5,
    incoming: int = 2,
    outgoing: int = 3,
    citation: int = 4,
    rel_types: int = 1,
) -> dict[str, int]:
    return {
        "edge_count": edge_count,
        "incoming_edge_count": incoming,
        "outgoing_edge_count": outgoing,
        "citation_count": citation,
        "relationship_type_count": rel_types,
    }


def _make_service(settings: MagicMock | None = None) -> NodeService:
    """Construct a NodeService with a fully mocked engine + repos."""
    settings = settings or _make_settings()
    graph_node_repo = MagicMock()
    sql_node_repo = MagicMock()
    graph_repo = MagicMock()
    search_repo = MagicMock()

    with (
        patch(f"{_SERVICE_MODULE}.EngineNodeService") as engine_cls,
        patch(f"{_SERVICE_MODULE}.build_engine_settings", return_value=MagicMock()),
    ):
        engine_cls.return_value = MagicMock()
        return NodeService(
            graph_node_repository=graph_node_repo,
            sql_node_repository=sql_node_repo,
            graph_repository=graph_repo,
            search_repository=search_repo,
            settings=settings,
        )


@contextmanager
def _patch_embedding(embedding: list[float] | None, raises: Exception | None = None):
    """Patch get_embedding_service at its source path with an async embed()."""
    embed_result = MagicMock()
    embed_result.embedding = embedding
    embedding_service = MagicMock()
    if raises is not None:
        embedding_service.embed = AsyncMock(side_effect=raises)
    else:
        embedding_service.embed = AsyncMock(return_value=embed_result)
    with patch(
        "chaoscypher_core.repo_factories.get_embedding_service",
        return_value=embedding_service,
    ):
        yield embedding_service


def _engine_node() -> Node:
    return Node(
        id="node-1",
        template_id="tpl-1",
        label="Alice",
        properties={"definition": "A person"},
        created_at=_NOW,
        updated_at=_NOW,
    )


# ---------------------------------------------------------------------------
# list_nodes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListNodes:
    def test_returns_paginated_without_stats(self) -> None:
        svc = _make_service()
        svc.engine_service.list_nodes.return_value = {
            "data": [_node_dict("node-1"), _node_dict("node-2")],
            "pagination": {
                "total": 2,
                "page": 1,
                "page_size": 50,
                "total_pages": 1,
                "has_next": False,
                "has_prev": False,
            },
        }

        result = svc.list_nodes()

        assert isinstance(result, PaginatedNodesResponse)
        assert len(result.data) == 2
        # No stats requested -> repo not queried.
        svc.sql_node_repository.get_node_stats_batch.assert_not_called()
        # include_embedding always forced False by the wrapper.
        _args, kwargs = svc.engine_service.list_nodes.call_args
        assert kwargs["include_embedding"] is False

    def test_uses_default_page_size_when_none(self) -> None:
        svc = _make_service(_make_settings(default_page_size=25, max_page_size=1000))
        svc.engine_service.list_nodes.return_value = {
            "data": [],
            "pagination": {
                "total": 0,
                "page": 1,
                "page_size": 25,
                "total_pages": 0,
                "has_next": False,
                "has_prev": False,
            },
        }

        svc.list_nodes(page_size=None)
        _args, kwargs = svc.engine_service.list_nodes.call_args
        assert kwargs["page_size"] == 25

    def test_enforces_max_page_size(self) -> None:
        svc = _make_service(_make_settings(default_page_size=50, max_page_size=100))
        svc.engine_service.list_nodes.return_value = {
            "data": [],
            "pagination": {
                "total": 0,
                "page": 1,
                "page_size": 100,
                "total_pages": 0,
                "has_next": False,
                "has_prev": False,
            },
        }

        svc.list_nodes(page_size=9999)
        _args, kwargs = svc.engine_service.list_nodes.call_args
        assert kwargs["page_size"] == 100

    def test_merges_stats_when_include_stats(self) -> None:
        svc = _make_service()
        svc.engine_service.list_nodes.return_value = {
            "data": [_node_dict("node-1"), _node_dict("node-2")],
            "pagination": {
                "total": 2,
                "page": 1,
                "page_size": 50,
                "total_pages": 1,
                "has_next": False,
                "has_prev": False,
            },
        }
        # Only node-1 has stats; node-2 is absent from the batch.
        svc.sql_node_repository.get_node_stats_batch.return_value = {
            "node-1": _stats(edge_count=7, incoming=3, outgoing=4, citation=2, rel_types=5),
        }

        result = svc.list_nodes(include_stats=True)

        svc.sql_node_repository.get_node_stats_batch.assert_called_once_with(["node-1", "node-2"])
        n1 = next(n for n in result.data if n.id == "node-1")
        n2 = next(n for n in result.data if n.id == "node-2")
        assert n1.edge_count == 7
        assert n1.incoming_edge_count == 3
        assert n1.outgoing_edge_count == 4
        assert n1.citation_count == 2
        assert n1.relationship_type_count == 5
        # node-2 untouched (no entry in stats dict).
        assert n2.edge_count is None

    def test_include_stats_with_empty_nodes_skips_repo(self) -> None:
        svc = _make_service()
        svc.engine_service.list_nodes.return_value = {
            "data": [],
            "pagination": {
                "total": 0,
                "page": 1,
                "page_size": 50,
                "total_pages": 0,
                "has_next": False,
                "has_prev": False,
            },
        }

        svc.list_nodes(include_stats=True)
        svc.sql_node_repository.get_node_stats_batch.assert_not_called()


# ---------------------------------------------------------------------------
# get_node
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetNode:
    def test_returns_node_with_stats_and_source_id(self) -> None:
        svc = _make_service()
        svc.graph_node_repository.get_node.return_value = _engine_node()
        svc.sql_node_repository.get_node_stats_batch.return_value = {
            "node-1": _stats(edge_count=9, incoming=4, outgoing=5, citation=6, rel_types=2)
        }
        svc.sql_node_repository.get_source_id_for_node.return_value = "src-42"

        result = svc.get_node("node-1")

        assert isinstance(result, NodeResponse)
        assert result.edge_count == 9
        assert result.citation_count == 6
        assert result.relationship_type_count == 2
        assert result.source_id == "src-42"
        svc.sql_node_repository.get_source_id_for_node.assert_called_once_with(
            node_id="node-1",
            node_label="Alice",
            node_definition="A person",
        )

    def test_no_stats_entry_leaves_fields_unset(self) -> None:
        svc = _make_service()
        svc.graph_node_repository.get_node.return_value = _engine_node()
        svc.sql_node_repository.get_node_stats_batch.return_value = {}
        svc.sql_node_repository.get_source_id_for_node.return_value = None

        result = svc.get_node("node-1")
        assert result.edge_count is None
        assert result.source_id is None

    def test_raises_not_found_when_repo_returns_none(self) -> None:
        svc = _make_service()
        svc.graph_node_repository.get_node.return_value = None

        with pytest.raises(NotFoundError):
            svc.get_node("missing")


# ---------------------------------------------------------------------------
# create_node
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateNode:
    @pytest.mark.asyncio
    async def test_generates_embedding_when_absent(self) -> None:
        svc = _make_service()
        created = _node_dict("node-new", embedding=None)
        svc.engine_service.create_node.return_value = created
        svc.engine_service.graph_repository.update_node.return_value = MagicMock()

        with _patch_embedding([0.1, 0.2, 0.3]):
            result = await svc.create_node(NodeCreate(template_id="tpl-1", label="Alice"))

        assert isinstance(result, NodeResponse)
        assert result.embedding == [0.1, 0.2, 0.3]
        svc.engine_service.search_repository.index_node_embedding.assert_called_once_with(
            "node-new", [0.1, 0.2, 0.3]
        )

    @pytest.mark.asyncio
    async def test_skips_embedding_when_already_present(self) -> None:
        svc = _make_service()
        created = _node_dict("node-new", embedding=[0.9, 0.8])
        svc.engine_service.create_node.return_value = created

        # If embedding generation were invoked, the (unpatched) lazy import
        # would run; assert it is NOT by checking the search repo untouched.
        result = await svc.create_node(NodeCreate(template_id="tpl-1", label="Alice"))

        assert result.embedding == [0.9, 0.8]
        svc.engine_service.search_repository.index_node_embedding.assert_not_called()


# ---------------------------------------------------------------------------
# update_node
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateNode:
    @pytest.mark.asyncio
    async def test_regenerates_embedding_when_label_changes(self) -> None:
        svc = _make_service()
        svc.engine_service.update_node.return_value = _node_dict("node-1", embedding=None)
        svc.engine_service.graph_repository.update_node.return_value = MagicMock()

        with _patch_embedding([1.0, 2.0]):
            result = await svc.update_node("node-1", NodeUpdate(label="New Label"))

        assert result.embedding == [1.0, 2.0]
        svc.engine_service.search_repository.index_node_embedding.assert_called_once()

    @pytest.mark.asyncio
    async def test_regenerates_embedding_when_properties_change(self) -> None:
        svc = _make_service()
        svc.engine_service.update_node.return_value = _node_dict("node-1", embedding=None)
        svc.engine_service.graph_repository.update_node.return_value = MagicMock()

        with _patch_embedding([3.0]):
            result = await svc.update_node("node-1", NodeUpdate(properties={"k": "v"}))

        assert result.embedding == [3.0]

    @pytest.mark.asyncio
    async def test_skips_embedding_when_no_content_change(self) -> None:
        svc = _make_service()
        svc.engine_service.update_node.return_value = _node_dict("node-1", embedding=None)

        # NodeUpdate with only position -> content_changed False.
        result = await svc.update_node("node-1", NodeUpdate(position=NodePosition(x=1.0, y=2.0)))

        assert isinstance(result, NodeResponse)
        svc.engine_service.search_repository.index_node_embedding.assert_not_called()


# ---------------------------------------------------------------------------
# _generate_and_store_embedding
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGenerateAndStoreEmbedding:
    @pytest.mark.asyncio
    async def test_success_persists_and_indexes(self) -> None:
        svc = _make_service()
        svc.engine_service.graph_repository.update_node.return_value = MagicMock()
        node_dict = _node_dict("node-1", embedding=None)

        with _patch_embedding([0.5, 0.6]):
            out = await svc._generate_and_store_embedding("node-1", node_dict)

        assert out["embedding"] == [0.5, 0.6]
        svc.engine_service.graph_repository.update_node.assert_called_once()
        svc.engine_service.search_repository.index_node_embedding.assert_called_once_with(
            "node-1", [0.5, 0.6]
        )

    @pytest.mark.asyncio
    async def test_empty_embedding_returns_unchanged(self) -> None:
        svc = _make_service()
        node_dict = _node_dict("node-1", embedding=None)

        with _patch_embedding([]):
            out = await svc._generate_and_store_embedding("node-1", node_dict)

        # Empty embedding short-circuits: no persistence, no indexing.
        assert out.get("embedding") is None
        svc.engine_service.graph_repository.update_node.assert_not_called()
        svc.engine_service.search_repository.index_node_embedding.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_is_swallowed(self) -> None:
        svc = _make_service()
        node_dict = _node_dict("node-1", embedding=None)

        with _patch_embedding(None, raises=RuntimeError("boom")):
            out = await svc._generate_and_store_embedding("node-1", node_dict)

        # Failure is logged + swallowed; node_dict returned unchanged.
        assert out is node_dict
        assert out.get("embedding") is None

    @pytest.mark.asyncio
    async def test_update_node_returning_none_skips_dict_mutation(self) -> None:
        svc = _make_service()
        # update_node returns None -> embedding not written onto node_dict,
        # but indexing still happens.
        svc.engine_service.graph_repository.update_node.return_value = None
        node_dict = _node_dict("node-1", embedding=None)

        with _patch_embedding([0.7]):
            out = await svc._generate_and_store_embedding("node-1", node_dict)

        assert out.get("embedding") is None
        svc.engine_service.search_repository.index_node_embedding.assert_called_once_with(
            "node-1", [0.7]
        )


# ---------------------------------------------------------------------------
# update_node_position
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateNodePosition:
    def test_updates_position_and_indexes(self) -> None:
        svc = _make_service()
        node = _engine_node()
        svc.graph_node_repository.update_node_position.return_value = node

        req = NodePositionUpdateRequest(position=NodePosition(x=10.0, y=20.0))
        result = svc.update_node_position("node-1", req)

        assert isinstance(result, NodeResponse)
        svc.graph_node_repository.update_node_position.assert_called_once_with(
            "node-1", x=10.0, y=20.0
        )
        svc.engine_service.safe_index_node.assert_called_once_with("node-1", node)

    def test_indexing_failure_is_swallowed(self) -> None:
        svc = _make_service()
        node = _engine_node()
        svc.graph_node_repository.update_node_position.return_value = node
        svc.engine_service.safe_index_node.side_effect = RuntimeError("index boom")

        req = NodePositionUpdateRequest(position=NodePosition(x=1.0, y=2.0))
        result = svc.update_node_position("node-1", req)
        assert isinstance(result, NodeResponse)

    def test_raises_not_found_when_repo_returns_none(self) -> None:
        svc = _make_service()
        svc.graph_node_repository.update_node_position.return_value = None

        req = NodePositionUpdateRequest(position=NodePosition(x=0.0, y=0.0))
        with pytest.raises(NotFoundError):
            svc.update_node_position("missing", req)


# ---------------------------------------------------------------------------
# delete_node
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeleteNode:
    def test_delegates_to_engine(self) -> None:
        svc = _make_service()
        svc.delete_node("node-1")
        svc.engine_service.delete_node.assert_called_once_with("node-1")


# ---------------------------------------------------------------------------
# get_node_citations
# ---------------------------------------------------------------------------


def _citation_row():
    citation = MagicMock()
    citation.id = "cit-1"
    citation.confidence = 0.9
    citation.extraction_method = "ai_extraction"
    citation.context_snippet = "…Alice…"
    citation.citation_metadata = {"k": "v"}
    citation.created_at = _NOW

    source = MagicMock()
    source.id = "src-1"
    source.title = "My Source"
    source.filename = "doc.pdf"
    source.source_type = "pdf"
    source.origin_url = "http://example.com"

    chunk = MagicMock()
    chunk.id = "chunk-1"
    chunk.content = "chunk text"
    chunk.page_number = 2
    chunk.section = "intro"
    chunk.chunk_metadata = {"m": 1}

    return citation, source, chunk


@pytest.mark.unit
class TestGetNodeCitations:
    def test_returns_paginated_citations(self) -> None:
        svc = _make_service()
        svc.graph_node_repository.get_node.return_value = _engine_node()
        svc.sql_node_repository.get_citations_for_node.return_value = (
            [_citation_row()],
            1,
        )

        result = svc.get_node_citations("node-1", page=1, page_size=10)

        assert isinstance(result, CitationListResponse)
        assert len(result.data) == 1
        c = result.data[0]
        assert c.id == "cit-1"
        assert c.source.id == "src-1"
        assert c.source.title == "My Source"
        assert c.chunk.id == "chunk-1"
        assert result.pagination["total"] == 1
        assert result.pagination["total_pages"] == 1
        assert result.pagination["has_next"] is False
        assert result.pagination["has_prev"] is False

    def test_source_title_falls_back_to_filename(self) -> None:
        svc = _make_service()
        svc.graph_node_repository.get_node.return_value = _engine_node()
        citation, source, chunk = _citation_row()
        source.title = None  # force filename fallback
        source.source_type = None  # force "unknown" fallback
        svc.sql_node_repository.get_citations_for_node.return_value = (
            [(citation, source, chunk)],
            1,
        )

        result = svc.get_node_citations("node-1")
        assert result.data[0].source.title == "doc.pdf"
        assert result.data[0].source.source_type == "unknown"

    def test_pagination_math_has_next(self) -> None:
        svc = _make_service(_make_settings(default_page_size=2, max_citation_page_size=50))
        svc.graph_node_repository.get_node.return_value = _engine_node()
        svc.sql_node_repository.get_citations_for_node.return_value = ([], 5)

        result = svc.get_node_citations("node-1", page=1, page_size=2)
        # total=5, page_size=2 -> total_pages=3, has_next True, has_prev False
        assert result.pagination["total_pages"] == 3
        assert result.pagination["has_next"] is True
        assert result.pagination["has_prev"] is False

    def test_enforces_max_citation_page_size(self) -> None:
        svc = _make_service(_make_settings(max_citation_page_size=20))
        svc.graph_node_repository.get_node.return_value = _engine_node()
        svc.sql_node_repository.get_citations_for_node.return_value = ([], 0)

        svc.get_node_citations("node-1", page=1, page_size=500)
        _args, kwargs = svc.sql_node_repository.get_citations_for_node.call_args
        assert kwargs["limit"] == 20

    def test_raises_not_found_when_node_missing(self) -> None:
        svc = _make_service()
        svc.graph_node_repository.get_node.return_value = None
        with pytest.raises(NotFoundError):
            svc.get_node_citations("missing")


# ---------------------------------------------------------------------------
# get_node_connections
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetNodeConnections:
    def test_returns_connections_with_pagination(self) -> None:
        svc = _make_service()
        svc.graph_node_repository.get_node.return_value = _engine_node()
        svc.sql_node_repository.get_connected_nodes.return_value = (
            [
                {
                    "id": "node-2",
                    "label": "Bob",
                    "template_id": "Person",
                    "edge_count": 3,
                    "relationship": "knows",
                    "direction": "outgoing",
                }
            ],
            10,
        )

        result = svc.get_node_connections("node-1", page=1, page_size=4)

        assert isinstance(result, ConnectionsResponse)
        assert len(result.data) == 1
        assert result.data[0].id == "node-2"
        assert result.data[0].direction == "outgoing"
        # total=10, page_size=4 -> total_pages=3, has_next True
        assert result.pagination["total_pages"] == 3
        assert result.pagination["has_next"] is True
        assert result.pagination["has_prev"] is False

    def test_has_prev_on_later_page(self) -> None:
        svc = _make_service()
        svc.graph_node_repository.get_node.return_value = _engine_node()
        svc.sql_node_repository.get_connected_nodes.return_value = ([], 10)

        result = svc.get_node_connections("node-1", page=3, page_size=4)
        # total=10, page_size=4 -> total_pages=3, page=3 -> has_next False, has_prev True
        assert result.pagination["has_next"] is False
        assert result.pagination["has_prev"] is True

    def test_forwards_sort_and_pagination_args(self) -> None:
        svc = _make_service()
        svc.graph_node_repository.get_node.return_value = _engine_node()
        svc.sql_node_repository.get_connected_nodes.return_value = ([], 0)

        svc.get_node_connections("node-1", sort_by="label", page=2, page_size=7)
        _args, kwargs = svc.sql_node_repository.get_connected_nodes.call_args
        assert kwargs["sort_by"] == "label"
        assert kwargs["offset"] == 7  # (page 2 - 1) * 7
        assert kwargs["limit"] == 7

    def test_raises_not_found_when_node_missing(self) -> None:
        svc = _make_service()
        svc.graph_node_repository.get_node.return_value = None
        with pytest.raises(NotFoundError):
            svc.get_node_connections("missing")
