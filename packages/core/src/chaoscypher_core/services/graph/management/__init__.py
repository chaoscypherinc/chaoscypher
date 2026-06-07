# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Graph Management Services - CRUD Operations for Knowledge Graph.

Management-layer services for graph entities (nodes, edges, templates, sources).
These handle database operations and business logic.

Components:
- NodeService: CRUD operations for graph nodes
- EdgeService: CRUD operations for graph edges
- TemplateService: CRUD operations for templates
- SourceService: CRUD operations for document sources
- TemplateEmbeddingService: Embedding generation for templates

Example:
    from chaoscypher_core.services.graph.management import NodeService, EdgeService

    node_service = NodeService(graph_repo, database_name)
    nodes = node_service.list_nodes()

"""

from chaoscypher_core.services.graph.management.edge import EdgeService
from chaoscypher_core.services.graph.management.embedding import TemplateEmbeddingService
from chaoscypher_core.services.graph.management.node import NodeService
from chaoscypher_core.services.graph.management.source import SourceService
from chaoscypher_core.services.graph.management.template import TemplateService


__all__ = [
    "EdgeService",
    "NodeService",
    "SourceService",
    "TemplateEmbeddingService",
    "TemplateService",
]
