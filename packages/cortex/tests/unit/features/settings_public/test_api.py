# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Integration test for GET /api/v1/settings/public."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_cortex.features.settings_public.api import router


@pytest.fixture
def test_client() -> TestClient:
    """TestClient that mounts only the settings_public router.

    The endpoint is intentionally auth-exempt (the SPA needs it to render the
    login screen), so no auth headers / overrides are required.
    """
    app = FastAPI()
    app.dependency_overrides[get_settings] = lambda: Settings()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def test_public_settings_returns_200_and_known_shape(test_client: TestClient) -> None:
    r = test_client.get("/api/v1/settings/public")
    assert r.status_code == 200
    body = r.json()
    assert "pagination_default_page_size" in body
    assert "search_min_similarity_threshold" in body
    assert "recovery_warn_threshold" in body
    assert "http_default_timeout_ms" in body


def test_public_settings_contains_no_secret_keys(test_client: TestClient) -> None:
    r = test_client.get("/api/v1/settings/public")
    body = r.json()
    for key in body:
        assert "password" not in key.lower()
        assert "secret" not in key.lower()
        assert "token" not in key.lower()
        assert "api_key" not in key.lower()


def test_access_hint_loopback_localhost(test_client: TestClient) -> None:
    r = test_client.get("/api/v1/settings/host", headers={"Host": "localhost:8080"})
    assert r.status_code == 200
    body = r.json()
    assert body["request_host"] == "localhost"
    assert body["is_loopback"] is True


def test_access_hint_loopback_ipv4(test_client: TestClient) -> None:
    r = test_client.get("/api/v1/settings/host", headers={"Host": "127.0.0.1"})
    assert r.status_code == 200
    body = r.json()
    assert body["request_host"] == "127.0.0.1"
    assert body["is_loopback"] is True


def test_access_hint_loopback_ipv6(test_client: TestClient) -> None:
    r = test_client.get("/api/v1/settings/host", headers={"Host": "[::1]:8080"})
    assert r.status_code == 200
    body = r.json()
    assert body["request_host"] == "::1"
    assert body["is_loopback"] is True


def test_access_hint_strips_port(test_client: TestClient) -> None:
    r = test_client.get("/api/v1/settings/host", headers={"Host": "localhost:9999"})
    assert r.status_code == 200
    assert r.json()["request_host"] == "localhost"
