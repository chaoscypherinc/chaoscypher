# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LLM Feature.

LLM provider monitoring, health checks, and queue statistics.

This feature provides visibility into LLM provider health, queue statistics,
and chat/embedding performance. Monitors the dedicated llm_worker queue (1
concurrent) for interactive chat and background embedding tasks. Enables health
checks, provider switching, and performance diagnostics. Critical for debugging
LLM integration issues and monitoring token usage.

Components:
- LLMService: Provider health checks and queue statistics
- LLMStatsResponse: Provider status and performance metrics DTO
- router: FastAPI endpoints for /api/v1/llm

Architecture:
Integrates with backend.shared.llm.factory for provider health and queue service
for statistics. No repository layer needed. Service layer aggregates provider
status, queue depth, and recent job history. Factory function provides dependencies.

Example:
    from chaoscypher_cortex.features.llm import LLMService

    # Check LLM health and queue
    service = LLMService(provider_factory, queue_service)
    stats = await service.get_llm_stats()
    is_healthy = await service.check_provider_health("ollama")

"""

from chaoscypher_cortex.features.llm.api import router
from chaoscypher_cortex.features.llm.models import LLMStatsResponse
from chaoscypher_cortex.features.llm.service import LLMService


__all__ = ["LLMService", "LLMStatsResponse", "router"]
