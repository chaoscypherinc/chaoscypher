# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LLM Models.

Pydantic DTOs for LLM queue monitoring.
"""

from typing import Any

from pydantic import BaseModel

from chaoscypher_cortex.shared.models.summaries import LLMStatsResponse


class LLMTasksResponse(BaseModel):
    """LLM tasks response."""

    data: list[dict[str, Any]]


class LLMTaskStatusResponse(BaseModel):
    """LLM task status response."""

    data: dict[str, Any]


class CancelAllTasksResponse(BaseModel):
    """Cancel all tasks response."""

    data: dict[str, Any]


class ClearSemaphoreResponse(BaseModel):
    """Clear semaphore response."""

    data: dict[str, Any]


__all__ = [
    "CancelAllTasksResponse",
    "ClearSemaphoreResponse",
    "LLMStatsResponse",
    "LLMTaskStatusResponse",
    "LLMTasksResponse",
]
