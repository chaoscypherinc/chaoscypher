# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Global LLM Queue Service Factory - Singleton Pattern.

Provides a single global LLMQueueService instance that wraps the cached
LLM provider. This prevents creating new queue service instances on every
API request.

Architecture:
- One LLMQueueService instance per process (cached)
- Wraps the singleton ProviderFactory's cached provider
- Manages LLM request queuing and prioritization

Usage (Backend):
    from chaoscypher_core.llm_queue.queue_factory import get_llm_queue_service

    llm_service = get_llm_queue_service()  # Returns singleton
    # Use for queueing LLM requests with priority management

Note: This is critical for preventing repeated initialization overhead
when multiple API endpoints need LLM queue capabilities.
"""

from functools import lru_cache

import structlog

from chaoscypher_core.app_config import get_settings
from chaoscypher_core.llm_queue.factory import get_provider_factory
from chaoscypher_core.llm_queue.queue_service import LLMQueueService


logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def get_llm_queue_service() -> LLMQueueService:
    """Get singleton LLMQueueService instance.

    Creates one LLMQueueService per process that wraps the singleton
    ProviderFactory's cached LLM provider. Manages request queuing
    and prioritization.

    Returns:
        Cached LLMQueueService instance (wraps cached provider)

    Example:
        # In any backend API factory:
        from chaoscypher_core.llm_queue.queue_factory import get_llm_queue_service

        llm_service = get_llm_queue_service()  # Singleton
        # llm_service is already initialized, ready to queue requests

    Important:
        The underlying provider is also a singleton (from get_provider_factory),
        so this creates an efficient two-level caching structure:
        1. ProviderFactory (caches HTTP clients and connection pools)
        2. LLMQueueService (caches queue management and Valkey connections)

    """
    settings = get_settings()

    # Get singleton factory (cached)
    factory = get_provider_factory()

    # Get cached chat provider from factory
    provider = factory.get_chat_provider()

    # Create LLMQueueService wrapper (cached at this level)
    # Type note: factory returns providers.base.LLMProvider, queue service expects provider.LLMProvider
    # Both are valid LLM providers with compatible interfaces
    llm_service = LLMQueueService(provider=provider, settings=settings)  # type: ignore[arg-type]

    logger.info(
        "llm_queue_service_singleton_created",
        chat_provider=settings.llm.chat_provider,
    )

    return llm_service


def reload_llm_queue_service() -> None:
    """Clear the LLM queue service cache to force recreation with new settings.

    Call this after LLM settings are updated to ensure new settings take effect.
    Clears caches in dependency order:
    1. Settings cache (so fresh settings.yaml values are loaded)
    2. Provider factory cache (so new factory gets fresh settings)
    3. Queue service cache (so new service gets fresh factory)
    """
    from chaoscypher_core.app_config import reload_settings
    from chaoscypher_core.llm_queue.factory import reload_provider_factory

    # Clear settings cache first (so fresh values are loaded from settings.yaml)
    reload_settings()

    # Clear provider factory (depends on settings)
    reload_provider_factory()

    # Clear queue service cache (depends on factory)
    get_llm_queue_service.cache_clear()
    logger.info("llm_queue_service_cache_cleared")
