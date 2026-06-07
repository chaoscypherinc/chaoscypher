# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for RateLimitMiddleware."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chaoscypher_core.app_config import RateLimitSettings
from chaoscypher_cortex.shared.middleware.rate_limit import RateLimitMiddleware


def _app() -> FastAPI:
    app = FastAPI()
    settings = RateLimitSettings(
        login_max_requests=1,
        login_window_seconds=60,
    )
    app.add_middleware(RateLimitMiddleware, settings=settings)

    @app.post("/api/v1/auth/login")
    async def login() -> dict:
        return {"ok": True}

    return app


def test_429_returns_unified_json_envelope() -> None:
    client = TestClient(_app())
    r1 = client.post("/api/v1/auth/login")
    assert r1.status_code == 200
    r2 = client.post("/api/v1/auth/login")
    assert r2.status_code == 429
    body = r2.json()
    assert body["error"] == "rate_limited"
    assert "details" in body
    assert r2.headers["retry-after"]


def test_429_returns_html_for_browser() -> None:
    client = TestClient(_app())
    client.post("/api/v1/auth/login")
    r = client.post("/api/v1/auth/login", headers={"Accept": "text/html"})
    assert r.status_code == 429
    assert r.headers["content-type"].startswith("text/html")
    assert "Chaos Cypher" in r.text
    assert r.headers["retry-after"]
