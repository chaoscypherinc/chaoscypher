# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for RateLimitMiddleware."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chaoscypher_core.app_config import RateLimitSettings
from chaoscypher_cortex.shared.middleware import rate_limit
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


def test_block_is_per_client_ip() -> None:
    """One client exhausting its budget must not lock out another client."""
    app = _app()
    client_a = TestClient(app, client=("203.0.113.1", 50000))
    client_b = TestClient(app, client=("203.0.113.2", 50000))

    assert client_a.post("/api/v1/auth/login").status_code == 200
    assert client_a.post("/api/v1/auth/login").status_code == 429

    assert client_b.post("/api/v1/auth/login").status_code == 200


def test_window_expiry_resets_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requests older than the window stop counting, so a block lifts by itself."""
    now = [1000.0]
    monkeypatch.setattr(rate_limit, "time", SimpleNamespace(monotonic=lambda: now[0]))
    client = TestClient(_app())

    assert client.post("/api/v1/auth/login").status_code == 200
    now[0] += 30  # still inside the 60s window
    assert client.post("/api/v1/auth/login").status_code == 429
    now[0] += 31  # 61s after the only counted request
    assert client.post("/api/v1/auth/login").status_code == 200
