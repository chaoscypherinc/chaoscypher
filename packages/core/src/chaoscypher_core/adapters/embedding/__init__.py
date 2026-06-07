# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Embedding Provider Adapters for ChaosCypher Knowledge Engine.

Provides multi-provider embedding support with a unified interface.
Concrete providers implement EmbeddingProviderProtocol for different backends.

Providers:
    - LocalEmbeddingProvider: Local CPU embedding via sentence-transformers
    - OllamaEmbeddingProvider: GPU-accelerated embedding via Ollama HTTP API
    - OpenAIEmbeddingProvider: Cloud embedding via OpenAI Embeddings API
    - GeminiEmbeddingProvider: Cloud embedding via Google Gemini API

Models:
    - EmbeddingHealthStatus: Provider health check result
    - ModelValidationResult: Model availability validation
    - CuratedModel: Local model definition with known characteristics
    - CloudModel: Cloud provider model definition

Factory:
    - create_embedding_provider: Build a provider from EngineSettings
    - validate_embedding_model: Check model validity against curated registry

Usage:
    from chaoscypher_core.adapters.embedding import create_embedding_provider
    from chaoscypher_core.adapters.embedding import LocalEmbeddingProvider
    from chaoscypher_core.adapters.embedding import OllamaEmbeddingProvider
    from chaoscypher_core.adapters.embedding import OpenAIEmbeddingProvider
    from chaoscypher_core.adapters.embedding import GeminiEmbeddingProvider

"""

from chaoscypher_core.adapters.embedding.factory import (
    create_embedding_provider,
    validate_embedding_model,
)
from chaoscypher_core.adapters.embedding.gemini_provider import GeminiEmbeddingProvider
from chaoscypher_core.adapters.embedding.local_provider import LocalEmbeddingProvider
from chaoscypher_core.adapters.embedding.models import (
    CloudModel,
    CuratedModel,
    ModelValidationResult,
)
from chaoscypher_core.adapters.embedding.ollama_provider import OllamaEmbeddingProvider
from chaoscypher_core.adapters.embedding.openai_provider import OpenAIEmbeddingProvider
from chaoscypher_core.ports.embedding import EmbeddingHealthStatus


__all__ = [
    "CloudModel",
    "CuratedModel",
    "EmbeddingHealthStatus",
    "GeminiEmbeddingProvider",
    "LocalEmbeddingProvider",
    "ModelValidationResult",
    "OllamaEmbeddingProvider",
    "OpenAIEmbeddingProvider",
    "create_embedding_provider",
    "validate_embedding_model",
]
