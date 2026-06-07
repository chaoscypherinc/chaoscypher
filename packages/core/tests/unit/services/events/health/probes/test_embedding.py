# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for EmbeddingProbe."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from chaoscypher_core.services.events.health.probes.embedding import EmbeddingProbe


def _health(**kwargs: object) -> SimpleNamespace:
    """Build a fake EmbeddingHealthStatus-shaped object for the probe."""
    defaults = {
        "healthy": True,
        "provider": "ollama",
        "model": "nomic-embed-text",
        "dimensions": 768,
        "response_time_ms": 5,
        "message": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestEmbeddingProbe:
    """EmbeddingProbe wraps the provider health result into a ProbeResult."""

    @pytest.mark.asyncio
    async def test_ok_message_when_healthy(self) -> None:
        async def fn() -> SimpleNamespace:
            return _health()

        result = await EmbeddingProbe(fn).check()

        assert result.status == "ok"
        assert result.message == "nomic-embed-text ready (ollama)"

    @pytest.mark.asyncio
    async def test_role_prefixed_message_when_unhealthy(self) -> None:
        async def fn() -> SimpleNamespace:
            return _health(
                healthy=False,
                provider="ollama",
                message="Not reachable at http://localhost:11434",
            )

        result = await EmbeddingProbe(fn).check()

        assert result.status == "error"
        assert result.message == "Embedding Provider Ollama Offline"
        assert result.details is not None
        assert result.details["tooltip"] == "Not reachable at http://localhost:11434"

    @pytest.mark.asyncio
    async def test_display_name_for_known_providers(self) -> None:
        async def fn() -> SimpleNamespace:
            return _health(healthy=False, provider="openai", message="boom")

        result = await EmbeddingProbe(fn).check()

        assert result.message == "Embedding Provider OpenAI Offline"

    @pytest.mark.asyncio
    async def test_unknown_provider_is_capitalized(self) -> None:
        async def fn() -> SimpleNamespace:
            return _health(healthy=False, provider="something", message="x")

        result = await EmbeddingProbe(fn).check()

        assert result.message == "Embedding Provider Something Offline"

    @pytest.mark.asyncio
    async def test_unhealthy_without_message_omits_tooltip(self) -> None:
        async def fn() -> SimpleNamespace:
            return _health(healthy=False, message=None)

        result = await EmbeddingProbe(fn).check()

        assert result.details is not None
        assert "tooltip" not in result.details
