# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LLM Infrastructure Module - Backend Queue Wrapper.

This module wraps the standalone chaoscypher.llm module with queue coordination
for Docker/Valkey environments.

Architecture:
- chaoscypher.llm: Standalone LLM core (providers, factory, semaphore, cost tracking)
- backend.shared.llm: Queue wrappers for web API and workers

Main Components:
- LLMQueueService: Queue coordination service for LLM operations
- TaskType: Task type enum (chat, embedding, tool)
- get_llm_semaphore: Priority semaphore factory (from chaoscypher)

All core LLM functionality is imported from chaoscypher_core.adapters.llm to avoid duplication.
"""

# Import core LLM infrastructure from chaoscypher (single source of truth)
from chaoscypher_core.adapters.llm import (
    LLMProvider,
    PrioritySemaphore,
    ProviderFactory,
    get_llm_semaphore,
    get_provider,
    list_available_providers,
)

# Import singleton factories
from chaoscypher_core.llm_queue.factory import get_provider_factory
from chaoscypher_core.llm_queue.provider_utils import ProviderConfig, get_provider_config
from chaoscypher_core.llm_queue.queue_factory import get_llm_queue_service
from chaoscypher_core.llm_queue.queue_service import LLMQueueService

# Import backend-specific queue wrappers
from chaoscypher_core.ports.llm import TaskType


__all__ = [
    # Core LLM (from chaoscypher)
    "LLMProvider",
    # Backend queue wrappers
    "LLMQueueService",
    "PrioritySemaphore",
    "ProviderConfig",
    "ProviderFactory",
    "TaskType",
    "get_llm_queue_service",
    "get_llm_semaphore",
    "get_provider",
    # Provider configuration utilities
    "get_provider_config",
    # Singleton factories
    "get_provider_factory",
    "list_available_providers",
]
