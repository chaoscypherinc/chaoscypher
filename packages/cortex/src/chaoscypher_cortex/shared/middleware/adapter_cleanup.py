# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Adapter cleanup middleware — prevents connection pool exhaustion.

Initializes a request-scoped list for tracking SqliteAdapter instances
created during the request. After the response is sent, all tracked
adapters are disconnected, returning their connections to the pool.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from starlette.middleware.base import BaseHTTPMiddleware

from chaoscypher_core.database.adapter_factory import _request_adapters


if TYPE_CHECKING:
    from starlette.middleware.base import RequestResponseEndpoint
    from starlette.requests import Request
    from starlette.responses import Response

logger = structlog.get_logger(__name__)


class AdapterCleanupMiddleware(BaseHTTPMiddleware):
    """Disconnect all per-request SqliteAdapters after response is sent."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Initialize adapter tracking, process request, then cleanup."""
        adapters: list = []
        token = _request_adapters.set(adapters)
        try:
            return await call_next(request)
        finally:
            for adapter in adapters:
                try:
                    adapter.disconnect()
                except Exception:
                    logger.warning("adapter_cleanup_failed", exc_info=True)
            _request_adapters.reset(token)
