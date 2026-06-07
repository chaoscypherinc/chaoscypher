# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workflow Loader - Imports workflows from CCX packages.

Handles importing workflow data from workflows.jsonld files,
including nodes, edges, and triggers.

Example:
    from chaoscypher_core.services.package.importer.loaders import WorkflowLoader

    loader = WorkflowLoader(graph_repository, workflow_db)
    loader.load(workflows_data, mapper, stats, "default")
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.models import EdgeCreate, NodeCreate
from chaoscypher_core.services.package.importer.loaders.base import PackageLoaderBase


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository
    from chaoscypher_core.services.package.importer.models import IdMapper, ImportStats


logger = structlog.get_logger(__name__)


class WorkflowLoader(PackageLoaderBase):
    """Loads workflow nodes, edges, and triggers from CCX packages.

    Handles importing workflow data from workflows.jsonld files.
    Workflows are stored in the workflows_graph with optional triggers.

    Attributes:
        graph_repository: Graph repository for workflow node/edge operations.
        workflow_db: Optional workflow database for trigger operations.
    """

    def __init__(
        self,
        graph_repository: GraphRepository,
        workflow_db: Any | None = None,
    ) -> None:
        """Initialize workflow loader.

        Args:
            graph_repository: Graph repository for workflow operations.
            workflow_db: Optional workflow database for triggers.
        """
        self.graph_repository = graph_repository
        self.workflow_db = workflow_db

    def load(
        self,
        data: dict[str, Any] | list[dict[str, Any]],
        mapper: IdMapper,
        stats: ImportStats,
        database_name: str,
    ) -> None:
        """Load workflows from parsed workflows.jsonld data.

        Args:
            data: Parsed workflows.jsonld data with nodes, edges, triggers.
            mapper: IdMapper for tracking ID transformations.
            stats: ImportStats for recording statistics.
            database_name: Target database name.
        """
        if not isinstance(data, dict):
            stats.errors.append("Invalid workflows.jsonld format: expected dict")
            return

        nodes_data = data.get("nodes", [])
        edges_data = data.get("edges", [])
        triggers_data = data.get("triggers", [])

        logger.info(
            "loading_workflows",
            node_count=len(nodes_data),
            edge_count=len(edges_data),
            trigger_count=len(triggers_data),
        )

        # Load workflow nodes
        self._load_workflow_nodes(nodes_data, mapper, stats)

        # Load workflow edges
        self._load_workflow_edges(edges_data, mapper, stats)

        # Load triggers if workflow_db is available
        if triggers_data and self.workflow_db:
            self._load_triggers(triggers_data, mapper, stats, database_name)
        elif triggers_data:
            stats.warnings.append(f"Skipping {len(triggers_data)} triggers (no workflow_db)")

        logger.info(
            "workflows_loaded",
            nodes_imported=stats.workflows_imported,
            edges_imported=stats.workflow_edges_imported,
            triggers_imported=stats.triggers_imported,
        )

    def _load_workflow_nodes(
        self,
        nodes_data: list[dict[str, Any]],
        mapper: IdMapper,
        stats: ImportStats,
    ) -> None:
        """Load workflow nodes from parsed data."""
        for node_data in nodes_data:
            original_id = node_data.get("id")
            if not original_id:
                stats.warnings.append("Skipping workflow node without id")
                continue

            try:
                # Get template ID - support both direct template_id and template_name lookup
                template_id = node_data.get("template_id")
                if not template_id:
                    template_name = node_data.get("template_name")
                    if template_name:
                        template_id = mapper.get_template_id(template_name)

                # Skip if no valid template_id
                if not template_id or not isinstance(template_id, str):
                    stats.warnings.append(
                        f"Skipping workflow node without valid template_id: {original_id}"
                    )
                    continue

                # Use 'label' field (export format) with fallback to 'name' for compatibility
                label = node_data.get("label") or node_data.get("name", "Unnamed Workflow")

                # Parse position if present (NodePosition from export)
                position_data = node_data.get("position")
                position = None
                if position_data and isinstance(position_data, dict):
                    from chaoscypher_core.models import NodePosition

                    position = NodePosition(
                        x=position_data.get("x", 0),
                        y=position_data.get("y", 0),
                    )

                node_create = NodeCreate(
                    label=label,
                    template_id=template_id,
                    properties=node_data.get("properties", {}),
                    position=position,
                    embedding=node_data.get("embedding"),
                )
                created = self.graph_repository.create_node(node_create)
                mapper.map_node(original_id, created.id)
                stats.workflows_imported += 1
            except Exception as e:
                stats.warnings.append(f"Failed to create workflow node: {e}")

    def _load_workflow_edges(
        self,
        edges_data: list[dict[str, Any]],
        mapper: IdMapper,
        stats: ImportStats,
    ) -> None:
        """Load workflow edges from parsed data."""
        for edge_data in edges_data:
            original_source = edge_data.get("source_node_id")
            original_target = edge_data.get("target_node_id")

            if not original_source or not original_target:
                stats.warnings.append("Skipping workflow edge without source or target")
                continue

            new_source = mapper.get_node_id(original_source)
            new_target = mapper.get_node_id(original_target)

            if not new_source or not new_target:
                stats.warnings.append("Workflow edge references unmapped nodes")
                continue

            try:
                # Get template ID - support both direct template_id and template_name lookup
                template_id = edge_data.get("template_id")
                if not template_id:
                    template_name = edge_data.get("template_name")
                    if template_name:
                        template_id = mapper.get_template_id(template_name)

                # Skip if no valid template_id
                if not template_id or not isinstance(template_id, str):
                    stats.warnings.append("Skipping workflow edge without valid template_id")
                    continue

                # Use 'label' field (export format) with fallback to 'name' for compatibility
                label = edge_data.get("label") or edge_data.get("name", "")
                edge_create = EdgeCreate(
                    source_node_id=new_source,
                    target_node_id=new_target,
                    template_id=template_id,
                    label=label,
                    properties=edge_data.get("properties", {}),
                )
                self.graph_repository.create_edge(edge_create)
                stats.workflow_edges_imported += 1
            except Exception as e:
                stats.warnings.append(f"Failed to create workflow edge: {e}")

    def _load_triggers(
        self,
        triggers_data: list[dict[str, Any]],
        mapper: IdMapper,
        stats: ImportStats,
        database_name: str,
    ) -> None:
        """Load triggers from parsed data.

        Triggers are stored in the workflow database.
        """
        for trigger_data in triggers_data:
            try:
                # Remap workflow_id if present
                workflow_id = trigger_data.get("workflow_id")
                if workflow_id:
                    new_workflow_id = mapper.get_node_id(workflow_id)
                    if new_workflow_id:
                        trigger_data["workflow_id"] = new_workflow_id

                # Create trigger using workflow_db
                # Note: Actual trigger creation depends on workflow_db interface
                if self.workflow_db is not None and hasattr(self.workflow_db, "create_trigger"):
                    self.workflow_db.create_trigger(trigger_data)
                    stats.triggers_imported += 1
                else:
                    stats.warnings.append("workflow_db missing or has no create_trigger method")
            except Exception as e:
                stats.warnings.append(f"Failed to create trigger: {e}")


__all__ = ["WorkflowLoader"]
