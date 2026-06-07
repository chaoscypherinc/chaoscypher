# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for GeminiEmbeddingProvider."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.adapters.embedding.gemini_provider import GeminiEmbeddingProvider
from chaoscypher_core.models import BatchEmbedResult, EmbedResult
from chaoscypher_core.ports.embedding import EmbeddingHealthStatus


class TestGeminiEmbeddingProvider:
    """Tests for GeminiEmbeddingProvider embed/batch_embed/health."""

    @pytest.fixture
    def provider(self) -> GeminiEmbeddingProvider:
        """Create GeminiEmbeddingProvider with test config."""
        return GeminiEmbeddingProvider(
            model_name="gemini-embedding-001",
            vector_dimensions=4,
            api_key="test-api-key",
            api_base="https://generativelanguage.googleapis.com",
        )

    def test_provider_type_is_gemini(self, provider: GeminiEmbeddingProvider) -> None:
        """provider_type property returns 'gemini'."""
        assert provider.provider_type == "gemini"

    @pytest.mark.asyncio
    async def test_embed_returns_embed_result(self, provider: GeminiEmbeddingProvider) -> None:
        """embed() returns EmbedResult with embedding vector and provider='gemini'."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "embedding": {"values": [0.1, 0.2, 0.3, 0.4]},
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "chaoscypher_core.adapters.embedding.gemini_provider.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await provider.embed("test text")

        assert isinstance(result, EmbedResult)
        assert len(result.embedding) == 4
        assert result.embedding == [0.1, 0.2, 0.3, 0.4]
        assert result.provider == "gemini"

    @pytest.mark.asyncio
    async def test_batch_embed_returns_batch_result(
        self, provider: GeminiEmbeddingProvider
    ) -> None:
        """batch_embed() returns BatchEmbedResult with correct vectors."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "embeddings": [
                {"values": [0.1, 0.2, 0.3, 0.4]},
                {"values": [0.5, 0.6, 0.7, 0.8]},
            ],
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "chaoscypher_core.adapters.embedding.gemini_provider.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await provider.batch_embed(["text 1", "text 2"])

        assert isinstance(result, BatchEmbedResult)
        assert result.total == 2
        assert result.failed == 0
        assert len(result.embeddings) == 2
        assert result.embeddings[0] == [0.1, 0.2, 0.3, 0.4]
        assert result.embeddings[1] == [0.5, 0.6, 0.7, 0.8]
        assert result.provider == "gemini"

    @pytest.mark.asyncio
    async def test_check_health_healthy(self, provider: GeminiEmbeddingProvider) -> None:
        """check_health() returns healthy status when Gemini responds."""
        # check_health() probes GET /v1beta/models and verifies the model is listed.
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"models": [{"name": "models/gemini-embedding-001"}]}

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "chaoscypher_core.adapters.embedding.gemini_provider.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await provider.check_health()

        assert isinstance(result, EmbeddingHealthStatus)
        assert result.healthy is True
        assert result.provider == "gemini"
        assert result.model == "gemini-embedding-001"
        assert result.response_time_ms is not None

    @pytest.mark.asyncio
    async def test_check_health_auth_error(self, provider: GeminiEmbeddingProvider) -> None:
        """check_health() returns unhealthy status on 401 auth error."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "API key not valid"

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "chaoscypher_core.adapters.embedding.gemini_provider.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await provider.check_health()

        assert isinstance(result, EmbeddingHealthStatus)
        assert result.healthy is False
        assert result.provider == "gemini"
        assert result.model == "gemini-embedding-001"
        assert result.message is not None
        assert "authentication" in result.message.lower() or "401" in result.message

    @pytest.mark.asyncio
    async def test_embed_sends_output_dimensionality(
        self, provider: GeminiEmbeddingProvider
    ) -> None:
        """embed() includes outputDimensionality parameter in the API request."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "embedding": {"values": [0.1, 0.2, 0.3, 0.4]},
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "chaoscypher_core.adapters.embedding.gemini_provider.httpx.AsyncClient",
            return_value=mock_client,
        ):
            await provider.embed("test text")

        # Verify outputDimensionality was sent in the request payload
        call_kwargs = mock_client.post.call_args
        sent_payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert sent_payload["outputDimensionality"] == 4
        assert sent_payload["model"] == "models/gemini-embedding-001"
