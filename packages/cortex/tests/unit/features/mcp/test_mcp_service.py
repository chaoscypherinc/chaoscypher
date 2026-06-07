# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for MCP transport and service manager.

Tests cover:
- MCPServiceManager.handle_request raises RuntimeError when not started
- MCPServiceManager.stop is a no-op when not started
- MCPServiceManager.stop cleans up when started
- MCPServiceManager.handle_request delegates to the inner session manager
- MCPTransportApp: non-http scope is ignored
- MCPTransportApp: http scope is forwarded to mcp_manager (auth is handled
  at the nginx edge via auth_request; the transport itself is unauthenticated).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_cortex.features.mcp.api import MCPTransportApp
from chaoscypher_cortex.features.mcp.service import MCPServiceManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_http_scope(headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    """Return a minimal ASGI HTTP scope dict."""
    return {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/mcp",
        "headers": headers or [],
    }


def _make_non_http_scope(scope_type: str = "websocket") -> dict:
    """Return a non-http ASGI scope dict."""
    return {"type": scope_type}


# ---------------------------------------------------------------------------
# TestMCPServiceManager
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMCPServiceManager:
    """Tests for MCPServiceManager lifecycle methods."""

    @pytest.mark.asyncio
    async def test_handle_request_raises_when_not_started(self) -> None:
        """handle_request raises RuntimeError when _manager is None (not started)."""
        manager = MCPServiceManager()
        assert manager._manager is None

        with pytest.raises(RuntimeError, match="not started"):
            await manager.handle_request(
                scope=_make_http_scope(),
                receive=AsyncMock(),
                send=AsyncMock(),
            )

    @pytest.mark.asyncio
    async def test_stop_when_not_started_is_noop(self) -> None:
        """stop() is a no-op and does not raise when manager was never started."""
        manager = MCPServiceManager()
        # Should not raise
        await manager.stop()
        assert manager._manager is None
        assert manager._run_context is None
        assert manager._engine is None

    @pytest.mark.asyncio
    async def test_stop_cleans_up_when_started(self) -> None:
        """stop() calls __aexit__ on run_context and closes engine when started."""
        manager = MCPServiceManager()

        # Manually wire up mock internals (simulate a started manager)
        mock_run_context = AsyncMock()
        mock_run_context.__aexit__ = AsyncMock(return_value=None)
        mock_engine = MagicMock()

        manager._manager = MagicMock()
        manager._run_context = mock_run_context
        manager._engine = mock_engine

        await manager.stop()

        mock_run_context.__aexit__.assert_awaited_once_with(None, None, None)
        mock_engine.close.assert_called_once()
        assert manager._manager is None
        assert manager._run_context is None
        assert manager._engine is None

    @pytest.mark.asyncio
    async def test_handle_request_delegates_to_manager(self) -> None:
        """handle_request forwards ASGI triple to the underlying session manager."""
        manager = MCPServiceManager()
        mock_inner = AsyncMock()
        manager._manager = MagicMock()
        manager._manager.handle_request = mock_inner

        scope = _make_http_scope()
        receive = AsyncMock()
        send = AsyncMock()

        await manager.handle_request(scope, receive, send)

        mock_inner.assert_awaited_once_with(scope, receive, send)


# ---------------------------------------------------------------------------
# TestMCPTransportApp
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMCPTransportApp:
    """Tests for MCPTransportApp ASGI callable.

    Auth is handled at the nginx edge via ``auth_request``, so the transport
    itself is unauthenticated — its only job is to forward http scopes to
    ``mcp_manager`` and ignore everything else.
    """

    @pytest.mark.asyncio
    async def test_non_http_scope_returns_immediately(self) -> None:
        """Non-http scope type (e.g. websocket) is ignored without calling send."""
        app = MCPTransportApp()
        send = AsyncMock()
        receive = AsyncMock()

        await app(_make_non_http_scope("websocket"), receive, send)

        send.assert_not_called()

    @pytest.mark.asyncio
    async def test_lifespan_scope_returns_immediately(self) -> None:
        """Lifespan scope type is ignored without calling send."""
        app = MCPTransportApp()
        send = AsyncMock()
        receive = AsyncMock()

        await app(_make_non_http_scope("lifespan"), receive, send)

        send.assert_not_called()

    @pytest.mark.asyncio
    async def test_http_scope_forwards_to_mcp_manager(self) -> None:
        """An HTTP scope is forwarded to the manager returned by ``get_mcp_manager``."""
        app = MCPTransportApp()
        # nginx normally injects ``x-auth-user``; replicate that here so the
        # transport's auth gate (added in Phase 4) lets the request through.
        scope = _make_http_scope(
            headers=[(b"x-auth-user", b"test-user"), (b"x-edge-token", b"test")]
        )
        receive = AsyncMock()
        send = AsyncMock()

        mock_manager = AsyncMock()
        mock_settings = MagicMock()
        mock_settings.dev_mode = True
        mock_settings.mcp_mode = "read"

        # The transport now resolves the manager lazily through the cached
        # ``get_mcp_manager()`` factory rather than a module-level singleton,
        # so the patch target is the factory.
        with (
            patch(
                "chaoscypher_core.app_config.get_settings",
                return_value=mock_settings,
            ),
            patch(
                "chaoscypher_cortex.features.mcp.service.get_mcp_manager",
                return_value=mock_manager,
            ),
        ):
            await app(scope, receive, send)

        mock_manager.handle_request.assert_awaited_once_with(scope, receive, send)
