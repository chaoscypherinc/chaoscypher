# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Global LLM Provider Factory - Singleton Pattern.

Provides a single global ProviderFactory instance that caches expensive
LLM provider instances (HTTP clients, connection pools).

The engine's ProviderFactory already has per-provider caching built-in.
This module ensures we reuse the same ProviderFactory instance across
all requests instead of creating new factories per request.

Architecture:
- chaoscypher.adapters.llm.factory.ProviderFactory: Has built-in provider caching
- This module: Provides singleton ProviderFactory instance for backend
- CLI/Workers: Can create their own ProviderFactory instances

Usage (Backend):
    from chaoscypher_core.llm_queue.factory import get_provider_factory

    factory = get_provider_factory()  # Returns singleton
    provider = factory.get_chat_provider()  # Cached

Usage (CLI):
    from chaoscypher_core.adapters.llm.factory import ProviderFactory

    factory = ProviderFactory(settings)  # Create your own
    provider = factory.get_chat_provider()
"""

from functools import lru_cache

import structlog

from chaoscypher_core.adapters.llm.factory import ProviderFactory
from chaoscypher_core.app_config import get_settings


logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def get_provider_factory() -> ProviderFactory:
    """Get global singleton ProviderFactory instance.

    The ProviderFactory itself has built-in caching of LLM providers,
    so reusing the same factory instance ensures providers are cached
    across all requests.

    Returns:
        Cached ProviderFactory instance

    Example:
        # In any backend API factory:
        from chaoscypher_core.llm_queue.factory import get_provider_factory

        factory = get_provider_factory()  # Singleton
        chat_provider = factory.get_chat_provider()  # Cached

    """
    from chaoscypher_core.app_config.engine_factory import build_engine_settings

    settings = get_settings()
    engine_settings = build_engine_settings(settings)
    factory = ProviderFactory(engine_settings)

    logger.info(
        "provider_factory_singleton_created",
        chat_provider=settings.llm.chat_provider,
    )

    return factory


def reload_provider_factory() -> None:
    """Clear the provider factory cache to force recreation with new settings.

    Call this after LLM settings are updated to ensure new settings take effect.
    """
    get_provider_factory.cache_clear()
    logger.info("provider_factory_cache_cleared")
