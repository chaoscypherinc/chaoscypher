# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for queue monitoring endpoints."""

import httpx


class TestQueue:
    """Test queue status and health."""

    def test_queue_health(self, client: httpx.Client) -> None:
        """Queue health endpoint reports connectivity."""
        resp = client.get("/api/v1/queue/health")
        assert resp.status_code == 200
        data = resp.json()
        # Response uses 'status' or 'healthy' key depending on version
        assert "status" in data or "healthy" in data

    def test_queue_stats(self, client: httpx.Client) -> None:
        """Queue stats returns queue information."""
        resp = client.get("/api/v1/queue/stats")
        assert resp.status_code == 200
        assert "queues" in resp.json()

    def test_list_tasks(self, client: httpx.Client) -> None:
        """Listing tasks returns recent task history."""
        resp = client.get("/api/v1/queue/tasks", params={"limit": 10})
        assert resp.status_code == 200

    def test_get_nonexistent_task(self, client: httpx.Client) -> None:
        """Getting a nonexistent task returns 404.

        QueueService.get_task raises NotFoundError unconditionally when
        the task hash is missing (queue/service.py:152-160) — 503 only
        happens via the unrelated _check_available() gate (queue
        service entirely down), not "task not found".
        """
        resp = client.get("/api/v1/queue/tasks/nonexistent-task-id-xyz")
        assert resp.status_code == 404

    def test_get_nonexistent_task_result(self, client: httpx.Client) -> None:
        """Getting a result for a nonexistent task returns 404."""
        resp = client.get("/api/v1/queue/tasks/nonexistent-task-xyz/result")
        assert resp.status_code == 404

    def test_cancel_nonexistent_task(self, client: httpx.Client) -> None:
        """Cancelling a nonexistent task returns 404.

        cancel_task looks the task up first and raises NotFoundError
        before it can ever reach the "cannot be cancelled" 400 branch
        (queue/service.py:172-189).
        """
        resp = client.delete("/api/v1/queue/tasks/nonexistent-task-xyz")
        assert resp.status_code == 404

    def test_retry_nonexistent_task(self, client: httpx.Client) -> None:
        """Retrying a nonexistent task returns 404.

        retry_task raises NotFoundError when queue_client.retry_task
        returns falsy for a missing id (queue/service.py:191-201); the
        400 branch is reserved for a task that exists but isn't failed.
        """
        resp = client.post("/api/v1/queue/tasks/nonexistent-task-xyz/retry")
        assert resp.status_code == 404
