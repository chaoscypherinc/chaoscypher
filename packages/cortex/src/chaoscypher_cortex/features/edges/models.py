# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Edges Models.

Pydantic DTOs for edge operations.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from chaoscypher_cortex.shared.api.models import PaginationMetadata


class EdgeResponse(BaseModel):
    """Edge response DTO.

    When ``minimal=True`` is used on the list endpoint, ``properties``,
    ``created_at``, and ``updated_at`` are omitted from the response to
    reduce payload size.
    """

    id: str
    template_id: str
    source_node_id: str
    target_node_id: str
    label: str
    properties: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class PaginatedEdgesResponse(BaseModel):
    """Paginated edges response."""

    data: list[EdgeResponse]
    pagination: PaginationMetadata
