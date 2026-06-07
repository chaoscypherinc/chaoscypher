# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for databases router auth gating on GET handlers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_cortex.features.databases.api import get_databases_service, router


@pytest.fixture
def unauth_client() -> TestClient:
    """TestClient with no auth headers and dev_mode=False.

    Overrides both get_settings (to disable dev-mode fallback) and
    get_databases_service (to avoid touching any real database on disk).
    """
    app = FastAPI()
    app.dependency_overrides[get_settings] = lambda: Settings(dev_mode=False)
    app.dependency_overrides[get_databases_service] = lambda: MagicMock()
    app.include_router(router, prefix="/databases")
    return TestClient(app, raise_server_exceptions=False)


def test_list_databases_requires_auth(unauth_client: TestClient) -> None:
    resp = unauth_client.get("/databases")
    assert resp.status_code == 401


def test_get_current_database_requires_auth(unauth_client: TestClient) -> None:
    resp = unauth_client.get("/databases/current")
    assert resp.status_code == 401


def test_get_database_requires_auth(unauth_client: TestClient) -> None:
    resp = unauth_client.get("/databases/default")
    assert resp.status_code == 401
