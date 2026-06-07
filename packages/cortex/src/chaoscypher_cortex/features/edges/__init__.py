# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Edges Feature.

Knowledge graph relationship management with CRUD operations.

This feature provides comprehensive edge (relationship) management for the RDF
knowledge graph including creation, retrieval, updates, and deletion. Edges
connect nodes with typed relationships and optional properties. Uses engine
EdgeService directly without wrapper layer for simplified architecture.

Components:
- EdgeService: Business logic for relationship operations (uses engine directly)
- EdgeResponse: Pydantic response DTOs for API serialization
- router: FastAPI endpoints for /api/v1/edges

Architecture:
Simplified VSA - uses engine EdgeService directly without wrapper layer.
Factory function in api.py provides dependency injection with GraphRepository.

Example:
    from chaoscypher_core.services.graph.management.edge import EdgeService
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository

    # Create relationships between nodes
    graph_repository = GraphRepository(session=session, database_name="default")
    service = EdgeService(graph_repository=graph_repository)
    edge = service.create_edge(edge_create)

"""

from chaoscypher_core.services.graph.management.edge import EdgeService
from chaoscypher_cortex.features.edges.api import router
from chaoscypher_cortex.features.edges.models import EdgeResponse


__all__ = [
    "EdgeResponse",
    "EdgeService",
    "router",
]
