# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Cross-slice summary DTOs composed by aggregator features.

These DTOs originate in individual sibling features (counts, llm, pause,
queue, workflows) but are also consumed by aggregator slices such as
``dashboard``. Centralising them here lets aggregators depend only on
``shared/models`` instead of reaching across to sibling feature modules.

Each originating feature re-imports its summary DTO from this module to
preserve its existing endpoint contract.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class CountsResponse(BaseModel):
    """Resource counts response."""

    knowledge_nodes: int
    links: int
    templates: int
    workflows: int
    lenses: int
    sources: int
    awaiting_confirmation: int = 0

    model_config = {"from_attributes": True}


class LLMStatsResponse(BaseModel):
    """LLM queue stats response."""

    data: dict[str, Any]


class SystemPauseStatusResponse(BaseModel):
    """Body for GET /api/v1/system/processing/status."""

    paused: bool
    paused_at: datetime | None = None
    reason: str | None = None


class QueueStatsResponse(BaseModel):
    """Queue statistics response."""

    queues: list[dict[str, Any]]
    note: str | None = None


class GlobalWorkflowStatsResponse(BaseModel):
    """API response model for global workflow stats."""

    total_workflows: int
    active_workflows: int
    inactive_workflows: int
    total_executions: int
    successful_executions: int
    failed_executions: int
    cancelled_executions: int
    success_rate: float
