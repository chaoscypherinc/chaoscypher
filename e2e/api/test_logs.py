# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for logs and service status endpoints."""

import httpx


class TestLogs:
    """Test logs and service status endpoints."""

    def test_get_all_logs(self, client: httpx.Client) -> None:
        """Getting all logs returns interleaved service logs."""
        resp = client.get("/api/v1/logs", params={"lines": 100})
        assert resp.status_code == 200
        data = resp.json()
        assert "lines" in data
        assert "total_lines" in data

    def test_service_status(self, client: httpx.Client) -> None:
        """Service status endpoint returns service info."""
        resp = client.get("/api/v1/logs/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "available" in data
        assert "services" in data

    def test_get_cortex_logs(self, client: httpx.Client) -> None:
        """Getting logs for cortex service returns its lines."""
        resp = client.get("/api/v1/logs/cortex", params={"lines": 50})
        assert resp.status_code == 200
        data = resp.json()
        assert "lines" in data


class TestDiagnostics:
    """Test diagnostics bundle export."""

    def test_export_diagnostics(self, client: httpx.Client) -> None:
        """Diagnostics export returns a binary ZIP file."""
        resp = client.get("/api/v1/diagnostics/export")
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith("application/zip")
        # Verify it's actually a ZIP
        assert resp.content.startswith(b"PK")
