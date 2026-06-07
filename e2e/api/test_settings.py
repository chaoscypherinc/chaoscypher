# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for settings endpoints."""

import httpx


class TestSettings:
    """Test settings read and update."""

    def test_get_settings(self, client: httpx.Client) -> None:
        """Getting settings returns the current configuration."""
        resp = client.get("/api/v1/settings")
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)

    def test_get_logging_level(self, client: httpx.Client) -> None:
        """Getting logging level returns current level."""
        resp = client.get("/api/v1/settings/logging/level")
        assert resp.status_code == 200
        data = resp.json()
        assert "level" in data
        assert data["level"] in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

    def test_update_logging_level(self, client: httpx.Client) -> None:
        """Updating logging level changes it and returns old/new."""
        current = client.get("/api/v1/settings/logging/level").json()["level"]

        resp = client.post("/api/v1/settings/logging/level", json={"level": "WARNING"})
        assert resp.status_code == 200
        assert resp.json()["new_level"] == "WARNING"

        # Restore original
        client.post("/api/v1/settings/logging/level", json={"level": current})
