# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Graph Service.

Business logic for graph operations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.adapters.sqlite.repos import remove_corrupt_nodes
from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.models import SourceStatus


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository
    from chaoscypher_core.app_config import Settings

logger = structlog.get_logger(__name__)

# Safe canvas caps — requests for a cap above either threshold must scope
# the query with an explicit ``source_ids`` filter, otherwise the load +
# JSON serialisation can saturate the event loop and OOM the renderer.
# Values match the lowered pre-launch defaults in
# ``PaginationSettings.canvas_max_{nodes,edges}`` (see settings.py).
CANVAS_SAFE_NODE_CAP = 5_000
CANVAS_SAFE_EDGE_CAP = 15_000


class GraphService:
    """Service for graph operations."""

    def __init__(
        self,
        graph_repository: GraphRepository,
        adapter: SqliteAdapter | None = None,
        database_name: str = "",
        settings: Settings | None = None,
    ):
        """Initialize graph service.

        Args:
            graph_repository: GraphRepository instance
            adapter: Optional SqliteAdapter for source/citation queries
            database_name: Current database name
            settings: Application settings (used for pagination limits)

        """
        self.graph_repository = graph_repository
        self.adapter = adapter
        self.database_name = database_name
        self.settings = settings

    def get_canvas_data(self, source_ids: list[str] | None = None) -> dict[str, Any]:
        """Assemble the minimal graph payload for the canvas renderer.

        Counts are capped by ``settings.pagination.canvas_max_{nodes,edges}``
        so a very large graph can't OOM the browser. Nodes and edges come
        back in ``minimal=True`` projection; templates are full.

        Args:
            source_ids: Optional list of source IDs to scope the query to.

        Returns:
            Dict with ``truncated`` flag plus ``nodes`` / ``edges`` /
            ``templates`` arrays plus counts.
        """
        if self.settings is None:
            msg = "GraphService.get_canvas_data requires settings to be set"
            raise RuntimeError(msg)

        repo = self.graph_repository
        max_nodes = self.settings.pagination.canvas_max_nodes
        max_edges = self.settings.pagination.canvas_max_edges

        # Threshold-gate: a cap above the safe defaults can serialise hundreds
        # of MB of JSON on a single request. Require an explicit ``source_ids``
        # filter so the operator narrows the blast radius. Maps to HTTP 400
        # via the ValidationError → VALIDATION_ERROR registry entry.
        if not source_ids and (
            max_nodes > CANVAS_SAFE_NODE_CAP or max_edges > CANVAS_SAFE_EDGE_CAP
        ):
            msg = (
                f"Requesting more than {CANVAS_SAFE_NODE_CAP:,} nodes "
                f"or {CANVAS_SAFE_EDGE_CAP:,} edges from the graph canvas "
                "requires a `source_ids` filter — narrow the query or "
                "use the standard cap."
            )
            raise ValidationError(
                msg,
                field="source_ids",
                details={
                    "canvas_max_nodes": max_nodes,
                    "canvas_max_edges": max_edges,
                    "safe_node_cap": CANVAS_SAFE_NODE_CAP,
                    "safe_edge_cap": CANVAS_SAFE_EDGE_CAP,
                },
            )

        node_count = min(repo.count_nodes(), max_nodes)
        edge_count = min(repo.count_edges(), max_edges)

        nodes = repo.list_nodes(
            source_ids=source_ids,
            skip=0,
            limit=max(node_count, 1),
            minimal=True,
        )
        edges = repo.list_edges(
            source_ids=source_ids,
            skip=0,
            limit=max(edge_count, 1),
            minimal=True,
        )
        templates = repo.list_templates()

        truncated = node_count >= max_nodes or edge_count >= max_edges

        return {
            "truncated": truncated,
            "nodes": [
                {
                    "id": n.id,
                    "template_id": n.template_id,
                    "label": n.label,
                    "position": {"x": n.position.x, "y": n.position.y} if n.position else None,
                    "source_id": n.source_id,
                }
                for n in nodes
            ],
            "edges": [
                {
                    "id": e.id,
                    "source_node_id": e.source_node_id,
                    "target_node_id": e.target_node_id,
                    "template_id": e.template_id,
                    "label": e.label,
                }
                for e in edges
            ],
            "templates": [
                {
                    "id": t.id,
                    "name": t.name,
                    "template_type": t.template_type,
                    "icon": t.icon,
                    "color": t.color,
                    "description": t.description,
                }
                for t in templates
            ],
            "total_nodes": len(nodes),
            "total_edges": len(edges),
        }

    async def cleanup_corrupt_nodes(self) -> dict[str, int]:
        """Remove corrupt nodes from the graph.

        Corrupt nodes are those missing required fields (template_id or label).
        With SQLite storage, corrupt nodes should not exist due to schema constraints,
        but this operation is provided for safety.

        Returns:
            Dict with counts: {"nodes_removed": int, "edges_removed": int}

        """
        return remove_corrupt_nodes(self.graph_repository)

    async def get_source_groups(self) -> list[dict[str, Any]]:
        """Get source groups for graph visualization.

        Returns image-type sources that have been committed and have
        extracted entities in the graph, grouped for visual display.

        Note: list_sources() returns tuple[list[dict], int] (results, total).
        The dict key for status is "status" (Python field name), not
        "processing_status" (DB column name).

        Returns:
            List of source group dicts with source metadata and entity_node_ids.

        """
        if not self.adapter:
            return []

        # list_sources returns (list[dict], total_count) tuple
        page_size = (
            self.settings.pagination.graph_list_page_size if self.settings is not None else 1000
        )
        sources, _total = self.adapter.list_sources(
            status=SourceStatus.COMMITTED,
            page_size=page_size,
        )

        if not sources:
            return []

        source_ids = [s["id"] for s in sources]

        grouped = self.adapter.get_entity_uris_grouped_by_source(
            database_name=self.database_name,
            source_ids=source_ids,
        )

        groups = []
        for source in sources:
            sid = source["id"]
            entity_uris = grouped.get(sid, [])
            if not entity_uris:
                continue
            groups.append(
                {
                    "source_id": sid,
                    "title": source.get("title") or source.get("filename", "Unknown"),
                    "source_type": source.get("source_type", ""),
                    "filename": source.get("filename", ""),
                    "extraction_domain": source.get("extraction_domain"),
                    "entity_count": len(entity_uris),
                    "entity_node_ids": entity_uris,
                }
            )

        logger.info(
            "source_groups_loaded",
            total_committed_sources=len(sources),
            groups_with_entities=len(groups),
        )

        return groups
