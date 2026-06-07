# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Graph Cleanup Utilities.

Provides utilities for detecting and removing corrupt nodes
from the SQLite-backed knowledge graph.
"""

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_repository import GraphRepository

logger = structlog.get_logger(__name__)

_NODE_SCAN_PAGE_SIZE = 500


def _iter_all_nodes(repository: Any) -> Iterator[Any]:
    """Yield every node in the repository, paging through ``list_nodes``."""
    skip = 0

    while True:
        nodes = repository.list_nodes(
            skip=skip,
            limit=_NODE_SCAN_PAGE_SIZE,
            include_disabled_sources=True,
            include_embedding=False,
        )
        if not nodes:
            break

        yield from nodes

        if len(nodes) < _NODE_SCAN_PAGE_SIZE:
            break
        skip += _NODE_SCAN_PAGE_SIZE


def remove_corrupt_nodes(repository: GraphRepository) -> dict[str, int]:
    """Remove nodes that are missing required fields.

    With SQLite storage, corrupt nodes (missing required fields) should not
    exist since the database schema enforces NOT NULL constraints. This function
    is kept for backwards compatibility but will typically find nothing to remove.

    Corrupt nodes would be those that:
    - Have an id but are missing template_id or label

    Args:
        repository: GraphRepository instance

    Returns:
        Dict with counts: {"nodes_removed": int, "edges_removed": int}

    Example:
        >>> from chaoscypher_core.adapters.sqlite.repos import GraphRepository
        >>> from chaoscypher_core.adapters.sqlite.repos.graph.cleanup import remove_corrupt_nodes
        >>>
        >>> repo = GraphRepository(session, database_name)
        >>> result = remove_corrupt_nodes(repo)
        >>> print(f"Removed {result['nodes_removed']} corrupt nodes")

    Note:
        With SQLite storage, this function should always return zeros
        since the schema prevents corrupt nodes from being created.

    """
    nodes_removed = 0
    edges_removed = 0

    # With SQLite, corrupt nodes can't exist due to schema constraints
    # (template_id and label are required fields)
    # But we'll do a safety check anyway
    logger.info("corrupt_node_scan_started")

    corrupt_node_ids = []

    for node in _iter_all_nodes(repository):
        # Check for missing required fields
        # In SQLite these can't be None due to schema, but check anyway
        if not node.template_id or not node.label:
            logger.warning(
                "corrupt_node_found",
                node_id=node.id,
                has_template_id=bool(node.template_id),
                has_label=bool(node.label),
            )
            corrupt_node_ids.append(node.id)

    if corrupt_node_ids:
        logger.info("removing_corrupt_nodes", count=len(corrupt_node_ids))

        for node_id in corrupt_node_ids:
            was_deleted = repository.delete_node(node_id)
            if was_deleted:
                nodes_removed += 1

        logger.info(
            "corrupt_nodes_removed",
            nodes_removed=nodes_removed,
            edges_removed=edges_removed,
        )
    else:
        logger.info("no_corrupt_nodes_found")

    return {
        "nodes_removed": nodes_removed,
        "edges_removed": edges_removed,
    }
