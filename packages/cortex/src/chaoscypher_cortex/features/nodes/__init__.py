# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Nodes Feature.

Knowledge graph node management with CRUD operations.

This feature provides comprehensive node management for the RDF knowledge graph
including creation, retrieval, updates, and deletion with search integration.
Follows standard VSA three-layer architecture with repository, service, and API.
Nodes represent entities in the knowledge graph with typed properties, templates,
and relationships. Includes automatic search index synchronization.

Components:
- NodeService: Business logic for node operations and validation
- GraphNodeRepository: RDF graph data access via Core GraphRepository
- SqlNodeRepository: SQL data access for citations, sources, chunks
- NodeResponse: Pydantic response DTOs for API serialization
- router: FastAPI endpoints for /api/v1/nodes

Architecture:
Pattern 3 (Engine Wrapper) with split repositories:
- GraphNodeRepository: Wraps Core's GraphRepository for RDF operations
- SqlNodeRepository: Handles SQLModel queries for citations/sources
Session is injected at factory level per CLAUDE.md guidelines.

Example:
    from chaoscypher_cortex.features.nodes import NodeService

    # Create and query nodes
    service = NodeService(graph_repo, sql_repo, search_repo, settings)
    node = service.create_node("Person", {"name": "Alice", "age": 30})
    results = service.list_nodes()

"""

from chaoscypher_cortex.features.nodes.api import router
from chaoscypher_cortex.features.nodes.graph_repository import GraphNodeRepository
from chaoscypher_cortex.features.nodes.models import (
    ConnectedNodeResponse,
    ConnectionsResponse,
    NodeResponse,
)
from chaoscypher_cortex.features.nodes.service import NodeService
from chaoscypher_cortex.features.nodes.sql_repository import SqlNodeRepository


__all__ = [
    "ConnectedNodeResponse",
    "ConnectionsResponse",
    "GraphNodeRepository",
    "NodeResponse",
    "NodeService",
    "SqlNodeRepository",
    "router",
]
