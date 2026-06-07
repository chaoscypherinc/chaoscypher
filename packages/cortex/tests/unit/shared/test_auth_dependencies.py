# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for trusted edge-auth dependency behavior."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from chaoscypher_core.app_config import LocalAuthSettings, Settings, get_settings
from chaoscypher_cortex.shared.auth.dependencies import (
    CurrentUsername,  # noqa: TC001 — used as runtime FastAPI annotation in _client route
)


def _client(settings: Settings) -> TestClient:
    app = FastAPI()
    app.dependency_overrides[get_settings] = lambda: settings

    @app.get("/protected")
    def protected(username: CurrentUsername) -> dict[str, str]:
        return {"username": username}

    return TestClient(app)


def test_rejects_spoofed_auth_user_without_edge_token() -> None:
    settings = Settings(
        local_auth=LocalAuthSettings(edge_auth_token=SecretStr("edge-secret")),
    )
    client = _client(settings)

    response = client.get("/protected", headers={"X-Auth-User": "admin"})

    assert response.status_code == 401


def test_accepts_auth_user_with_matching_edge_token() -> None:
    settings = Settings(
        local_auth=LocalAuthSettings(edge_auth_token=SecretStr("edge-secret")),
    )
    client = _client(settings)

    response = client.get(
        "/protected",
        headers={"X-Auth-User": "admin", "X-Auth-Edge-Token": "edge-secret"},
    )

    assert response.status_code == 200
    assert response.json() == {"username": "admin"}


def test_rejects_auth_user_with_wrong_edge_token() -> None:
    settings = Settings(
        local_auth=LocalAuthSettings(edge_auth_token=SecretStr("edge-secret")),
    )
    client = _client(settings)

    response = client.get(
        "/protected",
        headers={"X-Auth-User": "admin", "X-Auth-Edge-Token": "wrong"},
    )

    assert response.status_code == 401


def test_dev_mode_still_allows_direct_requests_without_header() -> None:
    settings = Settings(dev_mode=True)
    client = _client(settings)

    response = client.get("/protected")

    assert response.status_code == 200
    assert response.json() == {"username": "dev"}
