# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Edge Service for chaoscypher-engine.

Business logic for edge operations - thin wrapper around GraphRepository.
"""

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.exceptions import NotFoundError, ValidationError


if TYPE_CHECKING:
    from chaoscypher_core.models import EdgeCreate, EdgeUpdate
    from chaoscypher_core.ports.graph import GraphRepositoryProtocol

logger = structlog.get_logger(__name__)


class EdgeService:
    """Service for edge business logic.

    Thin wrapper around GraphRepository that provides validation
    and standardized error handling for edge operations.
    """

    def __init__(self, graph_repository: GraphRepositoryProtocol):
        """Initialize edge service.

        Args:
            graph_repository: GraphRepository implementation

        """
        self.graph_repository = graph_repository

    def list_edges(
        self,
        source_node_id: str | None = None,
        target_node_id: str | None = None,
        source_ids: list[str] | None = None,
        page: int = 1,
        page_size: int = 50,
        minimal: bool = False,
    ) -> dict[str, Any]:
        """List edges with pagination.

        Args:
            source_node_id: Filter by source node (optional)
            target_node_id: Filter by target node (optional)
            source_ids: Filter by source document IDs (optional)
            page: Page number (1-indexed)
            page_size: Items per page
            minimal: If True, only load essential fields (excludes properties)
                     for better performance with large graphs

        Returns:
            Dict with keys:
                - data: List of edge dicts
                - pagination: Pagination metadata (total, page, page_size, etc.)

        """
        # Calculate skip from page
        skip = (page - 1) * page_size

        # Get total count. list_edges (below) hides disabled-source rows by
        # default, so the count must match or pagination over-reports.
        total = self.graph_repository.count_edges(
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            source_ids=source_ids,
            include_disabled_sources=False,
        )

        # Get paginated edges
        edges = self.graph_repository.list_edges(
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            source_ids=source_ids,
            skip=skip,
            limit=page_size,
            minimal=minimal,
        )

        total_pages = (total + page_size - 1) // page_size

        # Minimal mode excludes heavyweight fields (properties, timestamps)
        # to reduce JSON payload for graph canvas rendering.
        data: list[dict[str, Any]]
        if minimal:
            data = [
                {
                    "id": e.id,
                    "source_node_id": e.source_node_id,
                    "target_node_id": e.target_node_id,
                    "template_id": e.template_id,
                    "label": e.label,
                }
                for e in edges
            ]
        else:
            data = [
                {
                    "id": e.id,
                    "source_node_id": e.source_node_id,
                    "target_node_id": e.target_node_id,
                    "template_id": e.template_id,
                    "label": e.label,
                    "properties": e.properties,
                    "created_at": e.created_at,
                    "updated_at": e.updated_at,
                }
                for e in edges
            ]

        return {
            "data": data,
            "pagination": {
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1,
            },
        }

    def get_edge(self, edge_id: str) -> dict[str, Any]:
        """Get edge by ID.

        Args:
            edge_id: Edge ID

        Returns:
            Edge dictionary

        Raises:
            NotFoundError: If edge not found

        """
        edge = self.graph_repository.get_edge(edge_id)

        if not edge:
            msg = "Edge"
            raise NotFoundError(msg, edge_id)

        return {
            "id": edge.id,
            "source_node_id": edge.source_node_id,
            "target_node_id": edge.target_node_id,
            "template_id": edge.template_id,
            "label": edge.label,
            "properties": edge.properties,
            "created_at": edge.created_at,
            "updated_at": edge.updated_at,
        }

    def create_edge(self, edge_create: EdgeCreate) -> dict[str, Any]:
        """Create new edge.

        Args:
            edge_create: Edge creation data

        Returns:
            Created edge dictionary

        Raises:
            NotFoundError: If source/target node or template not found
            ValidationError: If template is not an edge template

        """
        # Validate source node exists
        source_node = self.graph_repository.get_node(edge_create.source_node_id)
        if not source_node:
            msg = "Node"
            raise NotFoundError(msg, edge_create.source_node_id)

        # Validate target node exists
        target_node = self.graph_repository.get_node(edge_create.target_node_id)
        if not target_node:
            msg = "Node"
            raise NotFoundError(msg, edge_create.target_node_id)

        # Validate template exists
        template = self.graph_repository.get_template(edge_create.template_id)
        if not template:
            msg = "Template"
            raise NotFoundError(msg, edge_create.template_id)

        # Validate template is an edge template
        if template.template_type != "edge":
            msg = f"Template {edge_create.template_id} is not an edge template (type: {template.template_type})"
            raise ValidationError(msg)

        edge = self.graph_repository.create_edge(edge_create)
        return {
            "id": edge.id,
            "source_node_id": edge.source_node_id,
            "target_node_id": edge.target_node_id,
            "template_id": edge.template_id,
            "label": edge.label,
            "properties": edge.properties,
            "created_at": edge.created_at,
            "updated_at": edge.updated_at,
        }

    def update_edge(self, edge_id: str, edge_update: EdgeUpdate) -> dict[str, Any]:
        """Update existing edge.

        Args:
            edge_id: Edge ID
            edge_update: Edge update data

        Returns:
            Updated edge dictionary

        Raises:
            NotFoundError: If edge not found

        """
        edge = self.graph_repository.update_edge(edge_id, edge_update)
        if not edge:
            msg = "Edge"
            raise NotFoundError(msg, edge_id)

        return {
            "id": edge.id,
            "source_node_id": edge.source_node_id,
            "target_node_id": edge.target_node_id,
            "template_id": edge.template_id,
            "label": edge.label,
            "properties": edge.properties,
            "created_at": edge.created_at,
            "updated_at": edge.updated_at,
        }

    def delete_edge(self, edge_id: str) -> None:
        """Delete edge by ID.

        Args:
            edge_id: Edge ID

        Raises:
            NotFoundError: If edge not found

        """
        success = self.graph_repository.delete_edge(edge_id)

        if not success:
            msg = "Edge"
            raise NotFoundError(msg, edge_id)
