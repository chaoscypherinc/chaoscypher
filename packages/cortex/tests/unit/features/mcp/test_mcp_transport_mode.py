# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for MCP mode resolution in the Cortex transport.

Per-key MCP ceilings were removed when the auth.db API key store was
deleted (see ``api.py`` module docstring). The transport now stamps
``effective_mcp_mode`` on the ASGI scope using server-wide settings only,
so these tests pin two contracts:

* ``_compute_effective_mcp_mode`` still implements the read/write min
  helper (kept as a pure function for downstream use).
* ``MCPTransportApp.__call__`` writes ``effective_mcp_mode = server_mode``
  to ``scope["state"]`` and forwards to the manager.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_cortex.features.mcp.api import (
    MCPTransportApp,
    _compute_effective_mcp_mode,
)


class _Sender:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def __call__(self, message: dict) -> None:
        self.messages.append(message)


def _mk_scope() -> dict:
    return {
        "type": "http",
        "headers": [(b"x-auth-user", b"test-user")],
        "state": {},
    }


class TestComputeEffectiveMcpMode:
    """The pure helper still encodes ``min(server, key)`` semantics."""

    def test_none_key_defers_to_server(self) -> None:
        assert _compute_effective_mcp_mode("write", None) == "write"
        assert _compute_effective_mcp_mode("read", None) == "read"

    def test_read_anywhere_clamps_to_read(self) -> None:
        assert _compute_effective_mcp_mode("write", "read") == "read"
        assert _compute_effective_mcp_mode("read", "write") == "read"

    def test_write_on_both_stays_write(self) -> None:
        assert _compute_effective_mcp_mode("write", "write") == "write"


class TestEffectiveMcpModeOnScope:
    """``MCPTransportApp`` stamps ``effective_mcp_mode`` from server settings.

    Per-key MCP downgrade was removed; the transport hard-codes
    ``key_mode=None`` so the effective mode always equals the server
    setting.
    """

    @pytest.mark.asyncio
    async def test_write_server_yields_write_effective_mode(self) -> None:
        app = MCPTransportApp()
        settings = MagicMock()
        settings.dev_mode = True
        settings.mcp.mode = "write"
        scope = _mk_scope()

        async def fake_receive() -> dict:
            return {"type": "http.request", "body": b"", "more_body": False}

        send = _Sender()

        mock_manager = AsyncMock()

        with (
            patch(
                "chaoscypher_core.app_config.get_settings",
                return_value=settings,
            ),
            patch(
                "chaoscypher_cortex.features.mcp.service.get_mcp_manager",
                return_value=mock_manager,
            ),
        ):
            await app(scope, fake_receive, send)

        assert scope["state"].get("effective_mcp_mode") == "write"
        mock_manager.handle_request.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_read_server_yields_read_effective_mode(self) -> None:
        app = MCPTransportApp()
        settings = MagicMock()
        settings.dev_mode = True
        settings.mcp.mode = "read"
        scope = _mk_scope()

        async def fake_receive() -> dict:
            return {"type": "http.request", "body": b"", "more_body": False}

        send = _Sender()

        mock_manager = AsyncMock()

        with (
            patch(
                "chaoscypher_core.app_config.get_settings",
                return_value=settings,
            ),
            patch(
                "chaoscypher_cortex.features.mcp.service.get_mcp_manager",
                return_value=mock_manager,
            ),
        ):
            await app(scope, fake_receive, send)

        assert scope["state"].get("effective_mcp_mode") == "read"
        mock_manager.handle_request.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_default_mcp_mode_is_read(self) -> None:
        """A real ``MCPSettings()`` has ``mode = "read"`` by default."""
        from chaoscypher_core.settings import MCPSettings

        app = MCPTransportApp()
        settings = MagicMock()
        settings.dev_mode = True
        settings.mcp = MCPSettings()  # default mode="read"
        scope = _mk_scope()

        async def fake_receive() -> dict:
            return {"type": "http.request", "body": b"", "more_body": False}

        send = _Sender()

        mock_manager = AsyncMock()

        with (
            patch(
                "chaoscypher_core.app_config.get_settings",
                return_value=settings,
            ),
            patch(
                "chaoscypher_cortex.features.mcp.service.get_mcp_manager",
                return_value=mock_manager,
            ),
        ):
            await app(scope, fake_receive, send)

        assert scope["state"].get("effective_mcp_mode") == "read"
