# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Request correlation ID middleware."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from chaoscypher_core.utils.id import generate_id


if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Inject a unique request ID into every request for log correlation."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Add correlation ID to request state and response headers."""
        client_id = request.headers.get("X-Request-ID", "")
        # Validate client-provided ID: max 128 chars, alphanumeric + hyphens only
        if client_id and len(client_id) <= 128 and all(c.isalnum() or c == "-" for c in client_id):
            request_id = client_id
        else:
            request_id = generate_id()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
