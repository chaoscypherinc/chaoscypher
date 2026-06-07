# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the edition API endpoint."""

from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_cortex.features.edition.api import router
from chaoscypher_cortex.features.edition.models import COMMUNITY_FEATURES


class TestEditionEndpoint:
    """Test GET /edition."""

    def test_community_edition_response(self) -> None:
        """Returns community edition when no enterprise package installed."""
        app = FastAPI()
        app.dependency_overrides[get_settings] = lambda: Settings(dev_mode=True)
        app.include_router(router, prefix="/edition")
        client = TestClient(app)
        with patch(
            "chaoscypher_cortex.features.edition.api.entry_points",
            return_value=[],
        ):
            response = client.get("/edition")

        assert response.status_code == 200
        data = response.json()
        assert data["edition"] == "community"
        assert data["license"] is None
        assert set(data["features"]) == set(COMMUNITY_FEATURES)

    def test_enterprise_edition_response(self) -> None:
        """Returns enterprise edition when enterprise package is installed."""
        mock_ep = MagicMock()
        mock_ep.name = "enterprise"
        mock_ep.load.return_value = lambda: {
            "edition": "enterprise",
            "license": {
                "type": "personal",
                "holder": "Test User",
                "expires": "2027-04-09",
            },
            "features": ["sso", "rbac", "audit"],
        }

        app = FastAPI()
        app.dependency_overrides[get_settings] = lambda: Settings(dev_mode=True)
        app.include_router(router, prefix="/edition")
        client = TestClient(app)
        with patch(
            "chaoscypher_cortex.features.edition.api.entry_points",
            return_value=[mock_ep],
        ):
            response = client.get("/edition")

        assert response.status_code == 200
        data = response.json()
        assert data["edition"] == "enterprise"
        assert data["license"]["type"] == "personal"
        assert "sso" in data["features"]
        assert "nodes" in data["features"]
