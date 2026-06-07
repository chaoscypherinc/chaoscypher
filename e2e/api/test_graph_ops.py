# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for graph operations and counts endpoints."""

import httpx


class TestGraphOps:
    """Test graph source groups and cleanup."""

    def test_graph_cleanup(self, client: httpx.Client) -> None:
        """Graph cleanup endpoint enqueues an async cleanup task."""
        resp = client.post("/api/v1/graph/cleanup")
        # Cleanup now goes through the queue: returns 202 + task_id rather
        # than the previous 200 + synchronous removal counts.
        assert resp.status_code == 202
        data = resp.json()
        assert "task_id" in data


class TestCounts:
    """Test resource counts endpoint."""

    def test_get_counts(self, client: httpx.Client) -> None:
        """Counts endpoint returns all resource counts."""
        resp = client.get("/api/v1/counts")
        assert resp.status_code == 200
        data = resp.json()
        assert "knowledge_nodes" in data
        assert "links" in data
        assert "templates" in data
        assert "workflows" in data
        assert "sources" in data
