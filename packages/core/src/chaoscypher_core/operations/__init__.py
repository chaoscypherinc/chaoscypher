# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Operations - shared service library for background worker tasks.

Queue-based background task processing for long-running operations.

This module provides async task queue integration via Valkey for
operations that need to run in the background, including bulk graph operations,
imports, exports, workflow execution, discovery analysis, and lens building.

Components:
- OperationsRepository: Interface adapter for Core's WorkflowExecutionService

Specialized Services (used by Neuron workers and API layer):
- BulkOperationsService: Bulk node/edge/template operations
- ExportOperationsService: Graph export operations
- ImportOperationsService: File import operations (in ``importing`` sub-package)
- ChunkExtractionOperationsService: Chunk extraction operations (in ``extraction`` sub-package)
- WorkflowOperationsService: Workflow execution

Queue utilities (used by API layer for import operations):
- queue_utils: Shared queue submission functions for import operations

Example:
    from chaoscypher_core.operations.export_operations_service import (
        ExportOperationsService,
    )

    # API layer uses specialized services directly
    service = ExportOperationsService(graph_repository=repo)
    task_id = await service.queue_export(include_knowledge=True)

"""

from chaoscypher_core.operations.repository import OperationsRepository


__all__ = [
    "OperationsRepository",
]
