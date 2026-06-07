# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for LocalEmbeddingProvider.from_settings() and create_embedding_provider()."""

import pytest

from chaoscypher_core.adapters.embedding import create_embedding_provider
from chaoscypher_core.adapters.embedding.local_provider import LocalEmbeddingProvider
from chaoscypher_core.settings import EngineSettings


@pytest.mark.unit
@pytest.mark.core
class TestEmbeddingServiceFactory:
    """Tests for from_settings class method and factory function."""

    def test_from_settings_creates_service(self):
        """from_settings() should create a properly configured service."""
        settings = EngineSettings()
        service = LocalEmbeddingProvider.from_settings(settings)
        assert isinstance(service, LocalEmbeddingProvider)
        assert service.model_name == settings.embedding.model
        assert service.vector_dimensions == settings.search.vector_dimensions

    def test_from_settings_uses_custom_model(self):
        """from_settings() should respect custom embedding settings."""
        settings = EngineSettings(
            embedding={"model": "custom/model"},
            search={"vector_dimensions": 512},
        )
        service = LocalEmbeddingProvider.from_settings(settings)
        assert service.model_name == "custom/model"
        assert service.vector_dimensions == 512

    def test_create_embedding_provider_returns_local_by_default(self):
        """create_embedding_provider() should return LocalEmbeddingProvider with default settings."""
        settings = EngineSettings()
        provider = create_embedding_provider(settings)
        assert isinstance(provider, LocalEmbeddingProvider)
        assert provider.model_name == settings.embedding.model
