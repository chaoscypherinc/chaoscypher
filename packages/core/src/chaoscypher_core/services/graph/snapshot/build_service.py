# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""BuildGraphSnapshotService -- aggregation service for GraphBreakdown.

Every surface that produces a GraphBreakdown (HTTP endpoint, CLI,
operation handler, export bundle) calls this service.  It is the single
canonical source of truth for graph statistics.

CC012 compliance: The service depends on
:class:`chaoscypher_core.ports.storage_graph_snapshot.GraphBreakdownQueryProtocol`
(a port).  The concrete adapter repo is wired via ``from_adapter`` which
uses a lazy import -- that import is allowed by the CC012 allowlist entry
for this file (same ``from_adapter`` pattern as graph/management/node.py).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from chaoscypher_core.services.graph.snapshot.models import (
    GraphBreakdown,
    GraphStats,
    SourceBreakdown,
    TemplateEntry,
)


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
    from chaoscypher_core.ports.storage_graph_snapshot import GraphBreakdownQueryProtocol

logger = structlog.get_logger(__name__)

_DEFAULT_COLOR = "#888888"


class BuildGraphSnapshotService:
    """Aggregation service that builds a GraphBreakdown from live graph data.

    Reads through :class:`GraphBreakdownQueryProtocol` so the service
    layer stays adapter-free at module scope.  Multi-tenant safe: every
    query is filtered by database_name.
    """

    def __init__(self, query: GraphBreakdownQueryProtocol) -> None:
        """Initialise with a query repository implementing GraphBreakdownQueryProtocol.

        Args:
            query: Any object implementing GraphBreakdownQueryProtocol.
                Typically a GraphBreakdownQueryRepository wired from
                the connected SqliteAdapter via ``from_adapter``.

        """
        self._query = query

    @classmethod
    def from_adapter(cls, adapter: SqliteAdapter) -> BuildGraphSnapshotService:
        """Create a BuildGraphSnapshotService from a connected SqliteAdapter.

        Lazy-imports the concrete GraphBreakdownQueryRepository so
        that the module-level import graph stays CC012 clean.
        The allowlist entry for this file explicitly permits the
        ``chaoscypher_core.adapters.sqlite.repos`` import here
        (same from_adapter pattern as graph/management/node.py; lazy
        repo construction for snapshot aggregation).

        Args:
            adapter: Connected SqliteAdapter whose session will be used
                for all queries.  Must be connected before use.

        Returns:
            BuildGraphSnapshotService wired to the adapter session.

        """
        from chaoscypher_core.adapters.sqlite.repos.graph_breakdown import (
            GraphBreakdownQueryRepository,
        )

        assert adapter.session is not None, "adapter must be connected"
        return cls(GraphBreakdownQueryRepository(adapter.session))

    def build(
        self,
        database_name: str,
        source_ids: list[str] | None = None,
        title: str | None = None,
    ) -> GraphBreakdown:
        """Build and return a GraphBreakdown for the given database.

        Args:
            database_name: Database to aggregate (filters every query).
            source_ids: Optional list of source IDs to restrict the
                breakdown.  None means all sources for the database.
            title: Optional display title passed through to the model.

        Returns:
            A fully populated GraphBreakdown.  Never raises on an
            empty graph -- returns a valid object with zero counts.

        """
        source_rows = self._query.list_source_rows(database_name, source_ids)

        if not source_rows:
            return GraphBreakdown(
                database_name=database_name,
                title=title,
                generated_at=datetime.now(UTC),
                stats=GraphStats(total_nodes=0, total_edges=0, total_sources=0),
                sources=[],
            )

        active_source_ids = [s.id for s in source_rows]

        node_counts = self._query.count_nodes_per_source(database_name, active_source_ids)
        total_nodes = sum(node_counts.get(sid, 0) for sid in active_source_ids)
        total_edges = self._query.count_edges(database_name, active_source_ids)
        internal_link_counts = self._query.count_internal_links_per_source(
            database_name, active_source_ids
        )
        template_counts = self._query.count_template_entities_per_source(
            database_name, active_source_ids
        )

        # Gather the union of all template IDs referenced across sources
        all_template_ids = list(
            {tpl_id for tpl_map in template_counts.values() for tpl_id in tpl_map}
        )
        template_meta = self._query.list_template_rows(database_name, all_template_ids)

        source_breakdowns: list[SourceBreakdown] = []
        for src in source_rows:
            entity_count = node_counts.get(src.id, 0)
            internal_links = internal_link_counts.get(src.id, 0)

            tpl_entries: list[TemplateEntry] = []
            for tpl_id, count in sorted(
                template_counts.get(src.id, {}).items(),
                key=lambda kv: (-kv[1], kv[0]),
            ):
                meta = template_meta.get(tpl_id)
                tpl_name = meta.name if meta else tpl_id
                tpl_color = meta.color if (meta and meta.color) else _DEFAULT_COLOR
                tpl_entries.append(
                    TemplateEntry(id=tpl_id, name=tpl_name, color=tpl_color, count=count)
                )

            source_breakdowns.append(
                SourceBreakdown(
                    id=src.id,
                    name=src.title if src.title is not None else src.filename,
                    source_type=src.source_type if src.source_type is not None else "unknown",
                    total_entities=entity_count,
                    total_internal_links=internal_links,
                    templates=tpl_entries,
                )
            )

        source_breakdowns.sort(key=lambda s: (-s.total_entities, s.id))

        return GraphBreakdown(
            database_name=database_name,
            title=title,
            generated_at=datetime.now(UTC),
            stats=GraphStats(
                total_nodes=total_nodes,
                total_edges=total_edges,
                total_sources=len(source_breakdowns),
            ),
            sources=source_breakdowns,
        )
