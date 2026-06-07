# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for top-level middleware (body-size, upgrade-gate)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from chaoscypher_core.app_config import BatchSettings, Settings, set_settings
from chaoscypher_cortex.middleware import enforce_body_size_limit


def _app() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(enforce_body_size_limit)

    @app.post("/echo")
    async def echo(request: Request) -> dict:
        body = await request.body()
        return {"len": len(body)}

    return app


@pytest.fixture
def tiny_body_limit() -> None:
    """Pin max_request_body_mb=1 so a small payload deterministically trips 413.

    The default is 128 MB; without this shim the tests would have to allocate
    >128 MB to fire the path, or use a conditional assertion that silently
    no-ops if the limit changes.
    """
    settings = Settings(batching=BatchSettings(max_request_body_mb=1))
    set_settings(settings)
    yield
    # Restore module-global to defaults so unrelated tests aren't affected.
    set_settings(Settings())


def test_body_size_limit_returns_unified_json_envelope(tiny_body_limit: None) -> None:
    client = TestClient(_app())
    huge = b"x" * (1024 * 1024 * 2)  # 2 MB > 1 MB cap
    r = client.post("/echo", content=huge)
    assert r.status_code == 413
    body = r.json()
    assert body["error"] == "body_too_large"
    assert body["details"]["max_mb"] == 1


def test_body_size_limit_returns_html_for_browser(tiny_body_limit: None) -> None:
    client = TestClient(_app())
    huge = b"x" * (1024 * 1024 * 2)
    r = client.post("/echo", content=huge, headers={"Accept": "text/html"})
    assert r.status_code == 413
    assert r.headers["content-type"].startswith("text/html")
    assert "Chaos Cypher" in r.text


def test_invalid_content_length_returns_unified_envelope() -> None:
    client = TestClient(_app())
    # Send a bad Content-Length via httpx's raw header support. starlette
    # parses the header before the middleware sees it, but the middleware
    # also re-reads request.headers and re-parses — so a non-integer value
    # routes through ``_bad_content_length()``.
    r = client.post("/echo", headers={"Content-Length": "not-an-int"}, content=b"")
    assert r.status_code == 400
    body = r.json()
    assert body["error"] == "bad_content_length"
