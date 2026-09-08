# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Adapter cleanup middleware — prevents connection pool exhaustion.

Initializes a request-scoped list for tracking SqliteAdapter instances
created during the request. Once the response has been fully sent — the
last body chunk included — all tracked adapters are disconnected,
returning their connections to the pool.

Pure ASGI, deliberately, and NOT ``BaseHTTPMiddleware``
-------------------------------------------------------
``BaseHTTPMiddleware.dispatch`` hands control back as soon as the
downstream app produces a response object. For a normal response that is
also the end of the response, so teardown in a ``finally`` around
``call_next`` is correct. For a **streaming** response (SSE, and any
``StreamingResponse``) it is not: ``call_next`` returns while the body
generator is still suspended on its first ``await``, so the ``finally``
disconnected adapters out from under a generator that was still running.
Anything the generator did afterwards hit ``_ensure_connected()`` and
raised ``RuntimeError``, which the chat SSE endpoint turned into a
terminal ``STREAM_INTERNAL_ERROR`` frame — live chat streaming died
before relaying a token.

A pure-ASGI middleware has no such gap: ``await self.app(...)`` returns
only once the entire response, streaming body included, has been sent.
The ``finally`` therefore runs at the real end of the request in every
case — normal response, streamed response, client disconnect, and an
exception raised from the endpoint or from mid-stream — with no need to
inspect ASGI messages for ``more_body``.

This middleware is registered last in ``app_factory`` so it runs
outermost, which is what makes the guarantee above hold for the whole
downstream stack.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from chaoscypher_core.database.adapter_factory import _request_adapters


if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

logger = structlog.get_logger(__name__)


class AdapterCleanupMiddleware:
    """Disconnect all per-request SqliteAdapters once the response is sent."""

    def __init__(self, app: ASGIApp) -> None:
        """Store the downstream ASGI app."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Track adapters for the request, then disconnect them all."""
        if scope["type"] != "http":
            # Lifespan and websocket scopes create no request-scoped adapters.
            await self.app(scope, receive, send)
            return

        adapters: list = []
        token = _request_adapters.set(adapters)
        try:
            await self.app(scope, receive, send)
        finally:
            for adapter in adapters:
                try:
                    adapter.disconnect()
                except Exception:
                    logger.warning("adapter_cleanup_failed", exc_info=True)
            _request_adapters.reset(token)
