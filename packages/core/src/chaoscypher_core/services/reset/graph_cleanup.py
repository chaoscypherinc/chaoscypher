# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Graph Cleanup Service.

Handles cleanup of orphaned graph items. With proper FK constraints on edges,
this cleanup is primarily for legacy data that may have become orphaned before
constraints were added.

Orphan definitions:
- Orphaned edge: Edge where source_node_id or target_node_id doesn't exist
- Orphaned node: Node where source_id points to a non-existent source
- Orphaned template: Template where source_id points to a non-existent source

NOTE: Nodes/edges with source_id=NULL are NOT orphans - they are intentionally
unlinked (created via chat, lenses, workflows, manual API, etc.)
"""

from typing import Any

import structlog

from chaoscypher_core.database.adapter_factory import get_sqlite_adapter
from chaoscypher_core.repo_factories import get_graph_repository


logger = structlog.get_logger(__name__)


class GraphCleanupService:
    """Cleanup service for orphaned graph items.

    Responsibility: Find and remove graph items that have invalid references
    (edges pointing to non-existent nodes, or items with source_id pointing
    to non-existent sources).

    NOTE: Items with source_id=NULL are NOT orphans — they were intentionally
    created without source association (chat, lenses, workflows, manual API).
    """

    def __init__(self, database_name: str):
        """Initialize graph cleanup service.

        Args:
            database_name: Name of the database

        """
        self.database_name = database_name

    def cleanup_orphaned_items(self) -> dict[str, Any]:
        """Remove orphaned items from the graph.

        Identifies and removes:
        1. Edges where source_node_id or target_node_id reference non-existent nodes
        2. Nodes where source_id references a non-existent source
        3. Non-system templates where source_id references a non-existent source

        Items with source_id=NULL are NOT considered orphans.

        Returns:
            Dictionary with cleanup statistics

        Raises:
            RuntimeError: If the SQLite adapter session is None inside the
                transaction context — a programmer-error guard for a broken
                adapter implementation (should never fire in production).

        """
        logger.info("orphan_cleanup_started", database_name=self.database_name)

        stats: dict[str, Any] = {
            "status": "success",
            "edges_scanned": 0,
            "edges_removed": 0,
            "nodes_scanned": 0,
            "nodes_removed": 0,
            "templates_scanned": 0,
            "templates_removed": 0,
        }

        adapter = get_sqlite_adapter(database_name=self.database_name)
        try:
            with adapter.transaction():
                if adapter.session is None:
                    msg = "Adapter session is None inside transaction()"
                    raise RuntimeError(  # nosemgrep: cc-045-bare-stdlib-raise-in-core - programmer error: adapter invariant violation, not a user-visible failure
                        msg
                    )
                repo = get_graph_repository(adapter.session, self.database_name)

                # 1. Orphaned edges (source_node_id or target_node_id missing)
                orphaned_edge_ids = set(
                    repo.find_orphaned_edges_by_source_node(database_name=self.database_name)
                )
                orphaned_edge_ids |= set(
                    repo.find_orphaned_edges_by_target_node(database_name=self.database_name)
                )

                if orphaned_edge_ids:
                    stats["edges_removed"] = repo.delete_edges_batch(
                        edge_ids=list(orphaned_edge_ids)
                    )
                    logger.info("orphaned_edges_removed", count=stats["edges_removed"])

                total_edges = repo.count_edges()
                stats["edges_scanned"] = total_edges + stats["edges_removed"]

                # 2. Orphaned nodes (source_id references missing source)
                orphaned_node_ids = repo.find_orphaned_nodes_by_source(
                    database_name=self.database_name
                )
                if orphaned_node_ids:
                    stats["nodes_removed"] = repo.delete_nodes_batch(node_ids=orphaned_node_ids)
                    logger.info("orphaned_nodes_removed", count=stats["nodes_removed"])

                total_nodes = repo.count_nodes()
                stats["nodes_scanned"] = total_nodes + stats["nodes_removed"]

                # 3. Orphaned templates (non-system, source_id references missing source)
                orphaned_template_ids = repo.find_orphaned_templates_by_source(
                    database_name=self.database_name
                )
                if orphaned_template_ids:
                    stats["templates_removed"] = repo.delete_templates_batch(
                        template_ids=orphaned_template_ids
                    )
                    logger.info(
                        "orphaned_templates_removed",
                        count=stats["templates_removed"],
                    )

                total_templates = repo.count_templates(database_name=self.database_name)
                stats["templates_scanned"] = total_templates + stats["templates_removed"]
        finally:
            adapter.disconnect()

        logger.info("orphan_cleanup_complete", stats=stats)
        return stats
