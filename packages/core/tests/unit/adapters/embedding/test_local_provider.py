# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for LocalEmbeddingProvider."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch


if TYPE_CHECKING:
    from pathlib import Path

import pytest

from chaoscypher_core.adapters.embedding.local_provider import LocalEmbeddingProvider
from chaoscypher_core.models import BatchEmbedResult, EmbedResult
from chaoscypher_core.ports.embedding import EmbeddingHealthStatus


class TestLocalEmbeddingProvider:
    """Tests for LocalEmbeddingProvider embed/batch_embed/health."""

    @pytest.fixture
    def provider(self, tmp_path: Path) -> LocalEmbeddingProvider:
        """Create LocalEmbeddingProvider with test config."""
        return LocalEmbeddingProvider(
            model_name="test-model",
            vector_dimensions=4,
            cache_dir=tmp_path / "models",
        )

    @pytest.mark.asyncio
    async def test_embed_returns_embed_result(self, provider: LocalEmbeddingProvider) -> None:
        """embed() returns EmbedResult with truncated vector and provider='local'."""
        fake_vector = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]

        mock_model = MagicMock()
        mock_model.encode.return_value = fake_vector

        with patch.object(provider, "_model", mock_model):
            provider._model_loaded = True
            result = await provider.embed("test text")

        assert isinstance(result, EmbedResult)
        assert len(result.embedding) == 4  # Truncated to vector_dimensions
        assert result.embedding == [0.1, 0.2, 0.3, 0.4]
        assert result.provider == "local"

    @pytest.mark.asyncio
    async def test_batch_embed_returns_batch_result(self, provider: LocalEmbeddingProvider) -> None:
        """batch_embed() returns BatchEmbedResult with truncated vectors."""
        import numpy as np

        fake_vectors = np.array(
            [
                [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
                [0.7, 0.8, 0.9, 1.0, 1.1, 1.2],
            ]
        )

        mock_model = MagicMock()
        mock_model.encode.return_value = fake_vectors

        with patch.object(provider, "_model", mock_model):
            provider._model_loaded = True
            result = await provider.batch_embed(["text 1", "text 2"])

        assert isinstance(result, BatchEmbedResult)
        assert result.total == 2
        assert result.failed == 0
        assert len(result.embeddings) == 2
        assert len(result.embeddings[0]) == 4  # Truncated
        assert result.provider == "local"

    @pytest.mark.asyncio
    async def test_embed_lazy_loads_model(self, provider: LocalEmbeddingProvider) -> None:
        """First embed() call triggers model loading."""
        assert provider._model is None
        assert not provider._model_loaded

        fake_vector = [0.1, 0.2, 0.3, 0.4, 0.5]
        mock_model = MagicMock()
        mock_model.encode.return_value = fake_vector

        with patch(
            "sentence_transformers.SentenceTransformer",
            return_value=mock_model,
        ):
            result = await provider.embed("test")

        assert provider._model_loaded
        assert isinstance(result, EmbedResult)

    @pytest.mark.asyncio
    async def test_check_health_returns_health_status(
        self, provider: LocalEmbeddingProvider
    ) -> None:
        """check_health() returns EmbeddingHealthStatus (not dict)."""
        fake_vector = [0.1, 0.2, 0.3, 0.4]

        mock_model = MagicMock()
        mock_model.encode.return_value = fake_vector

        with patch.object(provider, "_model", mock_model):
            provider._model_loaded = True
            result = await provider.check_health()

        assert isinstance(result, EmbeddingHealthStatus)
        assert result.healthy is True
        assert result.provider == "local"
        assert result.model == "test-model"
        assert result.dimensions == 4
        assert result.response_time_ms is not None

    def test_provider_type_is_local(self, provider: LocalEmbeddingProvider) -> None:
        """provider_type property returns 'local'."""
        assert provider.provider_type == "local"
