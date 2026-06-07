# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""MCP service wiring for Cortex.

Creates an MCP Server using the shared Core ``create_mcp_server()`` factory,
which provides the full tool set including extraction orchestration and
document processing. Manages the Streamable HTTP session manager lifecycle
for the mounted ASGI transport.
"""

from typing import Any

import structlog
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager


logger = structlog.get_logger(__name__)


class MCPServiceManager:
    """Manages the MCP Streamable HTTP session manager lifecycle.

    Singleton that creates an Engine instance, builds the MCP server
    via ``create_mcp_server()``, and manages the StreamableHTTPSessionManager
    start/stop lifecycle. Uses the same server factory as the CLI, so
    all tools (including extraction orchestration) are available.
    """

    def __init__(self) -> None:
        """Initialize the manager (does not start the session manager)."""
        self._manager: StreamableHTTPSessionManager | None = None
        self._run_context: Any = None
        self._engine: Any = None

    async def start(self) -> None:
        """Initialize and start the MCP session manager.

        Creates an Engine instance from Cortex settings, builds the
        MCP server with all tools, and starts the session manager.
        """
        from chaoscypher_core.app_config import get_settings
        from chaoscypher_core.app_config.engine_factory import (
            build_engine_settings,
        )
        from chaoscypher_core.bootstrap import Engine
        from chaoscypher_core.mcp.server import create_mcp_server

        backend_settings = get_settings()
        engine_settings = build_engine_settings(backend_settings)
        db_name = backend_settings.current_database

        # Create Engine — same as CLI, provides all repos and services
        data_dir = backend_settings.data_dir / "databases" / db_name
        self._engine = Engine(
            data_dir=data_dir,
            settings=engine_settings,
            initialize_db=False,  # Cortex already initializes DB at startup
        )

        # Build MCP server with full tool set (same as CLI)
        server = create_mcp_server(self._engine)

        self._manager = StreamableHTTPSessionManager(
            app=server,
            stateless=True,
        )
        self._run_context = self._manager.run()
        await self._run_context.__aenter__()

        logger.info(
            "mcp_session_manager_started",
            database=db_name,
            mode=engine_settings.mcp.mode,
        )

    async def stop(self) -> None:
        """Stop the MCP session manager and release resources."""
        if self._run_context:
            await self._run_context.__aexit__(None, None, None)
            self._run_context = None
            self._manager = None
        if self._engine:
            self._engine.close()
            self._engine = None
        logger.info("mcp_session_manager_stopped")

    async def handle_request(self, scope: Any, receive: Any, send: Any) -> None:
        """Forward an ASGI request to the session manager.

        Args:
            scope: ASGI scope dict.
            receive: ASGI receive callable.
            send: ASGI send callable.

        Raises:
            RuntimeError: If the session manager is not started.

        """
        if self._manager is None:
            msg = "MCP session manager not started"
            raise RuntimeError(msg)
        await self._manager.handle_request(scope, receive, send)


import functools


@functools.cache
def get_mcp_manager() -> MCPServiceManager:
    """Return the singleton ``MCPServiceManager`` instance.

    Cached so the same object is shared across callers, but created on
    first access rather than at module load — which keeps
    ``import chaoscypher_cortex`` side-effect-light and lets tests use
    ``get_mcp_manager.cache_clear()`` to reset between runs.
    """
    return MCPServiceManager()
