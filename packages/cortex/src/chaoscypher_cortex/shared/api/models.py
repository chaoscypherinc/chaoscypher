# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared API Models.

Pydantic DTOs that are used across multiple feature slices.

Models are placed here (instead of in a feature's models.py) when they need to be
imported by more than one feature, preventing cross-feature import violations.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class PaginationMetadata(BaseModel):
    """Pagination metadata for paginated list responses."""

    total: int
    page: int
    page_size: int
    total_pages: int
    has_next: bool
    has_prev: bool


class TriggerSummaryResponse(BaseModel):
    """Trigger summary for list endpoints (excludes large JSON fields).

    Shared because it is used by both the triggers feature and the workflows
    feature (``GET /workflows/{id}/triggers``).
    """

    id: str
    name: str
    event_source: str
    workflow_id: str
    enabled: bool
    priority: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TriggerResponse(BaseModel):
    """Trigger response DTO.

    Shared because it is used by both the triggers feature and the workflows
    feature (``GET /workflows/{id}/triggers``).
    """

    id: str
    name: str
    event_source: str
    filters: dict[str, Any]
    workflow_id: str
    workflow_inputs: dict[str, Any] | None
    enabled: bool
    priority: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
