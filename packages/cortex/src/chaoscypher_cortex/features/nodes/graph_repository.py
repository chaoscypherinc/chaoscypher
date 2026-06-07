# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Graph Node Repository.

Data access layer for node operations via Core GraphRepository (RDF/knowledge graph).
"""

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository
    from chaoscypher_core.models import Node


class GraphNodeRepository:
    """Repository for node operations via Core GraphRepository.

    Delegates all RDF/knowledge graph operations to the engine's GraphRepository.
    This is a thin wrapper that provides VSA-compliant interface for node CRUD.
    """

    def __init__(self, graph_repository: GraphRepository):
        """Initialize graph node repository.

        Args:
            graph_repository: Core GraphRepository instance for RDF operations

        """
        self.graph_repository = graph_repository

    def get_node(self, node_id: str) -> Node | None:
        """Get node by ID.

        Args:
            node_id: Node ID

        Returns:
            Node object or None if not found

        """
        return self.graph_repository.get_node(node_id)

    def update_node_position(self, node_id: str, x: float, y: float) -> Node | None:
        """Update only node position (optimized for layout saving).

        Args:
            node_id: Node ID
            x: X coordinate
            y: Y coordinate

        Returns:
            Updated Node object or None if not found

        """
        return self.graph_repository.update_node_position(node_id, x, y)
