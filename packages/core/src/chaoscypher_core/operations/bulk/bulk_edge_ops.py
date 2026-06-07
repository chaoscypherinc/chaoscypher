# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Bulk edge operation handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.operations.bulk.bulk_service import (
        BulkOperationsService,
    )


logger = structlog.get_logger(__name__)


async def bulk_edges_handler(
    service: BulkOperationsService,
    data: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Execute bulk edge operations.

    Args:
        service: BulkOperationsService instance with graph_repository
        data: Task data containing operations list
        metadata: Task metadata
        task_id: Task ID for tracking

    Returns:
        Result dictionary with success/failed counts and details

    """
    from chaoscypher_core.models import EdgeCreate, EdgeUpdate

    operations = data.get("operations", [])
    logger.info("bulk_edges_operation_executing", operation_count=len(operations))

    # Note: With SQLite storage, no reload needed - database is always consistent

    # Use generic helper to handle operations
    return await service.execute_bulk_operations(
        operations=operations,
        entity_type="edge",
        create_model_class=EdgeCreate,
        update_model_class=EdgeUpdate,
        create_method="create_edge",
        update_method="update_edge",
        delete_method="delete_edge",
        id_field_alternatives=["id", "edge_id"],
    )
