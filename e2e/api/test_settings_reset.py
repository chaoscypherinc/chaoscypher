# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for settings reset endpoints.

Note: These tests are careful to avoid wiping real data during fresh phase.
They test the API contract with minimal disruption.
"""

import httpx


class TestSettingsReset:
    """Test settings reset endpoints (non-destructive)."""

    def test_reset_queue_stats(self, client: httpx.Client) -> None:
        """Queue stats reset returns statistics response."""
        resp = client.post("/api/v1/settings/reset/queue")
        assert resp.status_code == 200

    def test_reset_all_requires_confirmation(self, client: httpx.Client) -> None:
        """Nuclear reset without confirmation returns 400."""
        resp = client.post("/api/v1/settings/reset/all", json={"confirmation": "WRONG"})
        assert resp.status_code == 400

    def test_cleanup_orphans(self, client: httpx.Client) -> None:
        """Orphan cleanup endpoint enqueues an async cleanup task."""
        resp = client.post("/api/v1/settings/cleanup/orphans")
        assert resp.status_code == 202
        assert "task_id" in resp.json()

    def test_tls_status(self, client: httpx.Client) -> None:
        """TLS status endpoint returns current TLS state."""
        resp = client.get("/api/v1/settings/tls/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "enabled" in data
