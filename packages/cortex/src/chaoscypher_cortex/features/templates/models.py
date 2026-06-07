# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Templates Models.

Pydantic DTOs for template operations.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from chaoscypher_core.models import PropertyDefinition
from chaoscypher_cortex.shared.api.models import PaginationMetadata


class TemplateResponse(BaseModel):
    """Template response DTO."""

    id: str
    name: str
    description: str | None
    template_type: str
    properties: list[PropertyDefinition]
    is_system: bool
    icon: str | None = None
    color: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PaginatedTemplatesResponse(BaseModel):
    """Paginated templates response."""

    data: list[TemplateResponse]
    pagination: PaginationMetadata


class QueuedEmbeddingRegenResponse(BaseModel):
    """202 response returned by POST /api/v1/templates/embeddings.

    The request enqueues a background job on the LLM queue and returns
    the task identifier the client polls via
    ``GET /api/v1/queue/tasks/{task_id}``.
    """

    task_id: str = Field(description="Identifier of the queued embedding regeneration task.")
    status: str = Field(
        default="queued",
        description="Queue lifecycle status at the time of response (always 'queued' here).",
    )
    message: str = Field(description="Human-readable confirmation message.")
