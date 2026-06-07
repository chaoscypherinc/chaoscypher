# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for LLM management endpoints.

These endpoints monitor the background LLM workers. They should always
return 200 with structured data - 503 indicates the queue/worker is broken,
which IS a real failure we want tests to catch.
"""

import httpx


class TestLlmManagement:
    """Test LLM stats, tasks, and health endpoints."""

    def test_llm_health(self, client: httpx.Client) -> None:
        """LLM health endpoint returns status with health info.

        Endpoint moved /api/v1/llm/health → /api/v1/settings/llm/health
        when LLM verification was unified with the settings surface
        (2026-05-22 reshape). The old path now 404s cleanly via the
        canonical SPA fallback; this test follows the rename.
        """
        resp = client.get("/api/v1/settings/llm/health")
        assert resp.status_code == 200, (
            f"LLM health endpoint failed: {resp.status_code} {resp.text}"
        )
        data = resp.json()
        # Real-time shape: {provider, configured, verified,
        # last_verified_at, missing_models}.
        assert "verified" in data
        assert "provider" in data

    def test_llm_stats(self, client: httpx.Client) -> None:
        """LLM stats endpoint returns queue statistics."""
        resp = client.get("/api/v1/llm/stats")
        assert resp.status_code == 200, f"LLM stats endpoint failed: {resp.status_code} {resp.text}"
        data = resp.json()
        assert "data" in data

    def test_llm_tasks(self, client: httpx.Client) -> None:
        """LLM tasks endpoint returns active task list."""
        resp = client.get("/api/v1/llm/tasks")
        assert resp.status_code == 200, f"LLM tasks endpoint failed: {resp.status_code} {resp.text}"
        data = resp.json()
        assert "data" in data

    def test_get_nonexistent_task(self, client: httpx.Client) -> None:
        """Getting a nonexistent task returns 404."""
        resp = client.get("/api/v1/llm/tasks/nonexistent-task-id-12345")
        assert resp.status_code == 404
