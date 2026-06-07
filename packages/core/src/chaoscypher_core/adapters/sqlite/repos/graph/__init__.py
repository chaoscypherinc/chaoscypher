# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Graph Repository - SQLite-backed knowledge graph storage.

Provides CRUD operations for nodes, edges, and templates using SQLite.
Lives under the adapter layer because the implementation issues raw SQL
against adapter-specific schema (``GraphNode``, ``GraphEdge``,
``GraphTemplate``) and composes mixins that bind directly to SQLModel
entities.
"""

from chaoscypher_core.adapters.sqlite.repos.graph.cleanup import remove_corrupt_nodes
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_repository import GraphRepository


__all__ = [
    "GraphRepository",
    "remove_corrupt_nodes",
]
