# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Admin plugin reload endpoint tests."""

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chaoscypher_cortex.features.admin_plugins.api import router as admin_plugins_router
from chaoscypher_cortex.shared.auth.dependencies import get_current_username


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(admin_plugins_router, prefix="/api/v1")
    # Bypass auth for the test.
    app.dependency_overrides[get_current_username] = lambda: "test-user"
    return app


def test_reload_endpoint_invalidates_factories() -> None:
    app = _make_app()
    client = TestClient(app)

    # Patch where the name is used in api.py (the `from ... import`
    # binding), not where it is defined.
    with patch(
        "chaoscypher_cortex.features.admin_plugins.api.reload_all_plugin_registries"
    ) as mock_reload:
        mock_reload.return_value = {
            "invalidated": ["LoaderRegistry", "ToolRegistry"],
            "total": 2,
        }
        resp = client.post("/api/v1/admin/plugins/reload")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert "LoaderRegistry" in body["invalidated"]
    mock_reload.assert_called_once()


def test_reload_endpoint_requires_auth() -> None:
    # Without overriding get_current_username, the dependency chain should reject.
    app = FastAPI()
    app.include_router(admin_plugins_router, prefix="/api/v1")
    client = TestClient(app)
    resp = client.post("/api/v1/admin/plugins/reload")
    assert resp.status_code in (401, 403)
