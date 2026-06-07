# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Graph Models.

Response models for graph operations.
"""

from typing import Any

from pydantic import BaseModel, Field

from chaoscypher_core.models import Edge, Node
from chaoscypher_cortex.shared.api.models import PaginationMetadata


# ============================================================================
# Grounding API Response Models
# ============================================================================


class NodeWithEdgesResponse(BaseModel):
    """Node with all connected edges."""

    node: Node
    outgoing_edges: list[Edge]
    incoming_edges: list[Edge]
    total_outgoing: int
    total_incoming: int

    model_config = {"from_attributes": True}


class GroundingNodeListResponse(BaseModel):
    """Paginated list of nodes with the canonical ``{data, pagination}`` envelope."""

    data: list[Node]
    pagination: PaginationMetadata


class GroundingEdgeListResponse(BaseModel):
    """Paginated list of edges with the canonical ``{data, pagination}`` envelope."""

    data: list[Edge]
    pagination: PaginationMetadata


class NeighborNodeResponse(BaseModel):
    """Neighbor node with relationship information."""

    node: Node
    relationship_type: str
    edge_id: str
    direction: str
    edge_properties: dict[str, Any]

    model_config = {"from_attributes": True}


class NeighborsResponse(BaseModel):
    """List of neighbor nodes with relationship context."""

    node_id: str
    neighbors: list[NeighborNodeResponse]
    total: int
    direction: str

    model_config = {"from_attributes": True}


# ============================================================================
# Source Groups Response Models
# ============================================================================


class SourceGroupResponse(BaseModel):
    """A source with its extracted entity node IDs for graph grouping."""

    model_config = {"from_attributes": True}

    source_id: str
    title: str
    source_type: str
    filename: str
    extraction_domain: str | None = None
    extraction_domain_icon: str | None = None
    entity_count: int
    entity_node_ids: list[str]


class SourceGroupListResponse(BaseModel):
    """List of source groups for graph visualization."""

    model_config = {"from_attributes": True}

    groups: list[SourceGroupResponse]


# ============================================================================
# Canvas Response Models
# ============================================================================


class CanvasNodePosition(BaseModel):
    """2D position of a node on the canvas."""

    x: float
    y: float


class CanvasNode(BaseModel):
    """Minimal node projection for canvas rendering.

    Mirrors the fields the canvas endpoint projects out of the full
    Node model — position/source_id are optional because not every
    node carries them.
    """

    id: str
    template_id: str | None = None
    label: str | None = None
    position: CanvasNodePosition | None = None
    source_id: str | None = None


class CanvasEdge(BaseModel):
    """Minimal edge projection for canvas rendering."""

    id: str
    source_node_id: str
    target_node_id: str
    template_id: str | None = None
    label: str | None = None


class CanvasTemplate(BaseModel):
    """Minimal template projection for canvas rendering."""

    id: str
    name: str
    template_type: str
    icon: str | None = None
    color: str | None = None
    description: str | None = None


class CanvasResponse(BaseModel):
    """Bulk canvas payload returned by GET /api/v1/graph/canvas.

    Designed to let the frontend render the whole graph in a single
    request. ``truncated`` is True when the node or edge count hit the
    configured ``pagination.canvas_max_nodes`` / ``canvas_max_edges``
    limits and the payload was clamped to prevent browser OOM.
    """

    truncated: bool = Field(
        description=(
            "True when node or edge counts hit the canvas pagination cap and the "
            "payload was clamped."
        ),
    )
    nodes: list[CanvasNode] = Field(description="Minimal node projections for rendering.")
    edges: list[CanvasEdge] = Field(description="Minimal edge projections for rendering.")
    templates: list[CanvasTemplate] = Field(
        description="Templates referenced by the returned nodes and edges.",
    )
    total_nodes: int = Field(description="Number of nodes included in this response.")
    total_edges: int = Field(description="Number of edges included in this response.")
