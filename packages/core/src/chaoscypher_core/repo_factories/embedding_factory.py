# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Global Embedding Provider Factory - Singleton Pattern.

Provides a single global EmbeddingProviderProtocol instance that is
created once and reused for all embedding requests.

Architecture:
- One provider instance per process (cached)
- Provider created on first call via create_embedding_provider factory
- Supports local, Ollama, OpenAI, and Gemini backends

Usage (Backend):
    from chaoscypher_core.repo_factories import get_embedding_service

    embedding_service = get_embedding_service()
    result = await embedding_service.embed("some text")
    embedding = result.embedding  # list[float]
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from chaoscypher_core.adapters.embedding import create_embedding_provider


if TYPE_CHECKING:
    from chaoscypher_core.ports.embedding import EmbeddingProviderProtocol


logger = structlog.get_logger(__name__)

_embedding_service: EmbeddingProviderProtocol | None = None


def get_embedding_service() -> EmbeddingProviderProtocol:
    """Get the singleton embedding provider instance.

    Creates the provider on first call using settings from
    the global configuration. The provider is created once and reused.

    Returns:
        Cached EmbeddingProviderProtocol instance

    Example:
        from chaoscypher_core.repo_factories import get_embedding_service

        service = get_embedding_service()
        result = await service.embed("text to embed")
        embedding = result.embedding  # list[float]

    """
    global _embedding_service
    if _embedding_service is None:
        from chaoscypher_core.app_config import get_settings
        from chaoscypher_core.app_config.engine_factory import (
            build_engine_settings,
        )

        settings = get_settings()
        engine_settings = build_engine_settings(settings)
        _embedding_service = create_embedding_provider(engine_settings)
        logger.info(
            "embedding_provider_singleton_created",
            provider=engine_settings.embedding.provider,
            model=engine_settings.embedding.model,
        )
    return _embedding_service


def invalidate_embedding_service() -> None:
    """Clear the cached embedding provider singleton.

    Call after embedding settings change so the next request
    creates a fresh provider with current settings.
    """
    global _embedding_service
    _embedding_service = None
    logger.info("embedding_provider_cache_invalidated")
