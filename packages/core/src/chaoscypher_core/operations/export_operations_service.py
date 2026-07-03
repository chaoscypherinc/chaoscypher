# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Export Operations Service - handles graph export operations."""

from __future__ import annotations

import base64
from typing import Any

import structlog

from chaoscypher_core.constants import QUEUE_OPERATIONS
from chaoscypher_core.queue import queue_client
from chaoscypher_core.queue.handler_spec import HandlerSpec
from chaoscypher_core.services.events import event_bus


logger = structlog.get_logger(__name__)


class ExportOperationsService:
    """Service for queuing graph export operations.

    Handles exporting graph data (templates, knowledge, workflows)
    to CCX format. All exports are queued and executed asynchronously.
    """

    def __init__(
        self,
        workflow_db: Any = None,
    ) -> None:
        """Initialize export operations service.

        Args:
            workflow_db: WorkflowDatabase for workflow data

        """
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
    # Export context
    # ------------------------------------------------------------------
    @staticmethod
    def _build_export_context(
        metadata: dict[str, Any] | None,
    ) -> tuple[Any, Any, Any, str]:
        """Build the engine settings + repositories scoped to the target database.

        Returns ``(engine_settings, adapter, graph_repository, database_name)``.
        The target database is the one captured in the task metadata at ENQUEUE
        time (cortex's ``current_database`` then), NOT the worker's live global.
        The worker is a separate process whose graph repository is bound to the
        database it booted / last hot-reloaded on, so reading ``current_database``
        here would export whatever the worker happens to be bound to rather than
        the database the user selected in the UI. Mirrors ``handle_import_ccx``,
        which likewise reads ``database_name`` from the task metadata. Both the
        graph repository and the source adapter are (re)built against this
        database so node/edge/template AND source reads stay in the same scope.
        """
        from chaoscypher_core.app_config import get_settings
        from chaoscypher_core.app_config.engine_factory import build_engine_settings
        from chaoscypher_core.database.adapter_factory import get_sqlite_adapter
        from chaoscypher_core.repo_factories import get_graph_repository

        engine_settings = build_engine_settings(get_settings())
        database_name = (metadata or {}).get("database_name") or engine_settings.current_database
        if database_name != engine_settings.current_database:
            engine_settings = engine_settings.model_copy(update={"current_database": database_name})
        adapter = get_sqlite_adapter(database_name)
        graph_repository = get_graph_repository(adapter.session, database_name)
        return engine_settings, adapter, graph_repository, database_name

    # ------------------------------------------------------------------
    # Preview rendering
    # ------------------------------------------------------------------
    @staticmethod
    def _render_preview_png(
        adapter: Any,
        *,
        database_name: str,
        title: str | None,
        source_ids: list[str] | None = None,
    ) -> bytes | None:
        """Render the graph-snapshot preview PNG bytes for an export.

        Mirrors the v2.0 exporter's preview path: build a ``GraphBreakdown``
        from the connected adapter and render it to PNG. Returns ``None``
        (preview omitted) if rendering fails for any reason so a preview
        failure never blocks the export.
        """
        import tempfile
        from pathlib import Path

        from chaoscypher_core.services.graph.snapshot.build_service import (
            BuildGraphSnapshotService,
        )
        from chaoscypher_core.services.graph.snapshot.renderer import SnapshotRenderer

        try:
            breakdown = BuildGraphSnapshotService.from_adapter(adapter).build(
                database_name=database_name,
                source_ids=source_ids,
                title=title,
            )
            with tempfile.TemporaryDirectory() as tmpdir:
                preview_path = Path(tmpdir) / "graph_preview.png"
                SnapshotRenderer().render_png(breakdown, preview_path)
                return preview_path.read_bytes()
        except Exception:
            logger.warning("ccx_export_preview_render_failed", exc_info=True)
            return None

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
        from chaoscypher_core.services.export import CcxExporter

        include_templates = data.get("include_templates", True)
        include_knowledge = data.get("include_knowledge", True)
        include_lenses = data.get("include_lenses", True)
        include_workflows = data.get("include_workflows", True)
        include_sources = data.get("include_sources", True)
        include_embeddings = data.get("include_embeddings", False)
        lens_id = data.get("lens_id")
        title = data.get("title")

        # Scope every read to the task's target database (see _build_export_context)
        # rather than the worker's ambient current_database.
        engine_settings, adapter, graph_repository, database_name = self._build_export_context(
            metadata
        )

        logger.info(
            "export_graph_operation_executing",
            database=database_name,
            lens_id=lens_id,
            include_templates=include_templates,
            include_knowledge=include_knowledge,
            include_lenses=include_lenses,
            include_workflows=include_workflows,
            include_sources=include_sources,
            include_embeddings=include_embeddings,
        )

        exporter = CcxExporter(
            graph_repository=graph_repository,
            settings=engine_settings,
            workflow_db=self.workflow_db,
            sources_repository=adapter,  # Implements SourceStorageProtocol
        )

        # Render the graph-snapshot preview PNG from the connected adapter and
        # supply it to the exporter (CCX 3.0 ``assets/graph_preview.png``).
        preview_png = self._render_preview_png(
            adapter,
            database_name=database_name,
            title=title,
        )

        ccx_bytes = exporter.export(
            include_templates=include_templates,
            include_knowledge=include_knowledge,
            include_lenses=include_lenses,
            include_workflows=include_workflows,
            include_sources=include_sources,
            include_embeddings=include_embeddings,
            lens_id=lens_id,
            title=title,
            preview_png=preview_png,
        )

        zip_content = ccx_bytes
        encoded_content = base64.b64encode(zip_content).decode("utf-8")

        filename = exporter.get_export_filename()

        event_bus.emit(
            "task_completed",
            action="Graph export complete",
            source="worker",
            details={"filename": filename, "size_bytes": len(zip_content)},
            database_name=database_name,
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
        from chaoscypher_core.services.export import CcxExporter

        source_ids = data.get("source_ids", [])
        include_templates = data.get("include_templates", True)
        include_embeddings = data.get("include_embeddings", False)
        title = data.get("title")

        # Scope every read to the task's target database (see _build_export_context)
        # rather than the worker's ambient current_database.
        engine_settings, adapter, graph_repository, database_name = self._build_export_context(
            metadata
        )

        logger.info(
            "export_by_sources_operation_executing",
            database=database_name,
            source_count=len(source_ids),
            include_templates=include_templates,
            include_embeddings=include_embeddings,
        )

        exporter = CcxExporter(
            graph_repository=graph_repository,
            settings=engine_settings,
            workflow_db=self.workflow_db,
            sources_repository=adapter,  # Implements SourceStorageProtocol
        )

        preview_png = self._render_preview_png(
            adapter,
            database_name=database_name,
            title=title,
            source_ids=source_ids,
        )

        ccx_bytes = exporter.export(
            source_ids=source_ids,
            include_templates=include_templates,
            include_sources=True,
            include_embeddings=include_embeddings,
            # Source-scoped export: lenses/workflows are not source-owned.
            include_lenses=False,
            include_workflows=False,
            title=title,
            preview_png=preview_png,
        )

        zip_content = ccx_bytes
        encoded_content = base64.b64encode(zip_content).decode("utf-8")

        filename = f"sources_export_{len(source_ids)}_{exporter.get_export_filename()}"

        event_bus.emit(
            "task_completed",
            action="Source export complete",
            source="worker",
            details={
                "filename": filename,
                "source_count": len(source_ids),
                "size_bytes": len(zip_content),
            },
            database_name=database_name,
        )

        return {
            "filename": filename,
            "content": encoded_content,
            "size_bytes": len(zip_content),
        }
