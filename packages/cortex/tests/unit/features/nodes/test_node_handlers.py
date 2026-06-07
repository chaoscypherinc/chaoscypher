# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for nodes API handler logic.

Verifies that each handler calls the correct NodeService method with the
correct arguments and transforms the response correctly. FastAPI DI is
bypassed — the service mock is passed directly as a function argument.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from chaoscypher_cortex.features.nodes.api import (
    batch_nodes_operation,
    create_node,
    delete_node,
    get_node,
    get_node_citations,
    get_node_connections,
    list_nodes,
    update_node,
    update_node_position,
)
from chaoscypher_cortex.features.nodes.models import (
    CitationListResponse,
    ConnectionsResponse,
    NodePositionUpdateRequest,
    NodeResponse,
    PaginatedNodesResponse,
)
from chaoscypher_cortex.shared.kernel import BulkOperationRequest, BulkRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(UTC)


def _node_response(node_id: str = "node-1") -> NodeResponse:
    """Return a minimal NodeResponse instance."""
    return NodeResponse(
        id=node_id,
        template_id="tpl-1",
        label="Alice",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _paginated_nodes(count: int = 2) -> PaginatedNodesResponse:
    """Return a minimal PaginatedNodesResponse."""
    return PaginatedNodesResponse(
        data=[_node_response(f"node-{i}") for i in range(count)],
        pagination={
            "total": count,
            "page": 1,
            "page_size": 50,
            "total_pages": 1,
            "has_next": False,
            "has_prev": False,
        },
    )


def _connections_response() -> ConnectionsResponse:
    """Return a minimal ConnectionsResponse."""
    return ConnectionsResponse(
        data=[],
        pagination={
            "total": 0,
            "page": 1,
            "page_size": 50,
            "total_pages": 1,
            "has_next": False,
            "has_prev": False,
        },
    )


def _citations_response() -> CitationListResponse:
    """Return a minimal CitationListResponse."""
    return CitationListResponse(
        data=[],
        pagination={
            "total": 0,
            "page": 1,
            "page_size": 50,
            "total_pages": 1,
            "has_next": False,
            "has_prev": False,
        },
    )


def _make_settings(page_size: int = 50) -> MagicMock:
    """Return a minimal settings mock."""
    settings = MagicMock()
    settings.pagination.default_page_size = page_size
    settings.pagination.max_page_size = 1000
    settings.priorities.background = 50
    return settings


# ---------------------------------------------------------------------------
# TestListNodes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListNodes:
    """Tests for the list_nodes handler."""

    @pytest.mark.asyncio
    async def test_returns_paginated_nodes(self) -> None:
        """Handler delegates to node_service.list_nodes and returns the result."""
        mock_service = MagicMock()
        mock_service.list_nodes.return_value = _paginated_nodes(3)
        settings = _make_settings()

        result = await list_nodes(
            _="test-user",
            node_service=mock_service,
            settings=settings,
            template_id=None,
            source_ids=None,
            page=1,
            page_size=50,
            minimal=False,
            include_stats=False,
        )

        mock_service.list_nodes.assert_called_once_with(
            template_id=None,
            source_ids=None,
            page=1,
            page_size=50,
            minimal=False,
            include_stats=False,
        )
        assert len(result.data) == 3

    @pytest.mark.asyncio
    async def test_passes_template_id_filter(self) -> None:
        """Handler forwards template_id filter to the service."""
        mock_service = MagicMock()
        mock_service.list_nodes.return_value = _paginated_nodes(1)

        await list_nodes(
            _="test-user",
            node_service=mock_service,
            settings=_make_settings(),
            template_id="tpl-abc",
            source_ids=None,
            page=1,
            page_size=50,
            minimal=False,
            include_stats=False,
        )

        mock_service.list_nodes.assert_called_once_with(
            template_id="tpl-abc",
            source_ids=None,
            page=1,
            page_size=50,
            minimal=False,
            include_stats=False,
        )

    @pytest.mark.asyncio
    async def test_passes_minimal_and_include_stats_flags(self) -> None:
        """Handler forwards minimal and include_stats flags to the service."""
        mock_service = MagicMock()
        mock_service.list_nodes.return_value = _paginated_nodes(0)

        await list_nodes(
            _="test-user",
            node_service=mock_service,
            settings=_make_settings(),
            template_id=None,
            source_ids=None,
            page=2,
            page_size=10,
            minimal=True,
            include_stats=True,
        )

        mock_service.list_nodes.assert_called_once_with(
            template_id=None,
            source_ids=None,
            page=2,
            page_size=10,
            minimal=True,
            include_stats=True,
        )

    @pytest.mark.asyncio
    async def test_returns_empty_paginated_response(self) -> None:
        """Handler returns empty list when service has no nodes."""
        mock_service = MagicMock()
        mock_service.list_nodes.return_value = _paginated_nodes(0)

        result = await list_nodes(
            _="test-user",
            node_service=mock_service,
            settings=_make_settings(),
            template_id=None,
            source_ids=None,
            page=1,
            page_size=50,
            minimal=False,
            include_stats=False,
        )

        assert result.data == []


# ---------------------------------------------------------------------------
# TestCreateNode
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateNode:
    """Tests for the create_node handler."""

    @pytest.mark.asyncio
    async def test_creates_and_returns_node(self) -> None:
        """Handler awaits node_service.create_node and returns the result."""
        mock_service = MagicMock()
        mock_service.create_node = AsyncMock(return_value=_node_response("node-new"))

        from chaoscypher_core.models import NodeCreate

        node_create = NodeCreate(template_id="tpl-1", label="Alice")

        result = await create_node(
            _="test-user",
            node_create=node_create,
            node_service=mock_service,
        )

        mock_service.create_node.assert_awaited_once_with(node_create)
        assert result.id == "node-new"
        assert result.label == "Alice"

    @pytest.mark.asyncio
    async def test_passes_node_create_object_directly(self) -> None:
        """Handler passes the full NodeCreate object (not a dict) to the service."""
        mock_service = MagicMock()
        returned = _node_response("node-xyz")
        mock_service.create_node = AsyncMock(return_value=returned)

        from chaoscypher_core.models import NodeCreate

        node_create = NodeCreate(template_id="tpl-2", label="Bob")

        await create_node(
            _="test-user",
            node_create=node_create,
            node_service=mock_service,
        )

        call_arg = mock_service.create_node.call_args[0][0]
        assert call_arg is node_create


# ---------------------------------------------------------------------------
# TestGetNode
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetNode:
    """Tests for the get_node handler."""

    @pytest.mark.asyncio
    async def test_returns_node_by_id(self) -> None:
        """Handler calls node_service.get_node with node_id kwarg and returns result."""
        mock_service = MagicMock()
        mock_service.get_node.return_value = _node_response("node-42")

        result = await get_node(_="test-user", node_id="node-42", node_service=mock_service)

        mock_service.get_node.assert_called_once_with(node_id="node-42")
        assert result.id == "node-42"

    @pytest.mark.asyncio
    async def test_propagates_service_exception(self) -> None:
        """Handler propagates exceptions raised by the service (e.g. 404)."""
        mock_service = MagicMock()
        mock_service.get_node.side_effect = HTTPException(status_code=404, detail="Not found")

        with pytest.raises(HTTPException) as exc_info:
            await get_node(_="test-user", node_id="missing", node_service=mock_service)

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# TestGetNodeConnections
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetNodeConnections:
    """Tests for the get_node_connections handler."""

    @pytest.mark.asyncio
    async def test_returns_connections_response(self) -> None:
        """Handler delegates to node_service.get_node_connections and returns the result."""
        mock_service = MagicMock()
        mock_service.get_node_connections.return_value = _connections_response()

        result = await get_node_connections(
            _="test-user",
            node_id="node-1",
            node_service=mock_service,
            sort_by="edge_count",
            page=1,
            page_size=50,
        )

        mock_service.get_node_connections.assert_called_once_with(
            node_id="node-1",
            sort_by="edge_count",
            page=1,
            page_size=50,
        )
        assert isinstance(result, ConnectionsResponse)

    @pytest.mark.asyncio
    async def test_passes_sort_and_pagination_params(self) -> None:
        """Handler forwards sort_by, page, and page_size to the service."""
        mock_service = MagicMock()
        mock_service.get_node_connections.return_value = _connections_response()

        await get_node_connections(
            _="test-user",
            node_id="node-5",
            node_service=mock_service,
            sort_by="label",
            page=3,
            page_size=10,
        )

        mock_service.get_node_connections.assert_called_once_with(
            node_id="node-5",
            sort_by="label",
            page=3,
            page_size=10,
        )


# ---------------------------------------------------------------------------
# TestGetNodeCitations
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetNodeCitations:
    """Tests for the get_node_citations handler."""

    @pytest.mark.asyncio
    async def test_returns_citations_response(self) -> None:
        """Handler delegates to node_service.get_node_citations and returns the result."""
        mock_service = MagicMock()
        mock_service.get_node_citations.return_value = _citations_response()

        result = await get_node_citations(
            _="test-user",
            node_id="node-1",
            node_service=mock_service,
            page=1,
            page_size=50,
        )

        mock_service.get_node_citations.assert_called_once_with(
            node_id="node-1",
            page=1,
            page_size=50,
        )
        assert isinstance(result, CitationListResponse)

    @pytest.mark.asyncio
    async def test_passes_pagination_params(self) -> None:
        """Handler forwards page and page_size to the service."""
        mock_service = MagicMock()
        mock_service.get_node_citations.return_value = _citations_response()

        await get_node_citations(
            _="test-user",
            node_id="node-7",
            node_service=mock_service,
            page=2,
            page_size=25,
        )

        mock_service.get_node_citations.assert_called_once_with(
            node_id="node-7",
            page=2,
            page_size=25,
        )


# ---------------------------------------------------------------------------
# TestUpdateNode
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateNode:
    """Tests for the update_node handler."""

    @pytest.mark.asyncio
    async def test_updates_and_returns_node(self) -> None:
        """Handler awaits node_service.update_node and returns the updated node."""
        mock_service = MagicMock()
        updated = _node_response("node-5")
        updated.label = "Updated Alice"
        mock_service.update_node = AsyncMock(return_value=updated)

        from chaoscypher_core.models import NodeUpdate

        node_update = NodeUpdate(label="Updated Alice")

        result = await update_node(
            _="test-user",
            node_id="node-5",
            node_update=node_update,
            node_service=mock_service,
        )

        mock_service.update_node.assert_awaited_once_with("node-5", node_update)
        assert result.id == "node-5"

    @pytest.mark.asyncio
    async def test_propagates_not_found_exception(self) -> None:
        """Handler propagates HTTPException 404 when node does not exist."""
        mock_service = MagicMock()
        mock_service.update_node = AsyncMock(
            side_effect=HTTPException(status_code=404, detail="Not found")
        )

        from chaoscypher_core.models import NodeUpdate

        with pytest.raises(HTTPException) as exc_info:
            await update_node(
                _="test-user",
                node_id="missing",
                node_update=NodeUpdate(label="X"),
                node_service=mock_service,
            )

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# TestUpdateNodePosition
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateNodePosition:
    """Tests for the update_node_position handler."""

    @pytest.mark.asyncio
    async def test_updates_position_and_returns_node(self) -> None:
        """Handler calls node_service.update_node_position and returns the node."""
        mock_service = MagicMock()
        mock_service.update_node_position.return_value = _node_response("node-8")

        from chaoscypher_core.models import NodePosition

        position_update = NodePositionUpdateRequest(position=NodePosition(x=10.0, y=20.0))

        result = await update_node_position(
            _="test-user",
            node_id="node-8",
            position_update=position_update,
            node_service=mock_service,
        )

        mock_service.update_node_position.assert_called_once_with("node-8", position_update)
        assert result.id == "node-8"

    @pytest.mark.asyncio
    async def test_propagates_not_found_exception(self) -> None:
        """Handler propagates HTTPException 404 when node does not exist."""
        mock_service = MagicMock()
        mock_service.update_node_position.side_effect = HTTPException(
            status_code=404, detail="Not found"
        )

        from chaoscypher_core.models import NodePosition

        position_update = NodePositionUpdateRequest(position=NodePosition(x=0.0, y=0.0))

        with pytest.raises(HTTPException) as exc_info:
            await update_node_position(
                _="test-user",
                node_id="missing",
                position_update=position_update,
                node_service=mock_service,
            )

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# TestDeleteNode
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeleteNode:
    """Tests for the delete_node handler."""

    @pytest.mark.asyncio
    async def test_calls_delete_and_returns_none(self) -> None:
        """Handler delegates to node_service.delete_node and returns None (204)."""
        mock_service = MagicMock()
        mock_service.delete_node.return_value = None

        result = await delete_node(
            _="test-user",
            node_id="node-del",
            node_service=mock_service,
        )

        mock_service.delete_node.assert_called_once_with("node-del")
        assert result is None

    @pytest.mark.asyncio
    async def test_propagates_not_found_exception(self) -> None:
        """Handler propagates HTTPException 404 when node does not exist."""
        mock_service = MagicMock()
        mock_service.delete_node.side_effect = HTTPException(status_code=404, detail="Not found")

        with pytest.raises(HTTPException) as exc_info:
            await delete_node(
                _="test-user",
                node_id="missing",
                node_service=mock_service,
            )

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# TestBatchNodesOperation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBatchNodesOperation:
    """Tests for the batch_nodes_operation handler."""

    @pytest.mark.asyncio
    async def test_enqueues_task_and_returns_bulk_response(self) -> None:
        """Handler enqueues a bulk_nodes task and returns a BulkResponse with task_id."""
        settings = _make_settings()
        request = BulkRequest(
            operations=[
                BulkOperationRequest(operation="create", data={"label": "Alice"}),
                BulkOperationRequest(operation="delete", data={"id": "node-x"}),
            ]
        )

        with patch("chaoscypher_cortex.features.nodes.api.queue_client") as mock_queue:
            mock_queue.enqueue_task = AsyncMock(return_value="task-abc")

            result = await batch_nodes_operation(
                _="test-user",
                request=request,
                settings=settings,
            )

        mock_queue.enqueue_task.assert_awaited_once()
        call_kwargs = mock_queue.enqueue_task.call_args[1]
        assert call_kwargs["operation"] == "bulk_nodes"
        assert len(call_kwargs["data"]["operations"]) == 2
        assert result.task_id == "task-abc"
        assert result.status == "queued"

    @pytest.mark.asyncio
    async def test_message_includes_operation_count(self) -> None:
        """Handler message reports the number of operations queued."""
        settings = _make_settings()
        operations = [
            BulkOperationRequest(operation="create", data={"label": f"Node{i}"}) for i in range(5)
        ]
        request = BulkRequest(operations=operations)

        with patch("chaoscypher_cortex.features.nodes.api.queue_client") as mock_queue:
            mock_queue.enqueue_task = AsyncMock(return_value="task-xyz")

            result = await batch_nodes_operation(
                _="test-user",
                request=request,
                settings=settings,
            )

        assert "5" in result.message

    @pytest.mark.asyncio
    async def test_uses_background_priority(self) -> None:
        """Handler uses settings.priorities.background when enqueueing."""
        settings = _make_settings()
        settings.priorities.background = 99
        request = BulkRequest(
            operations=[BulkOperationRequest(operation="delete", data={"id": "n1"})]
        )

        with patch("chaoscypher_cortex.features.nodes.api.queue_client") as mock_queue:
            mock_queue.enqueue_task = AsyncMock(return_value="task-prio")

            await batch_nodes_operation(
                _="test-user",
                request=request,
                settings=settings,
            )

        call_kwargs = mock_queue.enqueue_task.call_args[1]
        assert call_kwargs["priority"] == 99
