# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""ExportRepository: Handles knowledge graph export to .ccx format.

This repository provides data access for exporting knowledge graphs to the CCX
(Chaos Cypher eXchange) format with selective content export,
checksums, and comprehensive metadata.

Heavy lifting is delegated to focused sub-modules:
- ``data_extractor``   -- graph/source data extraction and classification
- ``metadata_manager`` -- serialization, checksums, statistics
- ``package_builder``  -- manifest creation, zip assembly, logging
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from io import BytesIO

    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
    from chaoscypher_core.ports.graph import GraphRepositoryProtocol
    from chaoscypher_core.settings import EngineSettings

import structlog

from chaoscypher_core.services.export.management.data_extractor import (
    collect_source_chunks,
    collect_source_citations,
    collect_source_tags,
    separate_nodes_and_edges,
)
from chaoscypher_core.services.export.management.metadata_manager import (
    build_source_dict,
    calculate_all_stats,
    serialize_and_checksum,
)
from chaoscypher_core.services.export.management.package_builder import (
    build_package_contents,
    create_manifest,
    create_zip_file,
    log_export_summary,
)


logger = structlog.get_logger(__name__)


class ExportRepository:
    """Repository for knowledge graph export operations."""

    def __init__(
        self,
        graph_repository: GraphRepositoryProtocol,
        settings: EngineSettings,
        workflow_db: Any = None,
        sources_repository: Any = None,
        adapter: SqliteAdapter | None = None,
    ) -> None:
        """Initialize export repository with required dependencies.

        Args:
            graph_repository: GraphRepository instance for data access
            settings: EngineSettings instance for export configuration
            workflow_db: Optional WorkflowDatabase instance for trigger export
            sources_repository: Optional SourcesProtocol instance for sources export
            adapter: Optional SqliteAdapter used to build the graph snapshot
                and render the preview PNG. When None, snapshot + PNG are skipped.

        """
        self.graph = graph_repository
        self.settings = settings
        self.workflow_db = workflow_db
        self.sources_repository = sources_repository
        self._adapter = adapter

    # ------------------------------------------------------------------
    # Snapshot helpers
    # ------------------------------------------------------------------

    def _build_snapshot_and_preview(
        self,
        database_name: str,
        source_ids: list[str] | None = None,
        title: str | None = None,
    ) -> tuple[Any, bytes | None]:
        """Build a GraphBreakdown and render a preview PNG.

        Returns ``(breakdown, preview_bytes)``.  When ``self._adapter``
        is None, returns ``(None, None)`` so callers can fall back to
        the manifest stub path.

        Args:
            database_name: Database to aggregate.
            source_ids: Optional source filter.
            title: Optional display title passed through to the model.

        Returns:
            Tuple of (GraphBreakdown | None, preview_bytes | None).

        """
        if self._adapter is None:
            return None, None

        from chaoscypher_core.services.graph.snapshot.build_service import (
            BuildGraphSnapshotService,
        )
        from chaoscypher_core.services.graph.snapshot.renderer import SnapshotRenderer

        breakdown = BuildGraphSnapshotService.from_adapter(self._adapter).build(
            database_name=database_name,
            source_ids=source_ids,
            title=title,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            preview_path = Path(tmpdir) / "graph_preview.png"
            SnapshotRenderer().render_png(breakdown, preview_path)
            preview_bytes: bytes | None = preview_path.read_bytes()

        return breakdown, preview_bytes

    # ------------------------------------------------------------------
    # Safe data access helpers
    # ------------------------------------------------------------------

    def _safe_get_triggers(self) -> list[dict[str, Any]]:
        """Get triggers with error handling."""
        try:
            triggers: list[dict[str, Any]] = self.workflow_db.get_triggers()
            return triggers
        except Exception:
            logger.warning("Failed to get triggers", exc_info=True)
            return []

    def _safe_get_sources(self) -> list[dict[str, Any]]:
        """Get all sources with paginated fetching."""
        try:
            all_sources: list[dict[str, Any]] = []
            page = 1
            page_size = self.settings.pagination.export_page_size
            while True:
                sources, total = self.sources_repository.list_sources(
                    page=page,
                    page_size=page_size,
                    source_type=None,
                    status=None,
                    search=None,
                    tag_id=None,
                )
                all_sources.extend(sources)
                if len(all_sources) >= total or len(sources) < page_size:
                    break
                page += 1
            return all_sources
        except Exception:
            logger.warning("Failed to get sources", exc_info=True)
            return []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def export_graph(
        self,
        include_templates: bool = True,
        include_knowledge: bool = True,
        include_lenses: bool = True,
        include_workflows: bool = True,
        include_sources: bool = True,
        include_embeddings: bool = False,
        lens_id: str | None = None,
        title: str | None = None,
    ) -> BytesIO:
        """Export knowledge graph to .ccx format with selective content.

        Args:
            include_templates: Include user templates in export
            include_knowledge: Include knowledge nodes and edges
            include_lenses: Include lens nodes and edges
            include_workflows: Include workflow nodes, steps, and triggers
            include_sources: Include document sources and metadata
            include_embeddings: Include embedding vectors in export
            lens_id: Optional - Export only a specific lens by ID
            title: Optional display title for the graph snapshot preview

        Returns:
            BytesIO buffer containing the .ccx zip file

        Raises:
            Exception: If export fails

        """
        try:
            # Export all graph data
            graph_data = self.graph.export_graph()
            settings = self.settings

            # Step 1: Build graph snapshot + render preview PNG (when adapter available)
            breakdown, preview_bytes = self._build_snapshot_and_preview(
                database_name=settings.current_database,
                title=title,
            )

            # Step 2: Separate data by type based on user selection
            separated_data = separate_nodes_and_edges(
                graph_data=graph_data,
                include_templates=include_templates,
                include_knowledge=include_knowledge,
                include_lenses=include_lenses,
                include_workflows=include_workflows,
                include_sources=include_sources,
                include_embeddings=include_embeddings,
                lens_id=lens_id,
                safe_get_triggers=self._safe_get_triggers,
                safe_get_sources=self._safe_get_sources,
                sources_repository=self.sources_repository,
                workflow_db=self.workflow_db,
            )

            # Step 3: Serialize and calculate checksums for all files
            file_data = serialize_and_checksum(
                separated_data, include_embeddings=include_embeddings
            )

            # Step 4: Calculate statistics for each content type
            stats = calculate_all_stats(
                separated_data=separated_data,
                settings=settings,
                include_templates=include_templates,
                include_knowledge=include_knowledge,
                include_lenses=include_lenses,
                include_workflows=include_workflows,
                include_sources=include_sources,
                include_embeddings=include_embeddings,
            )

            # Step 5: Build package contents list from actual files created
            package_type = build_package_contents(file_data=file_data)
            if preview_bytes is not None:
                package_type = sorted([*package_type, "graph_preview"])

            # Step 6: Create manifest (with snapshot breakdown + preview checksum)
            manifest = create_manifest(
                package_type=package_type,
                file_data=file_data,
                stats=stats,
                settings=settings,
                graph_breakdown=breakdown,
                preview_bytes=preview_bytes,
            )

            # Step 7: Create zip file (includes preview PNG when present)
            zip_buffer = create_zip_file(
                file_data=file_data, manifest=manifest, preview_bytes=preview_bytes
            )

            # Log export summary
            log_export_summary(
                package_type=package_type, separated_data=separated_data, stats=stats
            )

            return zip_buffer

        except Exception as e:
            logger.exception("export_failed", error_type=type(e).__name__, error_message=str(e))
            raise

    def get_export_filename(self) -> str:
        """Generate a timestamped export filename.

        Returns:
            Filename string in format: knowledge_export_YYYYMMDD_HHMMSS.ccx

        """
        return f"knowledge_export_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.ccx"

    def export_by_sources(
        self,
        source_ids: list[str],
        include_templates: bool = True,
        include_embeddings: bool = True,
        title: str | None = None,
    ) -> BytesIO:
        """Export only data related to specified sources.

        Creates a .ccx package containing only entities, edges, templates,
        and source data that are linked to the specified sources.

        Args:
            source_ids: List of source UUIDs to include in export
            include_templates: Include templates linked to these sources
            include_embeddings: Include embeddings in exported chunks
            title: Optional display title for the graph snapshot preview

        Returns:
            BytesIO buffer containing the .ccx zip file

        Raises:
            ValueError: If sources_repository was not injected (programmer error).
            Exception: If export fails

        """
        if not self.sources_repository:
            msg = "Sources repository required for source-filtered export"
            raise ValueError(  # nosemgrep: cc-045-bare-stdlib-raise-in-core - programmer error: sources_repository must be injected before calling export_by_sources
                msg
            )

        try:
            settings = self.settings

            # Step 1: Build graph snapshot + render preview PNG (scoped to source_ids)
            breakdown, preview_bytes = self._build_snapshot_and_preview(
                database_name=settings.current_database,
                source_ids=source_ids,
                title=title,
            )

            # Step 2: Get entity URIs from the specified sources
            entity_uris = self.sources_repository.get_entity_uris_for_sources(source_ids)

            logger.info(
                "source_filtered_export_started",
                source_count=len(source_ids),
                entity_count=len(entity_uris),
            )

            # Step 2: Get all graph data and filter
            graph_data = self.graph.export_graph()

            # Step 3: Filter knowledge nodes to only those matching entity URIs
            # Entity URIs are in format "ke:node/node_xxx" so we need to extract node IDs
            node_id_set = set()
            for uri in entity_uris:
                # Extract node ID from URI
                node_id = uri.split("/")[-1] if "/" in uri else uri
                node_id_set.add(node_id)

            filtered_knowledge_nodes = [
                node
                for node in graph_data["nodes"]
                if node["id"] in node_id_set
                and not node.get("template_id", "").startswith("system_")
            ]

            # Step 4: Filter edges - include only if BOTH endpoints are in the entity set
            filtered_knowledge_edges = [
                edge
                for edge in graph_data["edges"]
                if edge.get("source_node_id") in node_id_set
                and edge.get("target_node_id") in node_id_set
            ]

            # Step 5: Get templates owned by these sources
            filtered_templates = []
            if include_templates:
                # Templates now have source_id directly - filter by source ownership
                # Also include templates used by the filtered nodes
                template_ids_from_nodes = set()
                for node in filtered_knowledge_nodes:
                    template_id = node.get("template_id")
                    if template_id:
                        template_ids_from_nodes.add(template_id)

                # Get template data from graph (user templates owned by these sources or used by nodes)
                source_id_set = set(source_ids)
                filtered_templates = [
                    template
                    for template in graph_data["templates"]
                    if (
                        template.get("source_id") in source_id_set
                        or template["id"] in template_ids_from_nodes
                    )
                    and not template.get("is_system", False)
                ]

            # Step 6: Collect sources with related data
            filtered_sources = []
            for source_id in source_ids:
                source = self.sources_repository.get_source(
                    source_id,
                    database_name=settings.current_database,
                )
                if not source:
                    continue

                source_dict = build_source_dict(source)
                source_dict["chunks"] = collect_source_chunks(
                    self.sources_repository, source_id, include_embeddings=include_embeddings
                )
                source_dict["citations"] = collect_source_citations(
                    self.sources_repository, source_id
                )
                source_dict["tags"] = collect_source_tags(self.sources_repository, source_id)
                filtered_sources.append(source_dict)

            # Step 7: Build separated data structure
            separated_data = {
                "templates": filtered_templates,
                "knowledge_nodes": filtered_knowledge_nodes,
                "knowledge_edges": filtered_knowledge_edges,
                "lens_nodes": [],  # Not included in source-filtered export
                "lens_edges": [],
                "workflow_nodes": [],  # Not included in source-filtered export
                "workflow_edges": [],
                "triggers": [],
                "sources": filtered_sources,
            }

            # Step 8: Serialize and create package (reuse existing methods)
            file_data = serialize_and_checksum(
                separated_data, include_embeddings=include_embeddings
            )

            stats = calculate_all_stats(
                separated_data=separated_data,
                settings=settings,
                include_templates=include_templates,
                include_knowledge=True,
                include_lenses=False,
                include_workflows=False,
                include_sources=True,
                include_embeddings=include_embeddings,
            )

            package_type = build_package_contents(file_data=file_data)
            if preview_bytes is not None:
                package_type = sorted([*package_type, "graph_preview"])

            manifest = create_manifest(
                package_type=package_type,
                file_data=file_data,
                stats=stats,
                settings=settings,
                graph_breakdown=breakdown,
                preview_bytes=preview_bytes,
            )

            zip_buffer = create_zip_file(
                file_data=file_data, manifest=manifest, preview_bytes=preview_bytes
            )

            logger.info(
                "source_filtered_export_complete",
                source_count=len(filtered_sources),
                node_count=len(filtered_knowledge_nodes),
                edge_count=len(filtered_knowledge_edges),
                template_count=len(filtered_templates),
            )

            return zip_buffer

        except Exception as e:
            logger.exception(
                "source_filtered_export_failed",
                source_ids=source_ids,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            raise
