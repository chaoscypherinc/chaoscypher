# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for graph grounding API (MCP integration endpoints)."""

import httpx


class TestGrounding:
    """Test read-only graph exploration endpoints used by MCP agents."""

    def test_list_nodes(self, client: httpx.Client) -> None:
        """Grounding list nodes returns the canonical paginated envelope."""
        resp = client.get("/api/v1/graph/grounding/nodes", params={"limit": 10})
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["data"], list)
        assert "pagination" in data

    def test_list_nodes_with_search(self, client: httpx.Client) -> None:
        """Grounding list nodes supports text search (returns envelope)."""
        resp = client.get(
            "/api/v1/graph/grounding/nodes",
            params={"q": "alice", "limit": 10},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["data"], list)

    def test_list_nodes_with_pagination(self, client: httpx.Client) -> None:
        """Grounding list nodes supports skip/limit pagination."""
        resp = client.get(
            "/api/v1/graph/grounding/nodes",
            params={"limit": 5, "skip": 0},
        )
        assert resp.status_code == 200

    def test_list_edges(self, client: httpx.Client) -> None:
        """Grounding list edges returns edges response."""
        resp = client.get("/api/v1/graph/grounding/edges", params={"limit": 10})
        assert resp.status_code == 200
        # EdgeListResponse has a specific shape
        data = resp.json()
        assert "edges" in data or "data" in data or isinstance(data, list)

    def test_get_node_not_found(self, client: httpx.Client) -> None:
        """Getting a nonexistent node via grounding returns 404."""
        resp = client.get("/api/v1/graph/grounding/nodes/nonexistent-node-xyz")
        assert resp.status_code == 404

    def test_get_neighbors_not_found(self, client: httpx.Client) -> None:
        """Getting neighbors for nonexistent node returns 404."""
        resp = client.get("/api/v1/graph/grounding/nodes/nonexistent-xyz/neighbors")
        assert resp.status_code == 404
