# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Export Service.

Business logic for graph export/import operations.
"""

from typing import TYPE_CHECKING

import structlog

from chaoscypher_core.exceptions import ExternalServiceError, ValidationError
from chaoscypher_core.operations.queue_utils import (
    queue_import_ccx,
)
from chaoscypher_cortex.features.export.models import ExportResponse, ImportResponse


if TYPE_CHECKING:
    from chaoscypher_core.app_config import Settings
    from chaoscypher_core.operations.export_operations_service import (
        ExportOperationsService,
    )

logger = structlog.get_logger(__name__)


class ExportService:
    """Service for graph export/import operations."""

    def __init__(self, export_operations: ExportOperationsService, settings: Settings):
        """Initialize export service.

        Args:
            export_operations: Export operations service for queueing export tasks
            settings: Application settings

        """
        self.export_operations = export_operations
        self.settings = settings

    async def queue_export(
        self,
        include_templates: bool = True,
        include_knowledge: bool = True,
        include_workflows: bool = True,
        include_sources: bool = True,
        include_embeddings: bool = False,
    ) -> ExportResponse:
        """Queue a graph export operation.

        Args:
            include_templates: Include user-created templates
            include_knowledge: Include knowledge graph nodes and edges
            include_workflows: Include workflows and triggers
            include_sources: Include document sources and metadata
            include_embeddings: Include embedding vectors in export

        Returns:
            ExportResponse with task_id for tracking

        Note:
            The export operation runs asynchronously.
            Use /api/v1/queue/tasks/{task_id}/result to download when complete.

        """
        if not self.export_operations:
            msg = "Operations"
            raise ExternalServiceError(msg, "Service unavailable")

        task_id = await self.export_operations.queue_export(
            database_name=self.settings.current_database,
            include_templates=include_templates,
            include_knowledge=include_knowledge,
            include_lenses=False,
            include_workflows=include_workflows,
            include_sources=include_sources,
            include_embeddings=include_embeddings,
            priority=self.settings.priorities.background,
        )

        return ExportResponse(
            task_id=task_id,
            status="queued",
            message="Graph export queued. Use /api/v1/queue/tasks/{task_id}/result to download when complete.",
        )

    async def queue_import(
        self, file_content: bytes, filename: str, merge: bool = False
    ) -> ImportResponse:
        """Queue a CCX import operation.

        Args:
            file_content: CCX file content
            filename: Original filename
            merge: Whether to merge (True) or replace (False)

        Returns:
            ImportResponse with task_id for tracking

        Note:
            The import operation runs asynchronously.
            Use /api/v1/queue/tasks/{task_id}/result to get import results.

        """
        if filename.lower().endswith(".cxl"):
            raise ValidationError(
                "The .cxl bundle format has been replaced by .ccx. Re-export the bundle."
            )

        task_id = await queue_import_ccx(
            file_content=file_content,
            database_name=self.settings.current_database,
            merge=merge,
            priority=self.settings.priorities.background,
            extra_metadata={"filename": filename},
        )

        return ImportResponse(
            task_id=task_id,
            status="queued",
            message=f"CCX import queued for file: {filename}. Use /api/v1/queue/tasks/{task_id}/result to get results.",
        )

    async def queue_export_by_sources(
        self,
        source_ids: list[str],
        include_templates: bool = True,
        include_embeddings: bool = False,
    ) -> ExportResponse:
        """Queue a source-filtered graph export operation.

        Creates an export containing only data related to specified sources:
        - Entities cited by the sources
        - Edges where both endpoints are in the entity set
        - Templates linked to or used by the sources
        - Source metadata, chunks, citations, and tags

        Args:
            source_ids: List of source UUIDs to include
            include_templates: Include templates linked to these sources
            include_embeddings: Include embeddings in exported chunks

        Returns:
            ExportResponse with task_id for tracking

        Note:
            The export operation runs asynchronously.
            Use /api/v1/queue/tasks/{task_id}/result to download when complete.

        """
        if not self.export_operations:
            msg = "Operations"
            raise ExternalServiceError(msg, "Service unavailable")

        task_id = await self.export_operations.queue_export_by_sources(
            source_ids=source_ids,
            database_name=self.settings.current_database,
            include_templates=include_templates,
            include_embeddings=include_embeddings,
            priority=self.settings.priorities.background,
        )

        return ExportResponse(
            task_id=task_id,
            status="queued",
            message=f"Source-filtered export queued for {len(source_ids)} sources. Use /api/v1/queue/tasks/{{task_id}}/result to download when complete.",
        )
