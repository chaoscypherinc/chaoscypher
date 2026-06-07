# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Triggers Models.

Pydantic DTOs for event triggers API requests/responses.

TriggerResponse lives in ``chaoscypher_cortex.shared.api.models`` because it is
used by both the triggers feature and the workflows feature.

SQLModel table definitions are in chaoscypher.adapters.sqlite.models
"""

from typing import Any

from pydantic import BaseModel

from chaoscypher_cortex.shared.api.models import (
    PaginationMetadata,
    TriggerResponse,
    TriggerSummaryResponse,
)


# ============================================================================
# Request/Response Models (Pydantic)
# ============================================================================


class TriggerCreate(BaseModel):
    """Create trigger DTO."""

    name: str
    event_source: str
    filters: dict[str, Any]
    workflow_id: str
    workflow_inputs: dict[str, Any] | None = None
    enabled: bool = True
    priority: int = 0


class TriggerUpdate(BaseModel):
    """Update trigger DTO."""

    name: str | None = None
    event_source: str | None = None
    filters: dict[str, Any] | None = None
    workflow_id: str | None = None
    workflow_inputs: dict[str, Any] | None = None
    enabled: bool | None = None
    priority: int | None = None


class PaginatedTriggersResponse(BaseModel):
    """Paginated response for listing triggers."""

    data: list[TriggerSummaryResponse]
    pagination: PaginationMetadata


__all__ = [
    "PaginatedTriggersResponse",
    "TriggerCreate",
    "TriggerResponse",
    "TriggerSummaryResponse",
    "TriggerUpdate",
]
