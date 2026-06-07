# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Bulk Operations - batch graph operations for nodes, edges, and templates.

This sub-package splits the bulk operations service into focused modules:

- bulk_service: Thin orchestrator with queue methods and generic execution engine
- bulk_node_ops: Node-specific bulk operation handler
- bulk_edge_ops: Edge-specific bulk operation handler
- bulk_template_ops: Template-specific bulk operation handler

Example:
    from chaoscypher_core.operations.bulk import BulkOperationsService

    service = BulkOperationsService(graph_repository=repo, settings=settings)
    service.register_handlers()

"""

from chaoscypher_core.operations.bulk.bulk_service import (
    BulkOperationsService,
)


__all__ = [
    "BulkOperationsService",
]
