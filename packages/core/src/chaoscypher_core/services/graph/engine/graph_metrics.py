# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Graph Statistics.

Simple statistical calculations including degree distribution
and isolated nodes.

Extracted from graph_analytics.py for SRP compliance.
"""

from typing import Any

import structlog


logger = structlog.get_logger(__name__)


def calculate_node_degrees_simple(edges: list[Any]) -> dict[str, int]:
    """Calculate degree (connection count) for each node (simplified version).

    This consolidates the repeated pattern found throughout the codebase.

    Args:
        edges: List of edge objects with source_node_id and target_node_id

    Returns:
        Dictionary mapping node_id to degree

    Example:
        degrees = GraphAnalyticsService.calculate_node_degrees_simple(edges)
        most_connected_id = max(degrees, key=degrees.get)

    """
    degrees: dict[str, int] = {}
    for edge in edges:
        degrees[edge.source_node_id] = degrees.get(edge.source_node_id, 0) + 1
        degrees[edge.target_node_id] = degrees.get(edge.target_node_id, 0) + 1
    return degrees


def find_isolated_nodes_simple(nodes: list[Any], edges: list[Any]) -> list[dict[str, Any]]:
    """Find nodes with no connections (simplified version).

    Args:
        nodes: List of node objects
        edges: List of edge objects

    Returns:
        List of isolated nodes with metadata

    Example:
        isolated = GraphAnalyticsService.find_isolated_nodes_simple(nodes, edges)

    """
    connected_ids = set()
    for edge in edges:
        connected_ids.add(edge.source_node_id)
        connected_ids.add(edge.target_node_id)

    return [
        {"id": node.id, "label": node.label, "template_id": node.template_id}
        for node in nodes
        if node.id not in connected_ids
    ]
