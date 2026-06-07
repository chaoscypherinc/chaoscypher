# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Dashboard composite response."""

from __future__ import annotations

from pydantic import BaseModel, Field

from chaoscypher_cortex.shared.models.summaries import (
    CountsResponse,
    GlobalWorkflowStatsResponse,
    LLMStatsResponse,
    QueueStatsResponse,
    SystemPauseStatusResponse,
)


class DashboardResponse(BaseModel):
    """Aggregated live-UI snapshot."""

    counts: CountsResponse = Field(..., description="Knowledge entity counts")
    llm: LLMStatsResponse = Field(..., description="LLM queue + cost stats")
    queue: QueueStatsResponse = Field(..., description="Operations + LLM queue depth")
    workflows: GlobalWorkflowStatsResponse = Field(..., description="Workflow stats")
    processing: SystemPauseStatusResponse = Field(..., description="System pause status")
