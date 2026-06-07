# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for OllamaEmbeddingProvider."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from chaoscypher_core.adapters.embedding.ollama_provider import OllamaEmbeddingProvider
from chaoscypher_core.exceptions import LLMError
from chaoscypher_core.models import BatchEmbedResult, EmbedResult
from chaoscypher_core.ports.embedding import EmbeddingHealthStatus


class TestOllamaEmbeddingProvider:
    """Tests for OllamaEmbeddingProvider embed/batch_embed/health."""

    @pytest.fixture
    def provider(self) -> OllamaEmbeddingProvider:
        """Create OllamaEmbeddingProvider with test config."""
        return OllamaEmbeddingProvider(
            model_name="test-model",
            vector_dimensions=4,
            base_url="http://localhost:11434",
        )

    def test_provider_type_is_ollama(self, provider: OllamaEmbeddingProvider) -> None:
        """provider_type property returns 'ollama'."""
        assert provider.provider_type == "ollama"

    @pytest.mark.asyncio
    async def test_embed_returns_embed_result(self, provider: OllamaEmbeddingProvider) -> None:
        """embed() returns EmbedResult with truncated vector and provider='ollama'."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "model": "test-model",
            "embeddings": [[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]],
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "chaoscypher_core.adapters.embedding.ollama_provider.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await provider.embed("test text")

        assert isinstance(result, EmbedResult)
        assert len(result.embedding) == 4
        assert result.embedding == [0.1, 0.2, 0.3, 0.4]
        assert result.provider == "ollama"

    @pytest.mark.asyncio
    async def test_batch_embed_returns_batch_result(
        self, provider: OllamaEmbeddingProvider
    ) -> None:
        """batch_embed() returns BatchEmbedResult with truncated vectors."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "model": "test-model",
            "embeddings": [
                [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
                [0.7, 0.8, 0.9, 1.0, 1.1, 1.2],
            ],
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "chaoscypher_core.adapters.embedding.ollama_provider.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await provider.batch_embed(["text 1", "text 2"])

        assert isinstance(result, BatchEmbedResult)
        assert result.total == 2
        assert result.failed == 0
        assert len(result.embeddings) == 2
        assert len(result.embeddings[0]) == 4
        assert result.embeddings[0] == [0.1, 0.2, 0.3, 0.4]
        assert result.provider == "ollama"

    @pytest.mark.asyncio
    async def test_embed_truncates_to_dimensions(self, provider: OllamaEmbeddingProvider) -> None:
        """embed() truncates vectors to vector_dimensions via MRL."""
        long_vector = list(range(100))
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "model": "test-model",
            "embeddings": [long_vector],
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "chaoscypher_core.adapters.embedding.ollama_provider.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await provider.embed("test")

        assert len(result.embedding) == 4
        assert result.embedding == [0.0, 1.0, 2.0, 3.0]

    @pytest.mark.asyncio
    async def test_check_health_healthy(self, provider: OllamaEmbeddingProvider) -> None:
        """check_health() returns healthy status when Ollama responds."""
        # check_health() probes GET /api/tags and verifies the model is listed.
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"models": [{"name": "test-model"}]}

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "chaoscypher_core.adapters.embedding.ollama_provider.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await provider.check_health()

        assert isinstance(result, EmbeddingHealthStatus)
        assert result.healthy is True
        assert result.provider == "ollama"
        assert result.model == "test-model"
        assert result.response_time_ms is not None

    @pytest.mark.asyncio
    async def test_check_health_connection_refused(self, provider: OllamaEmbeddingProvider) -> None:
        """check_health() returns unhealthy status with the base URL when unreachable."""
        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.ConnectError("Connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "chaoscypher_core.adapters.embedding.ollama_provider.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await provider.check_health()

        assert isinstance(result, EmbeddingHealthStatus)
        assert result.healthy is False
        assert result.provider == "ollama"
        assert result.model == "test-model"
        assert result.message == f"Not reachable at {provider.base_url}"

    @pytest.mark.asyncio
    async def test_check_health_timeout(self, provider: OllamaEmbeddingProvider) -> None:
        """check_health() returns unhealthy status with the base URL when timing out."""
        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.ConnectTimeout("timed out")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "chaoscypher_core.adapters.embedding.ollama_provider.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await provider.check_health()

        assert isinstance(result, EmbeddingHealthStatus)
        assert result.healthy is False
        assert result.message == f"Timed out at {provider.base_url}"

    @pytest.mark.asyncio
    async def test_embed_raises_llm_error_on_client_error(
        self, provider: OllamaEmbeddingProvider
    ) -> None:
        """embed() raises LLMError on 4xx responses."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "model not found"

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "chaoscypher_core.adapters.embedding.ollama_provider.httpx.AsyncClient",
                return_value=mock_client,
            ),
            pytest.raises(LLMError, match="404"),
        ):
            await provider.embed("test")

    @pytest.mark.asyncio
    async def test_batch_embed_chunks_large_batches(
        self, provider: OllamaEmbeddingProvider
    ) -> None:
        """batch_embed() splits texts into batch_size chunks."""
        texts = [f"text {i}" for i in range(5)]

        # First call returns 2 embeddings, second returns 2, third returns 1
        responses = []
        for count in [2, 2, 1]:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "model": "test-model",
                "embeddings": [[0.1, 0.2, 0.3, 0.4, 0.5]] * count,
            }
            responses.append(mock_response)

        mock_client = AsyncMock()
        mock_client.post.side_effect = responses
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "chaoscypher_core.adapters.embedding.ollama_provider.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await provider.batch_embed(texts, batch_size=2)

        assert result.total == 5
        assert len(result.embeddings) == 5
        assert mock_client.post.call_count == 3

    @pytest.mark.asyncio
    async def test_embed_retries_on_server_error(self, provider: OllamaEmbeddingProvider) -> None:
        """embed() retries on 5xx server errors with backoff."""
        error_response = MagicMock()
        error_response.status_code = 500
        error_response.text = "Internal Server Error"

        success_response = MagicMock()
        success_response.status_code = 200
        success_response.json.return_value = {
            "model": "test-model",
            "embeddings": [[0.1, 0.2, 0.3, 0.4]],
        }

        mock_client = AsyncMock()
        mock_client.post.side_effect = [error_response, success_response]
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "chaoscypher_core.adapters.embedding.ollama_provider.httpx.AsyncClient",
                return_value=mock_client,
            ),
            patch(
                # Retries live in the shared _retry helper; that's where the
                # sleep call actually happens. Patching the provider module's
                # asyncio (which it doesn't import) is dead from a pre-refactor
                # layout.
                "chaoscypher_core.adapters.embedding._retry.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            result = await provider.embed("test")

        assert isinstance(result, EmbedResult)
        assert result.embedding == [0.1, 0.2, 0.3, 0.4]
        assert mock_client.post.call_count == 2
