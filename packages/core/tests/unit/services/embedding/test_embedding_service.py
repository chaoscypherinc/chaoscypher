# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for LocalEmbeddingProvider."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch


if TYPE_CHECKING:
    from pathlib import Path

import pytest

from chaoscypher_core.models import BatchEmbedResult, EmbedResult


class TestEmbeddingService:
    """Tests for LocalEmbeddingProvider embed/batch_embed."""

    @pytest.fixture
    def service(self, tmp_path: Path):
        """Create LocalEmbeddingProvider with test config."""
        from chaoscypher_core.adapters.embedding.local_provider import LocalEmbeddingProvider

        return LocalEmbeddingProvider(
            model_name="test-model",
            vector_dimensions=4,
            cache_dir=tmp_path / "models",
        )

    @pytest.mark.asyncio
    async def test_embed_returns_embed_result(self, service):
        """embed() returns EmbedResult with truncated vector."""
        fake_vector = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]

        mock_model = MagicMock()
        mock_model.encode.return_value = fake_vector

        with patch.object(service, "_model", mock_model):
            service._model_loaded = True
            result = await service.embed("test text")

        assert isinstance(result, EmbedResult)
        assert len(result.embedding) == 4  # Truncated to vector_dimensions
        assert result.embedding == [0.1, 0.2, 0.3, 0.4]
        assert result.provider == "local"

    @pytest.mark.asyncio
    async def test_batch_embed_returns_batch_result(self, service):
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

        with patch.object(service, "_model", mock_model):
            service._model_loaded = True
            result = await service.batch_embed(["text 1", "text 2"])

        assert isinstance(result, BatchEmbedResult)
        assert result.total == 2
        assert result.failed == 0
        assert len(result.embeddings) == 2
        assert len(result.embeddings[0]) == 4  # Truncated

    @pytest.mark.asyncio
    async def test_embed_lazy_loads_model(self, service):
        """First embed() call triggers model loading."""
        assert service._model is None
        assert not service._model_loaded

        fake_vector = [0.1, 0.2, 0.3, 0.4, 0.5]
        mock_model = MagicMock()
        mock_model.encode.return_value = fake_vector

        with patch(
            "sentence_transformers.SentenceTransformer",
            return_value=mock_model,
        ):
            result = await service.embed("test")

        assert service._model_loaded
        assert isinstance(result, EmbedResult)

    @pytest.mark.asyncio
    async def test_check_health_returns_healthy(self, service):
        """check_health() returns EmbeddingHealthStatus."""
        from chaoscypher_core.ports.embedding import EmbeddingHealthStatus

        fake_vector = [0.1, 0.2, 0.3, 0.4]

        mock_model = MagicMock()
        mock_model.encode.return_value = fake_vector

        with patch.object(service, "_model", mock_model):
            service._model_loaded = True
            result = await service.check_health()

        assert isinstance(result, EmbeddingHealthStatus)
        assert result.healthy is True
        assert result.provider == "local"
        assert result.model == "test-model"
        assert result.dimensions == 4
