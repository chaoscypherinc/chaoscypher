# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Graph snapshot storage port for chaoscypher-core.

Defines the protocol (port) for reading and writing pre-computed
``GraphBreakdown`` snapshots -- one row per database.  The concrete
SQLite implementation lives in
``chaoscypher_core.adapters.sqlite.repos.graph_snapshot``.

``SnapshotStalenessInfo`` is a lightweight DTO returned by
``get_staleness_info``; it exposes only the scalar columns so callers
can decide whether to rebuild without deserialising the full JSON payload.

``GraphBreakdownQueryProtocol`` is the port that
:class:`~chaoscypher_core.services.graph.snapshot.build_service.BuildGraphSnapshotService`
depends on for live aggregation queries.  The concrete implementation is
:class:`chaoscypher_core.adapters.sqlite.repos.graph_breakdown.GraphBreakdownQueryRepository`.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, ConfigDict, Field


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.repos.graph_breakdown import (
        SourceRowSummary,
        TemplateSummary,
    )
    from chaoscypher_core.services.graph.snapshot.models import GraphBreakdown


class SnapshotStalenessInfo(BaseModel):
    """Lightweight metadata for staleness decisions.

    Consumers compare ``generated_at`` + counts against the live DB to
    decide whether to rebuild.  Returned by
    ``GraphSnapshotStorageProtocol.get_staleness_info`` without
    deserialising the full ``payload_json``.
    """

    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    node_count: int = Field(ge=0)
    edge_count: int = Field(ge=0)


class GraphSnapshotStorageProtocol(Protocol):
    """Port for reading and writing pre-computed GraphBreakdown snapshots."""

    def upsert(self, breakdown: GraphBreakdown) -> None:
        """Insert or replace the snapshot for ``breakdown.database_name``."""
        ...

    def get_current(self, database_name: str) -> GraphBreakdown | None:
        """Return the latest snapshot or None if no row exists."""
        ...

    def get_staleness_info(self, database_name: str) -> SnapshotStalenessInfo | None:
        """Return lightweight metadata (generated_at + counts) without deserializing the payload."""
        ...


class GraphBreakdownQueryProtocol(Protocol):
    """Port for live graph aggregation queries used by BuildGraphSnapshotService.

    The concrete implementation is
    :class:`chaoscypher_core.adapters.sqlite.repos.graph_breakdown.GraphBreakdownQueryRepository`.
    BuildGraphSnapshotService depends on this protocol (not the concrete class)
    so the service layer remains adapter-free at module scope.
    """

    def list_source_rows(
        self,
        database_name: str,
        source_ids: list[str] | None,
    ) -> list[SourceRowSummary]:
        """Return lightweight source summaries, optionally filtered by ID."""
        ...

    def list_template_rows(
        self,
        database_name: str,
        template_ids: list[str],
    ) -> dict[str, TemplateSummary]:
        """Return template name/color keyed by template ID."""
        ...

    def count_nodes(
        self,
        database_name: str,
        source_ids: list[str],
    ) -> int:
        """Total node count across all source_ids in database_name."""
        ...

    def count_all_nodes(self, database_name: str) -> int:
        """Total node count for all sources in database_name (no source filter)."""
        ...

    def count_edges(
        self,
        database_name: str,
        source_ids: list[str],
    ) -> int:
        """Count edges where both endpoints belong to source_ids."""
        ...

    def count_nodes_per_source(
        self,
        database_name: str,
        source_ids: list[str],
    ) -> dict[str, int]:
        """Count graph nodes per source_id."""
        ...

    def count_internal_links_per_source(
        self,
        database_name: str,
        source_ids: list[str],
    ) -> dict[str, int]:
        """Count internal edges per source (both endpoints share the same source_id)."""
        ...

    def count_template_entities_per_source(
        self,
        database_name: str,
        source_ids: list[str],
    ) -> dict[str, dict[str, int]]:
        """Count entities by template_id, grouped by source_id."""
        ...
