# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""MCP Streamable HTTP transport for Cortex.

Provides an ASGI application that delegates MCP protocol handling to
the StreamableHTTPSessionManager. Mounted on the FastAPI app at
/api/v1/mcp to handle MCP Streamable HTTP protocol messages.

Auth: handled at the nginx edge via ``auth_request /api/v1/auth/verify``
like every other ``/api/`` route, so this transport does no auth of its
own. Cortex only trusts the forwarded identity when nginx adds the shared
edge-auth token marker.

MCP mode: resolved from ``settings.mcp.mode`` (server-wide) via
``scope["state"]["effective_mcp_mode"]`` so downstream tool handlers can
consult it. Per-key MCP ceilings were removed when the auth.db API key
store was deleted — there is no per-key override anymore.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from starlette.datastructures import Headers
from starlette.responses import JSONResponse


if TYPE_CHECKING:
    from starlette.types import Receive, Scope, Send

logger = structlog.get_logger(__name__)


class MCPTransportApp:
    """ASGI application wrapping the MCP session manager.

    Handles POST (JSON-RPC requests), GET (SSE streaming),
    and DELETE (session termination) for MCP Streamable HTTP.

    Also resolves the effective MCP mode for the request:
      effective = min(server_mode, key_mode)  where write > read.
    """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Forward ASGI request to the MCP session manager.

        Auth is enforced by nginx ``auth_request`` upstream; this transport
        does not re-validate user sessions. We still resolve the effective
        MCP mode from server config and stash it on ASGI scope state for
        downstream tool handlers.
        """
        if scope["type"] != "http":
            return

        from chaoscypher_core.app_config import get_settings
        from chaoscypher_cortex.shared.auth.dependencies import has_valid_edge_token

        settings = get_settings()
        headers = Headers(raw=scope.get("headers", []))
        username = headers.get("x-auth-user")
        if not settings.dev_mode and (not username or not has_valid_edge_token(headers, settings)):
            await JSONResponse(
                {"error": "authentication_required"},
                status_code=401,
            )(scope, receive, send)
            return

        scope.setdefault("state", {})
        server_mode = settings.mcp.mode
        scope["state"]["effective_mcp_mode"] = _compute_effective_mcp_mode(
            server_mode, key_mode=None
        )

        from chaoscypher_cortex.features.mcp.service import get_mcp_manager

        mcp_manager = get_mcp_manager()

        await mcp_manager.handle_request(scope, receive, send)


def _compute_effective_mcp_mode(
    server_mode: str,
    key_mode: str | None,
) -> str:
    """Return the min of server_mode and key_mode, treating write > read.

    Args:
        server_mode: The static server-level MCP mode (``"read"`` or
            ``"write"``).
        key_mode: Per-key ceiling (``"read"``, ``"write"``, or ``None``).

    Returns:
        The effective mode. If ``key_mode`` is ``None`` the server
        setting applies; otherwise the narrower of the two wins.

    """
    if key_mode is None:
        return server_mode
    if "read" in (server_mode, key_mode):
        return "read"
    return "write"


import functools


@functools.cache
def get_mcp_transport() -> MCPTransportApp:
    """Return the singleton ``MCPTransportApp`` ASGI app.

    Cached so the same instance is reused across callers (main.py's
    ``app.mount`` and any future callers both need the same object),
    but created on first access rather than at module load so tests can
    reset it via ``get_mcp_transport.cache_clear()``.
    """
    return MCPTransportApp()
