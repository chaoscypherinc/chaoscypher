# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for SecurityHeadersMiddleware."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chaoscypher_cortex.shared.middleware.security_headers import (
    SecurityHeadersMiddleware,
)


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/hello")
    def hello() -> dict:
        return {"hi": True}

    return app


def test_security_headers_present() -> None:
    r = TestClient(_app()).get("/hello")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert r.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "camera=()" in r.headers.get("Permissions-Policy", "")
    csp = r.headers.get("Content-Security-Policy", "")
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "base-uri 'self'" in csp
    assert "form-action 'self'" in csp
    # connect-src should NOT allow arbitrary ws/wss hosts (prompt-injection defense)
    assert "connect-src 'self'" in csp


def test_headers_apply_to_errors() -> None:
    r = TestClient(_app()).get("/does-not-exist")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("Content-Security-Policy") is not None


def test_setdefault_does_not_overwrite_existing() -> None:
    """Middleware must not overwrite headers a route already set.

    setdefault semantics — e.g. a route returning a relaxed CSP for a
    specific HTML response should win over the default.
    """
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/custom-csp")
    def route() -> dict:
        from starlette.responses import JSONResponse

        return JSONResponse(
            {"ok": True},
            headers={"Content-Security-Policy": "default-src 'none'"},
        )

    r = TestClient(app).get("/custom-csp")
    assert r.headers.get("Content-Security-Policy") == "default-src 'none'"
