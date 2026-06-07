# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pydantic models for graph snapshot data."""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


class TemplateEntry(BaseModel):
    """One template within a source, with its entity count."""

    id: str = Field(..., description="Template ID (stable across renders)")
    name: str = Field(..., description="Human-readable template name")
    color: str = Field(..., description="Hex color for the template's dots and glow")
    count: int = Field(
        ...,
        ge=0,
        description="Number of entities of this template in the source",
    )
    model_config = ConfigDict(extra="forbid")


class SourceBreakdown(BaseModel):
    """One source's per-template entity counts plus relationship density."""

    id: str = Field(..., description="Source ID (stable across renders)")
    name: str = Field(..., description="Display name (source title or filename)")
    source_type: str = Field(..., description="pdf / text / url / markdown / ...")
    total_entities: int = Field(
        ...,
        ge=0,
        description="Total entities extracted from this source",
    )
    total_internal_links: int = Field(
        default=0,
        ge=0,
        description=(
            "Edges where both endpoints are in this source. Drives glow intensity in the renderer."
        ),
    )
    templates: list[TemplateEntry] = Field(
        default_factory=list,
        description="Templates used in this source, sorted by count descending",
    )
    model_config = ConfigDict(extra="forbid")


class GraphStats(BaseModel):
    """Aggregate counts that are not trivially derivable from sources[]."""

    total_nodes: int = Field(
        ...,
        ge=0,
        description="Total entity count across all sources",
    )
    total_edges: int = Field(
        ...,
        ge=0,
        description="Total relationship count across the graph",
    )
    total_sources: int = Field(
        ...,
        ge=0,
        description="Number of sources contributing to the graph",
    )
    model_config = ConfigDict(extra="forbid")


class GraphBreakdown(BaseModel):
    """Canonical graph snapshot — drives dashboard rendering and export manifests."""

    version: int = Field(
        default=2,
        description="Schema version. 2 = post-refactor shape.",
    )
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When this snapshot was built",
    )
    database_name: str = Field(
        ...,
        description="Database the snapshot was built from",
    )
    title: str | None = Field(
        default=None,
        description=(
            "Display title. Populated by the export dialog (user-entered "
            "'Name for this export') or the Lexicon upload flow (repo name). "
            "None for in-DB snapshots not yet exported."
        ),
    )
    stats: GraphStats = Field(..., description="Aggregate counts")
    sources: list[SourceBreakdown] = Field(
        default_factory=list,
        description="Per-source breakdowns, sorted by total_entities descending",
    )
    model_config = ConfigDict(extra="forbid")
