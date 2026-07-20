# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared edge-pagination helpers for node commands."""

from typing import Any


def list_connected_edges(
    edge_service: Any,
    *,
    node_id: str,
    page_size: int,
    edge_filter: str,
) -> list[dict[str, Any]]:
    """Return every edge connected to a node for one direction.

    Walks every page of ``edge_service.list_edges`` so high-degree nodes are
    not silently truncated to the first page.

    Args:
        edge_service: The edge service exposing ``list_edges``.
        node_id: The node whose edges to fetch.
        page_size: Page size to request per call.
        edge_filter: Which endpoint to filter on — ``"source_node_id"`` for
            outgoing edges or ``"target_node_id"`` for incoming edges.

    """
    page = 1
    edges: list[dict[str, Any]] = []

    while True:
        kwargs: dict[str, Any] = {
            edge_filter: node_id,
            "page": page,
            "page_size": page_size,
        }
        result = edge_service.list_edges(**kwargs)
        edges.extend(result.get("data", []))

        pagination = result.get("pagination", {})
        total_pages = int(pagination.get("total_pages") or page)
        if not pagination.get("has_next", page < total_pages):
            break
        page += 1

    return edges
