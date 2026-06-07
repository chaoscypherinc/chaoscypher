# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for edition router auth gating."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_cortex.features.edition.api import router


@pytest.fixture
def unauth_client() -> TestClient:
    """TestClient with no auth headers and dev_mode=False.

    Overrides get_settings to disable dev-mode fallback.
    """
    app = FastAPI()
    app.dependency_overrides[get_settings] = lambda: Settings(dev_mode=False)
    app.include_router(router, prefix="/edition")
    return TestClient(app, raise_server_exceptions=False)


def test_get_edition_requires_auth(unauth_client: TestClient) -> None:
    resp = unauth_client.get("/edition")
    assert resp.status_code == 401
