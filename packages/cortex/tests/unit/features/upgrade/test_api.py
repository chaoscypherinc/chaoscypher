# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for upgrade router auth gating."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_cortex.features.upgrade.api import get_upgrade_service, router


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
