# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Nodes Models.

Pydantic DTOs for node operations.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from chaoscypher_core.models import NodePosition
from chaoscypher_cortex.shared.api.models import PaginationMetadata


class NodeResponse(BaseModel):
    """Node response DTO.

    When ``minimal=True`` is used on the list endpoint, ``properties``,
    ``embedding``, ``created_at``, and ``updated_at`` are omitted from the
    response to reduce payload size.
    """

    id: str
    template_id: str
    label: str
    properties: dict[str, Any] | None = None
    position: NodePosition | None = None
    embedding: list[float] | None = None
    source_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    # Stats fields (populated for list views)
    edge_count: int | None = None
    incoming_edge_count: int | None = None
    outgoing_edge_count: int | None = None
    citation_count: int | None = None
    relationship_type_count: int | None = None

    model_config = {"from_attributes": True}


class ConnectedNodeResponse(BaseModel):
    """Connected node response for connections view."""

    id: str
    label: str
    template_id: str
    edge_count: int  # Total edges for this connected node
    relationship: str  # Edge label connecting to parent
    direction: str  # "incoming" or "outgoing"


class ConnectionsResponse(BaseModel):
    """Response for node connections endpoint."""

    data: list[ConnectedNodeResponse]
    pagination: dict[str, Any]


class PaginatedNodesResponse(BaseModel):
    """Paginated nodes response."""

    data: list[NodeResponse]
    pagination: PaginationMetadata


class NodePositionUpdateRequest(BaseModel):
    """Request model for updating node position."""

    position: NodePosition


# ================================
# Citation Models
# ================================


class ChunkReference(BaseModel):
    """Chunk reference in citation."""

    id: str
    content: str
    page_number: int | None
    section: str | None
    chunk_metadata: dict[str, Any] | None


class SourceReference(BaseModel):
    """Source reference in citation."""

    id: str
    title: str
    source_type: str
    origin_url: str | None


class CitationResponse(BaseModel):
    """Citation response DTO."""

    id: str
    source: SourceReference
    chunk: ChunkReference
    confidence: float
    extraction_method: str
    context_snippet: str | None
    citation_metadata: dict[str, Any] | None = None
    created_at: datetime


class CitationListResponse(BaseModel):
    """Paginated citations response."""

    data: list[CitationResponse]
    pagination: dict[str, Any]
