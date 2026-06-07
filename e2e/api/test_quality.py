# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for quality scoring endpoints."""

import httpx


class TestQuality:
    """Test quality scoring and analysis endpoints."""

    def test_quality_summary(self, client: httpx.Client) -> None:
        """Quality summary endpoint returns aggregate metrics."""
        resp = client.get("/api/v1/quality/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_sources" in data
        assert "avg_total_score" in data

    def test_quality_domains(self, client: httpx.Client) -> None:
        """Quality domains endpoint returns per-domain comparison."""
        resp = client.get("/api/v1/quality/domains")
        assert resp.status_code == 200
        assert "domains" in resp.json()

    def test_quality_analyze_get(self, client: httpx.Client) -> None:
        """GET analyze returns paginated quality scores."""
        resp = client.get("/api/v1/quality/analyze")
        assert resp.status_code == 200
        data = resp.json()
        assert "sources" in data
        assert "pagination" in data

    def test_quality_analyze_post(self, client: httpx.Client) -> None:
        """POST analyze with empty filter returns all sources."""
        resp = client.post("/api/v1/quality/analyze", json={"min_entities": 0})
        assert resp.status_code == 200
        assert "sources" in resp.json()

    def test_quality_outdated(self, client: httpx.Client) -> None:
        """Outdated sources endpoint returns list."""
        resp = client.get("/api/v1/quality/outdated")
        assert resp.status_code == 200
        data = resp.json()
        assert "outdated_count" in data
        assert "sources" in data
