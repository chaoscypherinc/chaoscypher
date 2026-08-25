# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""/health/auth helps operators diagnose silent 401 storms from nginx misconfiguration."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chaoscypher_cortex.features.health.api import router


@pytest.fixture
def client() -> TestClient:
    """TestClient for the health router (no auth required for /health/auth)."""
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def test_health_auth_reports_diagnostic_fields(client: TestClient) -> None:
    response = client.get("/health/auth")
    assert response.status_code == 200
    body = response.json()
    assert "x_auth_user_present" in body  # whether the header arrived from nginx
    assert "recent_failed_attempts" in body  # 5-minute window counter
    assert "last_failure_at" in body  # ISO timestamp or None
    assert isinstance(body["x_auth_user_present"], bool)
    assert isinstance(body["recent_failed_attempts"], int)


def test_health_auth_no_header(client: TestClient) -> None:
    """Without X-Auth-User header, x_auth_user_present is False."""
    response = client.get("/health/auth")
    assert response.status_code == 200
    assert response.json()["x_auth_user_present"] is False


def test_health_auth_with_header(client: TestClient) -> None:
    """With X-Auth-User header, x_auth_user_present is True."""
    response = client.get("/health/auth", headers={"X-Auth-User": "admin"})
    assert response.status_code == 200
    assert response.json()["x_auth_user_present"] is True


def test_health_auth_last_failure_at_none_initially(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """last_failure_at is None when no failures have been recorded."""
    from chaoscypher_cortex.features.local_auth import auth_failure_tracker as aft

    # The tracker is a process-global singleton; earlier tests in the
    # session record real 401s into it. Swap in a fresh tracker so
    # "initially" is well-defined regardless of test order.
    monkeypatch.setattr(aft, "tracker", aft.AuthFailureTracker())

    response = client.get("/health/auth")
    assert response.status_code == 200
    body = response.json()
    assert body["last_failure_at"] is None
    assert body["recent_failed_attempts"] == 0

    # And after a failure is recorded, it becomes an ISO-8601 string.
    aft.tracker.record_failure()
    body = client.get("/health/auth").json()
    assert isinstance(body["last_failure_at"], str)
    assert body["recent_failed_attempts"] == 1
