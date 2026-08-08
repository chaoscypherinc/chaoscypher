# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for upgrade router auth gating and rollback error mapping."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_core.exceptions import ChaosCypherException, NotFoundError, ValidationError
from chaoscypher_cortex.features.upgrade.api import get_upgrade_service, router
from chaoscypher_cortex.shared.api.errors import chaoscypher_exception_handler


@pytest.fixture
def unauth_client() -> TestClient:
    """TestClient with no auth headers and dev_mode=False.

    Overrides both get_settings (to disable dev-mode fallback) and
    get_upgrade_service (to avoid touching any real database on disk).
    """
    app = FastAPI()
    app.dependency_overrides[get_settings] = lambda: Settings(dev_mode=False)
    app.dependency_overrides[get_upgrade_service] = lambda: MagicMock()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def test_upgrade_pending_requires_auth(unauth_client: TestClient) -> None:
    resp = unauth_client.get("/upgrade/pending")
    assert resp.status_code == 401


def test_upgrade_apply_requires_auth(unauth_client: TestClient) -> None:
    resp = unauth_client.post("/upgrade/apply")
    assert resp.status_code == 401


def test_upgrade_rollback_requires_auth(unauth_client: TestClient) -> None:
    resp = unauth_client.post("/upgrade/rollback")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Rollback failure mapping (core exceptions → HTTP via the domain handler)
# ---------------------------------------------------------------------------


def _authed_client(service: MagicMock) -> TestClient:
    """TestClient in dev_mode with the domain exception handler registered."""
    app = FastAPI()
    app.dependency_overrides[get_settings] = lambda: Settings(dev_mode=True)
    app.dependency_overrides[get_upgrade_service] = lambda: service
    app.add_exception_handler(ChaosCypherException, chaoscypher_exception_handler)  # type: ignore[arg-type]
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def test_rollback_without_backup_maps_to_400() -> None:
    """Core ValidationError from rollback keeps the pre-migration 400."""
    service = MagicMock()
    service.rollback.side_effect = ValidationError("No backup available to roll back to.")

    resp = _authed_client(service).post("/upgrade/rollback")

    assert resp.status_code == 400
    assert resp.json()["error"] == "VALIDATION_ERROR"


def test_rollback_missing_backup_file_maps_to_404() -> None:
    """Core NotFoundError from rollback keeps the pre-migration 404."""
    service = MagicMock()
    service.rollback.side_effect = NotFoundError("Backup", "/data/backups/gone.db")

    resp = _authed_client(service).post("/upgrade/rollback")

    assert resp.status_code == 404
    assert resp.json()["error"] == "NOT_FOUND"
