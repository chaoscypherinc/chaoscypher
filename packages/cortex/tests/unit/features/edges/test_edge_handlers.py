# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for edges API handler logic.

Verifies that each handler calls the correct EdgeService method with the
correct arguments and transforms the service dict into an EdgeResponse.
FastAPI DI is bypassed — the service mock is passed directly.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.models import EdgeCreate, EdgeUpdate
from chaoscypher_cortex.features.edges.api import (
    batch_edges_operation,
    create_edge,
    delete_edge,
    get_edge,
    list_edges,
    update_edge,
)
from chaoscypher_cortex.shared.kernel import BulkOperationRequest, BulkRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(UTC)


def _edge_dict(edge_id: str = "e-1") -> dict:
    """Return a minimal edge mapping compatible with EdgeResponse."""
    return {
        "id": edge_id,
        "template_id": "tmpl-rel",
        "source_node_id": "n-src",
        "target_node_id": "n-tgt",
        "label": "relates_to",
        "properties": {"weight": 0.9},
        "created_at": _NOW,
        "updated_at": _NOW,
    }


def _paginated_edges(*edge_ids: str) -> dict:
    """Return a service-style paginated result for edges."""
    data = [_edge_dict(eid) for eid in edge_ids]
    return {
        "data": data,
        "pagination": {
            "total": len(data),
            "page": 1,
            "page_size": 50,
            "total_pages": 1,
            "has_next": False,
            "has_prev": False,
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListEdges:
    """Tests for the list_edges handler."""

    @pytest.mark.asyncio
    async def test_returns_paginated_response(self) -> None:
        """Handler calls list_edges and wraps dicts in PaginatedEdgesResponse."""
        mock_service = MagicMock()
        mock_service.list_edges.return_value = _paginated_edges("e-1", "e-2")

        result = await list_edges(
            _="test-user",
            edge_service=mock_service,
            pagination=(1, 50),
            source_node_id=None,
            source_ids=None,
            minimal=False,
        )

        mock_service.list_edges.assert_called_once_with(
            source_node_id=None,
            source_ids=None,
            page=1,
            page_size=50,
            minimal=False,
        )
        assert len(result.data) == 2
        assert result.data[0].id == "e-1"
        assert result.data[1].id == "e-2"
        assert result.pagination.total == 2

    @pytest.mark.asyncio
    async def test_passes_filters_to_service(self) -> None:
        """Handler forwards source_node_id, source_ids, and minimal to the service."""
        mock_service = MagicMock()
        mock_service.list_edges.return_value = _paginated_edges("e-1")

        await list_edges(
            _="test-user",
            edge_service=mock_service,
            pagination=(2, 10),
            source_node_id="n-src",
            source_ids=["doc-1", "doc-2"],
            minimal=True,
        )

        mock_service.list_edges.assert_called_once_with(
            source_node_id="n-src",
            source_ids=["doc-1", "doc-2"],
            page=2,
            page_size=10,
            minimal=True,
        )

    @pytest.mark.asyncio
    async def test_returns_empty_list(self) -> None:
        """Handler handles an empty page correctly."""
        mock_service = MagicMock()
        mock_service.list_edges.return_value = _paginated_edges()

        result = await list_edges(
            _="test-user",
            edge_service=mock_service,
            pagination=(1, 50),
            source_node_id=None,
            source_ids=None,
            minimal=False,
        )

        assert result.data == []
        assert result.pagination.total == 0


@pytest.mark.unit
class TestCreateEdge:
    """Tests for the create_edge handler."""

    @pytest.mark.asyncio
    async def test_creates_edge_and_returns_response(self) -> None:
        """Handler passes EdgeCreate to the service and wraps the dict in EdgeResponse."""
        mock_service = MagicMock()
        mock_service.create_edge.return_value = _edge_dict("e-new")

        edge_create = EdgeCreate(
            template_id="tmpl-rel",
            source_node_id="n-src",
            target_node_id="n-tgt",
            label="relates_to",
        )

        result = await create_edge(
            _="test-user",
            edge_create=edge_create,
            edge_service=mock_service,
        )

        mock_service.create_edge.assert_called_once_with(edge_create)
        assert result.id == "e-new"
        assert result.label == "relates_to"

    @pytest.mark.asyncio
    async def test_result_is_edge_response_instance(self) -> None:
        """create_edge returns an EdgeResponse (not a raw dict)."""
        from chaoscypher_cortex.features.edges.models import EdgeResponse

        mock_service = MagicMock()
        mock_service.create_edge.return_value = _edge_dict()

        result = await create_edge(
            _="test-user",
            edge_create=EdgeCreate(
                template_id="t",
                source_node_id="s",
                target_node_id="tgt",
                label="l",
            ),
            edge_service=mock_service,
        )

        assert isinstance(result, EdgeResponse)


@pytest.mark.unit
class TestGetEdge:
    """Tests for the get_edge handler."""

    @pytest.mark.asyncio
    async def test_returns_edge_response(self) -> None:
        """Handler calls get_edge with the ID and wraps the dict in EdgeResponse."""
        mock_service = MagicMock()
        mock_service.get_edge.return_value = _edge_dict("e-42")

        result = await get_edge(
            _="test-user",
            edge_id="e-42",
            edge_service=mock_service,
        )

        mock_service.get_edge.assert_called_once_with("e-42")
        assert result.id == "e-42"
        assert result.source_node_id == "n-src"

    @pytest.mark.asyncio
    async def test_result_is_edge_response_instance(self) -> None:
        """get_edge always returns an EdgeResponse object."""
        from chaoscypher_cortex.features.edges.models import EdgeResponse

        mock_service = MagicMock()
        mock_service.get_edge.return_value = _edge_dict()

        result = await get_edge(_="test-user", edge_id="e-1", edge_service=mock_service)

        assert isinstance(result, EdgeResponse)


@pytest.mark.unit
class TestUpdateEdge:
    """Tests for the update_edge handler."""

    @pytest.mark.asyncio
    async def test_updates_edge_and_returns_response(self) -> None:
        """Handler calls update_edge with the ID and EdgeUpdate, returns EdgeResponse."""
        mock_service = MagicMock()
        updated = _edge_dict("e-5")
        updated["label"] = "depends_on"
        mock_service.update_edge.return_value = updated

        edge_update = EdgeUpdate(label="depends_on")

        result = await update_edge(
            _="test-user",
            edge_id="e-5",
            edge_update=edge_update,
            edge_service=mock_service,
        )

        mock_service.update_edge.assert_called_once_with("e-5", edge_update)
        assert result.id == "e-5"
        assert result.label == "depends_on"

    @pytest.mark.asyncio
    async def test_result_is_edge_response_instance(self) -> None:
        """update_edge returns an EdgeResponse, not a raw dict."""
        from chaoscypher_cortex.features.edges.models import EdgeResponse

        mock_service = MagicMock()
        mock_service.update_edge.return_value = _edge_dict()

        result = await update_edge(
            _="test-user",
            edge_id="e-1",
            edge_update=EdgeUpdate(),
            edge_service=mock_service,
        )

        assert isinstance(result, EdgeResponse)


@pytest.mark.unit
class TestDeleteEdge:
    """Tests for the delete_edge handler."""

    @pytest.mark.asyncio
    async def test_calls_delete_and_returns_none(self) -> None:
        """Handler calls delete_edge and returns None (204 No Content)."""
        mock_service = MagicMock()

        result = await delete_edge(
            _="test-user",
            edge_id="e-del",
            edge_service=mock_service,
        )

        mock_service.delete_edge.assert_called_once_with("e-del")
        assert result is None


@pytest.mark.unit
class TestBatchEdgesOperation:
    """Tests for the batch_edges_operation handler."""

    @pytest.mark.asyncio
    async def test_queues_task_and_returns_bulk_response(self) -> None:
        """Handler enqueues a bulk_edges task and returns a BulkResponse with task_id."""
        from chaoscypher_cortex.shared.kernel import BulkResponse

        mock_settings = MagicMock()
        mock_settings.priorities.background = 50

        request = BulkRequest(
            operations=[
                BulkOperationRequest(operation="delete", data={"id": "e-1"}),
                BulkOperationRequest(operation="delete", data={"id": "e-2"}),
            ]
        )

        with patch("chaoscypher_cortex.features.edges.api.queue_client") as mock_queue:
            mock_queue.enqueue_task = AsyncMock(return_value="task-abc")

            result = await batch_edges_operation(
                _="test-user",
                request=request,
                settings=mock_settings,
            )

        mock_queue.enqueue_task.assert_called_once()
        call_kwargs = mock_queue.enqueue_task.call_args.kwargs
        assert call_kwargs["operation"] == "bulk_edges"
        assert len(call_kwargs["data"]["operations"]) == 2

        assert isinstance(result, BulkResponse)
        assert result.task_id == "task-abc"
        assert result.status == "queued"
        assert "2 operations" in result.message

    @pytest.mark.asyncio
    async def test_uses_operations_queue(self) -> None:
        """batch_edges_operation always targets the QUEUE_OPERATIONS queue."""
        from chaoscypher_core.constants import QUEUE_OPERATIONS

        mock_settings = MagicMock()
        mock_settings.priorities.background = 50

        with patch("chaoscypher_cortex.features.edges.api.queue_client") as mock_queue:
            mock_queue.enqueue_task = AsyncMock(return_value="task-xyz")

            await batch_edges_operation(
                _="test-user",
                request=BulkRequest(operations=[]),
                settings=mock_settings,
            )

        call_kwargs = mock_queue.enqueue_task.call_args.kwargs
        assert call_kwargs["queue"] == QUEUE_OPERATIONS
