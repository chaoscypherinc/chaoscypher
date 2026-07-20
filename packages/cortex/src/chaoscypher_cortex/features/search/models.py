# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Search Models.

Pydantic DTOs for search operations.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class SearchNodeHit(BaseModel):
    """Projection of a graph node as it appears in search results.

    Narrower than the nodes feature's full node payload — contains only the
    fields the search endpoint actually surfaces and the frontend consumes.
    The search slice owns this type so downstream changes to the nodes
    feature (properties, embedding, stats fields, etc.) do not ripple
    through the search API.

    Field rationale (discovered from
    ``packages/interface/src/components/Omnibar/modes/SearchMode.tsx``):

    * ``id``, ``label`` — always read by the UI, always populated by the engine.
    * ``template_id`` — part of the node projection contract; the engine
      populates it from the node's template so API consumers can resolve a
      hit's type without a second fetch. Optional because nodes without a
      template can still surface in results. (The Omnibar navigates by
      ``node.id`` today and does not read this field — see
      ``SearchMode.tsx`` — but it stays in the contract for other clients.)

    Fields that the frontend appears to read on ``sr.node`` but which the
    search engine never populates (``title``, ``type``) are intentionally
    omitted. The UI already uses ``?? fallback`` operators for those
    accesses, so the projection is safe.

    ``edge_count`` is populated lazily by the search service (one batched
    query per response) so the omnibar can show real connection counts
    instead of a hardcoded ``0``.
    """

    id: str
    label: str
    template_id: str | None = None
    edge_count: int = 0


class ChunkResult(BaseModel):
    """Document chunk search result."""

    chunk_id: str
    source_id: str
    chunk_index: int
    content: str
    page_number: int | None = None
    section: str | None = None
    filename: str


class SearchResult(BaseModel):
    """Single search result - can be either a node or chunk."""

    node: SearchNodeHit | None = None
    chunk: ChunkResult | None = None
    score: float
    result_type: Literal["node", "chunk"]


class SearchResponse(BaseModel):
    """Search results response."""

    data: list[SearchResult]
    type: Literal["keyword", "semantic", "hybrid"]


class SearchStatistics(BaseModel):
    """Search index statistics."""

    fulltext_doc_count: int
    vector_index_size: int
    vector_dimension: int


class RebuildIndexResponse(BaseModel):
    """Response from rebuilding search indexes."""

    success: bool
    total_nodes: int
    nodes_with_embeddings: int
    chunks_indexed: int = 0
    message: str


class QueuedRebuildResponse(BaseModel):
    """Response when rebuild is queued (regeneration needed)."""

    task_id: str
    status: str = "queued"
    regenerated: bool = True
    message: str = "Embedding regeneration and index rebuild queued"


class GenerateEmbeddingsResponse(BaseModel):
    """Response from generating embeddings."""

    success: bool
    total_nodes: int
    processed_count: int
    message: str


class IndexStatusResponse(BaseModel):
    """Response from GET /api/v1/search/indexes/status.

    Combines the search repository's current model/dimension snapshot
    with live index row counts from ``SearchRepository.get_index_stats``.
    ``fulltext`` and ``vector`` mirror the nested shape the repo
    returns; when stats collection fails, ``error`` is populated
    instead.
    """

    needs_rebuild: bool = Field(
        description=(
            "True when the persisted embedding model/dimensions no longer match the "
            "current index contents — a full reindex is required."
        ),
    )
    embedding_model: str | None = Field(
        default=None,
        description="Name of the embedding model the index was built against.",
    )
    vector_dimensions: int | None = Field(
        default=None,
        description="Dimensionality of the vectors currently stored in the index.",
    )
    fulltext: dict[str, Any] | None = Field(
        default=None,
        description="Fulltext index stats (e.g. {'document_count': int}).",
    )
    vector: dict[str, Any] | None = Field(
        default=None,
        description="Vector index stats (e.g. {'vector_count': int, 'dimensions': int}).",
    )
    error: str | None = Field(
        default=None,
        description="Populated when index stats could not be read (e.g. table missing).",
    )
