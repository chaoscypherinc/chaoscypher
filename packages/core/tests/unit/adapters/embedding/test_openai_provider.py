# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for OpenAIEmbeddingProvider."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.adapters.embedding.openai_provider import OpenAIEmbeddingProvider
from chaoscypher_core.models import BatchEmbedResult, EmbedResult
from chaoscypher_core.ports.embedding import EmbeddingHealthStatus


class TestOpenAIEmbeddingProvider:
    """Tests for OpenAIEmbeddingProvider embed/batch_embed/health."""

    @pytest.fixture
    def provider(self) -> OpenAIEmbeddingProvider:
        """Create OpenAIEmbeddingProvider with test config."""
        return OpenAIEmbeddingProvider(
            model_name="text-embedding-3-large",
            vector_dimensions=4,
            api_key="sk-test-key",
            api_base="https://api.openai.com",
        )

    def test_provider_type_is_openai(self, provider: OpenAIEmbeddingProvider) -> None:
        """provider_type property returns 'openai'."""
        assert provider.provider_type == "openai"

    @pytest.mark.asyncio
    async def test_embed_returns_embed_result(self, provider: OpenAIEmbeddingProvider) -> None:
        """embed() returns EmbedResult with embedding vector and provider='openai'."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {"embedding": [0.1, 0.2, 0.3, 0.4], "index": 0},
            ],
            "model": "text-embedding-3-large",
            "usage": {"prompt_tokens": 3, "total_tokens": 3},
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "chaoscypher_core.adapters.embedding.openai_provider.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await provider.embed("test text")

        assert isinstance(result, EmbedResult)
        assert len(result.embedding) == 4
        assert result.embedding == [0.1, 0.2, 0.3, 0.4]
        assert result.provider == "openai"
        assert result.usage is not None
        assert result.usage.input_tokens == 3
        assert result.usage.total_tokens == 3

    @pytest.mark.asyncio
    async def test_batch_embed_returns_batch_result(
        self, provider: OpenAIEmbeddingProvider
    ) -> None:
        """batch_embed() returns BatchEmbedResult with correct vectors."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {"embedding": [0.1, 0.2, 0.3, 0.4], "index": 0},
                {"embedding": [0.5, 0.6, 0.7, 0.8], "index": 1},
            ],
            "model": "text-embedding-3-large",
            "usage": {"prompt_tokens": 6, "total_tokens": 6},
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "chaoscypher_core.adapters.embedding.openai_provider.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await provider.batch_embed(["text 1", "text 2"])

        assert isinstance(result, BatchEmbedResult)
        assert result.total == 2
        assert result.failed == 0
        assert len(result.embeddings) == 2
        assert result.embeddings[0] == [0.1, 0.2, 0.3, 0.4]
        assert result.embeddings[1] == [0.5, 0.6, 0.7, 0.8]
        assert result.provider == "openai"

    @pytest.mark.asyncio
    async def test_check_health_healthy(self, provider: OpenAIEmbeddingProvider) -> None:
        """check_health() returns healthy status when OpenAI responds."""
        # check_health() probes GET /v1/models and verifies the model is listed.
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": [{"id": "text-embedding-3-large"}]}

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "chaoscypher_core.adapters.embedding.openai_provider.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await provider.check_health()

        assert isinstance(result, EmbeddingHealthStatus)
        assert result.healthy is True
        assert result.provider == "openai"
        assert result.model == "text-embedding-3-large"
        assert result.response_time_ms is not None

    @pytest.mark.asyncio
    async def test_check_health_auth_error(self, provider: OpenAIEmbeddingProvider) -> None:
        """check_health() returns unhealthy status on 401 auth error."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Invalid API key"

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "chaoscypher_core.adapters.embedding.openai_provider.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await provider.check_health()

        assert isinstance(result, EmbeddingHealthStatus)
        assert result.healthy is False
        assert result.provider == "openai"
        assert result.model == "text-embedding-3-large"
        assert result.message is not None
        assert "authentication" in result.message.lower() or "401" in result.message

    @pytest.mark.asyncio
    async def test_embed_sends_dimensions_param(self, provider: OpenAIEmbeddingProvider) -> None:
        """embed() includes dimensions parameter in the API request."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {"embedding": [0.1, 0.2, 0.3, 0.4], "index": 0},
            ],
            "model": "text-embedding-3-large",
            "usage": {"prompt_tokens": 2, "total_tokens": 2},
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "chaoscypher_core.adapters.embedding.openai_provider.httpx.AsyncClient",
            return_value=mock_client,
        ):
            await provider.embed("test text")

        # Verify dimensions was sent in the request payload
        call_kwargs = mock_client.post.call_args
        sent_payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert sent_payload["dimensions"] == 4
        assert sent_payload["model"] == "text-embedding-3-large"
