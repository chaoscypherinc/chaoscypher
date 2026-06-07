# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared Pydantic DTOs used by more than one Cortex slice.

Placing a DTO here indicates: multiple features/slices legitimately
need this shape. By convention the most common case is aggregator
slices (e.g., dashboard) that compose summary responses from sibling
features — the summary DTOs live here, each feature's endpoint imports
its summary DTO from here.

Do NOT put feature-specific request/response DTOs here. Those belong
in their feature's own models.py.
"""

from chaoscypher_cortex.shared.models.summaries import (
    CountsResponse,
    GlobalWorkflowStatsResponse,
    LLMStatsResponse,
    QueueStatsResponse,
    SystemPauseStatusResponse,
)


__all__ = [
    "CountsResponse",
    "GlobalWorkflowStatsResponse",
    "LLMStatsResponse",
    "QueueStatsResponse",
    "SystemPauseStatusResponse",
]
