# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for SecurityHeadersMiddleware."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chaoscypher_cortex.shared.middleware.security_headers import (
    SecurityHeadersMiddleware,
)


def _parse_csp(csp: str) -> dict[str, str]:
    """Parse a CSP header into {directive: value} for exact-match assertions."""
    directives: dict[str, str] = {}
    for part in csp.split("; "):
        name, _, value = part.partition(" ")
        directives[name] = value
    return directives


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
    # Exact per-directive matches: substring checks would still pass if a
    # directive were widened (e.g. "script-src 'self' 'unsafe-inline'" or
    # "connect-src 'self' ws: https:" keep the original value as a prefix).
    csp = _parse_csp(r.headers.get("Content-Security-Policy", ""))
    assert csp["default-src"] == "'self'"
    assert csp["script-src"] == "'self'"
    # connect-src must NOT allow arbitrary ws/wss hosts (prompt-injection defense)
    assert csp["connect-src"] == "'self'"
    assert csp["frame-ancestors"] == "'none'"
    assert csp["base-uri"] == "'self'"
    assert csp["form-action"] == "'self'"


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
