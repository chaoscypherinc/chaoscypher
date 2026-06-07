# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Export Operations Service - handles graph export operations."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.constants import QUEUE_OPERATIONS
from chaoscypher_core.queue import queue_client
from chaoscypher_core.queue.handler_spec import HandlerSpec
from chaoscypher_core.services.events import event_bus


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository


logger = structlog.get_logger(__name__)


class ExportOperationsService:
    """Service for queuing graph export operations.

    Handles exporting graph data (templates, knowledge, workflows)
    to CCX format. All exports are queued and executed asynchronously.
    """

    def __init__(
        self,
        graph_repository: GraphRepository | None = None,
        workflow_db: Any = None,
    ) -> None:
        """Initialize export operations service.

        Args:
            graph_repository: GraphRepository for graph operations
            workflow_db: WorkflowDatabase for workflow data

        """
        self.graph_repository = graph_repository
        self.workflow_db = workflow_db

        # Export handlers are pure reads (no database writes, no side
        # effects) and therefore fully idempotent — safe to retry.
        self.operation_handlers = {
            "export_graph": HandlerSpec(
                handler=self._export_graph_handler,
                retry_on_crash=True,
            ),
            "export_by_sources": HandlerSpec(
                handler=self._export_by_sources_handler,
                retry_on_crash=True,
            ),
        }

        logger.info("export_operations_service_initialized")

    def register_handlers(self) -> None:
        """Register export operation handlers with queue."""
        queue_client.register_handlers(QUEUE_OPERATIONS, self.operation_handlers)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Queue methods
    # ------------------------------------------------------------------
    async def queue_export(
        self,
        *,
        database_name: str,
        include_templates: bool = True,
        include_knowledge: bool = True,
        include_lenses: bool = True,
        include_workflows: bool = True,
        include_sources: bool = True,
        include_embeddings: bool = False,
        lens_id: str | None = None,
        title: str | None = None,
        priority: int = 50,
        extra_metadata: dict[str, Any] | None = None,
    ) -> str:
        """Queue graph export operation.

        Args:
            database_name: Target database for scoping cancel-by-metadata.
            include_templates: Include templates in export.
            include_knowledge: Include knowledge graph in export.
            include_lenses: Include lenses in export.
            include_workflows: Include workflows in export.
            include_sources: Include document sources in export.
            include_embeddings: Include embedding vectors in export.
            lens_id: Optional lens ID to filter export.
            title: Optional display title for the graph snapshot preview.
            priority: Task priority (0-100, higher = more priority).
            extra_metadata: Extra keys to merge into the task metadata.

        Returns:
            Task ID for tracking.

        """
        metadata: dict[str, Any] = {
            "database_name": database_name,
            "operation_type": "export_graph",
        }
        if extra_metadata:
            metadata.update(extra_metadata)
        metadata["database_name"] = database_name
        metadata["operation_type"] = "export_graph"
        return await queue_client.enqueue_task(
            queue=QUEUE_OPERATIONS,
            operation="export_graph",
            data={
                "include_templates": include_templates,
                "include_knowledge": include_knowledge,
                "include_lenses": include_lenses,
                "include_workflows": include_workflows,
                "include_sources": include_sources,
                "include_embeddings": include_embeddings,
                "lens_id": lens_id,
                "title": title,
            },
            priority=priority,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Operation handlers
    # ------------------------------------------------------------------
    async def _export_graph_handler(
        self,
        data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute graph export operation.

        Args:
            data: Task data with export configuration
            metadata: Task metadata
            task_id: Task ID for tracking

        Returns:
            Result dictionary with filename, base64 content, and size

        """
        from chaoscypher_core.app_config import get_settings
        from chaoscypher_core.app_config.engine_factory import build_engine_settings
        from chaoscypher_core.database.adapter_factory import get_sqlite_adapter
        from chaoscypher_core.services.export import ExportRepository

        include_templates = data.get("include_templates", True)
        include_knowledge = data.get("include_knowledge", True)
        include_lenses = data.get("include_lenses", True)
        include_workflows = data.get("include_workflows", True)
        include_sources = data.get("include_sources", True)
        include_embeddings = data.get("include_embeddings", False)
        lens_id = data.get("lens_id")
        title = data.get("title")

        logger.info(
            "export_graph_operation_executing",
            lens_id=lens_id,
            include_templates=include_templates,
            include_knowledge=include_knowledge,
            include_lenses=include_lenses,
            include_workflows=include_workflows,
            include_sources=include_sources,
            include_embeddings=include_embeddings,
        )

        # Build the EngineSettings view at the operation boundary.
        # ExportRepository is typed against EngineSettings; threading the
        # engine view here (rather than the app Settings singleton) keeps the
        # engine free of the backend Settings type post-Tier-2 unification.
        engine_settings = build_engine_settings(get_settings())

        # Use singleton adapter (already connected, no disconnect needed)
        adapter = get_sqlite_adapter(engine_settings.current_database)

        export_repository = ExportRepository(
            graph_repository=self.graph_repository,
            settings=engine_settings,
            workflow_db=self.workflow_db,
            sources_repository=adapter,  # Implements SourceStorageProtocol
            adapter=adapter,
        )

        zip_buffer = export_repository.export_graph(
            include_templates=include_templates,
            include_knowledge=include_knowledge,
            include_lenses=include_lenses,
            include_workflows=include_workflows,
            include_sources=include_sources,
            include_embeddings=include_embeddings,
            lens_id=lens_id,
            title=title,
        )

        zip_buffer.seek(0)
        zip_content = zip_buffer.read()
        encoded_content = base64.b64encode(zip_content).decode("utf-8")

        filename = export_repository.get_export_filename()

        event_bus.emit(
            "task_completed",
            action="Graph export complete",
            source="worker",
            details={"filename": filename, "size_bytes": len(zip_content)},
            database_name=engine_settings.current_database,
        )

        return {
            "filename": filename,
            "content": encoded_content,
            "size_bytes": len(zip_content),
        }

    async def queue_export_by_sources(
        self,
        source_ids: list[str],
        *,
        database_name: str,
        include_templates: bool = True,
        include_embeddings: bool = False,
        title: str | None = None,
        priority: int = 50,
        extra_metadata: dict[str, Any] | None = None,
    ) -> str:
        """Queue source-filtered export operation.

        Args:
            source_ids: List of source UUIDs to include.
            database_name: Target database for scoping cancel-by-metadata.
            include_templates: Include templates linked to these sources.
            include_embeddings: Include embeddings in exported chunks.
            title: Optional display title for the graph snapshot preview.
            priority: Task priority (0-100, higher = more priority).
            extra_metadata: Extra keys to merge into the task metadata.

        Returns:
            Task ID for tracking.

        """
        metadata: dict[str, Any] = {
            "database_name": database_name,
            "operation_type": "export_by_sources",
            "source_count": len(source_ids),
        }
        if extra_metadata:
            metadata.update(extra_metadata)
        metadata["database_name"] = database_name
        metadata["operation_type"] = "export_by_sources"
        return await queue_client.enqueue_task(
            queue=QUEUE_OPERATIONS,
            operation="export_by_sources",
            data={
                "source_ids": source_ids,
                "include_templates": include_templates,
                "include_embeddings": include_embeddings,
                "title": title,
            },
            priority=priority,
            metadata=metadata,
        )

    async def _export_by_sources_handler(
        self,
        data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute source-filtered export operation.

        Args:
            data: Task data with source_ids and export configuration
            metadata: Task metadata
            task_id: Task ID for tracking

        Returns:
            Result dictionary with filename, base64 content, and size

        """
        from chaoscypher_core.app_config import get_settings
        from chaoscypher_core.app_config.engine_factory import build_engine_settings
        from chaoscypher_core.database.adapter_factory import get_sqlite_adapter
        from chaoscypher_core.services.export import ExportRepository

        source_ids = data.get("source_ids", [])
        include_templates = data.get("include_templates", True)
        include_embeddings = data.get("include_embeddings", False)
        title = data.get("title")

        logger.info(
            "export_by_sources_operation_executing",
            source_count=len(source_ids),
            include_templates=include_templates,
            include_embeddings=include_embeddings,
        )

        # Build the EngineSettings view at the operation boundary (see the
        # graph-export handler above for the rationale).
        engine_settings = build_engine_settings(get_settings())

        # Use singleton adapter (already connected, no disconnect needed)
        adapter = get_sqlite_adapter(engine_settings.current_database)

        export_repository = ExportRepository(
            graph_repository=self.graph_repository,
            settings=engine_settings,
            workflow_db=self.workflow_db,
            sources_repository=adapter,  # Implements SourceStorageProtocol
            adapter=adapter,
        )

        zip_buffer = export_repository.export_by_sources(
            source_ids=source_ids,
            include_templates=include_templates,
            include_embeddings=include_embeddings,
            title=title,
        )

        zip_buffer.seek(0)
        zip_content = zip_buffer.read()
        encoded_content = base64.b64encode(zip_content).decode("utf-8")

        filename = f"sources_export_{len(source_ids)}_{export_repository.get_export_filename()}"

        event_bus.emit(
            "task_completed",
            action="Source export complete",
            source="worker",
            details={
                "filename": filename,
                "source_count": len(source_ids),
                "size_bytes": len(zip_content),
            },
            database_name=engine_settings.current_database,
        )

        return {
            "filename": filename,
            "content": encoded_content,
            "size_bytes": len(zip_content),
        }
