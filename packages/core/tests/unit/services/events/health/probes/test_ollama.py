# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for OllamaProbe failure messaging."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from chaoscypher_core.services.events.health.probes.ollama import OllamaProbe


class TestOllamaProbeFailureMessages:
    """Failure-path messages must be role-prefixed and put the URL in details.tooltip."""

    @pytest.mark.asyncio
    async def test_connect_error_yields_offline_message_and_tooltip(self) -> None:
        probe = OllamaProbe(base_url="http://localhost:11434", timeout=1.0)

        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.ConnectError("refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "chaoscypher_core.services.events.health.probes.ollama.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await probe.check()

        assert result.status == "error"
        assert result.message == "Chat Provider Ollama Offline"
        assert result.details is not None
        assert result.details["tooltip"] == "Not reachable at http://localhost:11434"

    @pytest.mark.asyncio
    async def test_timeout_yields_unresponsive_message_and_tooltip(self) -> None:
        probe = OllamaProbe(base_url="http://localhost:11434", timeout=1.0)

        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.ConnectTimeout("slow")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "chaoscypher_core.services.events.health.probes.ollama.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await probe.check()

        assert result.status == "error"
        assert result.message == "Chat Provider Ollama Unresponsive"
        assert result.details is not None
        assert result.details["tooltip"] == "Timed out at http://localhost:11434"
