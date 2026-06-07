# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for GET /api/v1/health auth-conditional detail gating.

The health endpoint is intentionally public so Docker HEALTHCHECK can
reach it without auth.  Unauthenticated callers must receive only
``{healthy, status}``; authenticated callers get the full ``checks``
payload.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from chaoscypher_core.app_config import LocalAuthSettings, Settings, get_settings
from chaoscypher_cortex.features.health.api import get_health_service, router
from chaoscypher_cortex.features.health.models import HealthCheckItem, HealthCheckResponse


def _make_full_response() -> HealthCheckResponse:
    """Minimal full HealthCheckResponse fixture (simulates an authenticated result)."""
    return HealthCheckResponse(
        healthy=True,
        status="ok",
        checks={
            "ollama": HealthCheckItem(status="ok", message="Ollama running"),
            "queue": HealthCheckItem(status="ok", message="Queue reachable"),
            "llm_worker": HealthCheckItem(status="ok", message="Worker up"),
        },
    )


def _make_mock_service(full_response: HealthCheckResponse) -> MagicMock:
    """Return a mock HealthService whose check_health respects the detailed kwarg."""
    svc = MagicMock()

    async def _check_health(*, detailed: bool = True) -> HealthCheckResponse:
        if detailed:
            return full_response
        return HealthCheckResponse(
            healthy=full_response.healthy,
            status=full_response.status,
        )

    svc.check_health = _check_health
    return svc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def full_response() -> HealthCheckResponse:
    """Reusable full health response."""
    return _make_full_response()


@pytest.fixture
def unauth_client(full_response: HealthCheckResponse) -> TestClient:
    """TestClient with no auth headers and ``dev_mode=False``.

    Overrides ``get_settings`` to disable the dev-mode fallback that would
    cause every request to be treated as authenticated in a test run.
    Overrides ``get_health_service`` to avoid real I/O.
    """
    app = FastAPI()
    app.dependency_overrides[get_settings] = lambda: Settings(dev_mode=False)
    app.dependency_overrides[get_health_service] = lambda: _make_mock_service(full_response)
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def authed_client(full_response: HealthCheckResponse) -> TestClient:
    """TestClient that presents a valid edge-token + X-Auth-User pair.

    ``dev_mode=False`` so the real edge-token path is exercised.
    """
    edge_secret = "test-edge-secret"
    settings = Settings(
        dev_mode=False,
        local_auth=LocalAuthSettings(edge_auth_token=SecretStr(edge_secret)),
    )
    app = FastAPI()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_health_service] = lambda: _make_mock_service(full_response)
    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)
    client.headers.update(
        {
            "X-Auth-User": "admin",
            "X-Auth-Edge-Token": edge_secret,
        }
    )
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_health_minimal_when_unauthenticated(unauth_client: TestClient) -> None:
    """Unauthenticated /health must not expose provider/model/check details."""
    resp = unauth_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    # Must always have the two safe scalars
    assert body["status"] in {"ok", "healthy", "degraded", "unhealthy"}
    assert isinstance(body["healthy"], bool)
    # Must NOT include any check details
    forbidden_keys = {"checks"}
    present_forbidden = set(body.keys()) & forbidden_keys
    assert not present_forbidden, f"Forbidden keys present in unauth response: {present_forbidden}"
    # checks key must be absent or null
    assert body.get("checks") is None


def test_health_detailed_when_authenticated(authed_client: TestClient) -> None:
    """Authenticated callers see the full payload including checks."""
    resp = authed_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["healthy"], bool)
    assert body["status"] in {"ok", "degraded"}
    # Full payload includes checks
    assert "checks" in body
    assert body["checks"] is not None
    assert len(body["checks"]) > 0


def test_health_200_without_auth(unauth_client: TestClient) -> None:
    """Unauthenticated /health returns 200 (not 401) — endpoint is public."""
    resp = unauth_client.get("/health")
    assert resp.status_code == 200


def test_health_status_field_reflects_healthy(full_response: HealthCheckResponse) -> None:
    """status='ok' when healthy=True, 'degraded' when healthy=False."""
    assert full_response.status == "ok"
    degraded = HealthCheckResponse(healthy=False, status="degraded")
    assert degraded.status == "degraded"
