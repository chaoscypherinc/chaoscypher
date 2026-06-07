# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""App-layer security headers.

Nginx applies equivalent headers when deployed via the bundled config, but this
middleware ensures headers are set even when the app runs behind a different
proxy or directly via uvicorn in dev. Uses ``setdefault`` so routes that set
their own security headers (e.g. relaxed CSP for a specific HTML response) are
not overridden.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint


if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "connect-src 'self'; "
    "font-src 'self' data:; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

_PERMISSIONS = "camera=(), microphone=(), geolocation=()"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add standard security headers to every response (setdefault semantics)."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Add default security headers to the downstream response."""
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", _PERMISSIONS)
        response.headers.setdefault("Content-Security-Policy", _CSP)
        return response
