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
- LLMService: Queue statistics, task listing/cancellation, semaphore reset
- LLMStatsResponse: Provider status and performance metrics DTO
- router: FastAPI endpoints for /api/v1/llm

Architecture:
Wraps the LLM queue service singleton from chaoscypher_core.llm_queue. No
repository layer needed. Service layer aggregates queue stats, current tasks,
and cancellation. (Provider health lives in the settings feature at
GET /api/v1/settings/llm/health.)

Example:
    from chaoscypher_cortex.features.llm import LLMService
    from chaoscypher_core.llm_queue import get_llm_queue_service

    service = LLMService(get_llm_queue_service())
    stats = await service.get_stats()

"""

from chaoscypher_cortex.features.llm.api import router
from chaoscypher_cortex.features.llm.models import LLMStatsResponse
from chaoscypher_cortex.features.llm.service import LLMService


__all__ = ["LLMService", "LLMStatsResponse", "router"]
