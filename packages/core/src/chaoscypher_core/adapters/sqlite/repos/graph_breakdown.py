# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""GraphBreakdownQueryRepository -- aggregation queries for graph snapshot builds.

All queries are multi-tenant safe (filtered by ``database_name``).
Returned types are plain NamedTuples / primitives so the service layer
never touches SQLModel entities (keeps the port boundary clean).

This module lives under ``adapters/sqlite/repos/`` because it issues
raw SQLAlchemy / SQLModel queries directly against the SQLite schema.
The service layer depends on
:class:`chaoscypher_core.ports.storage_graph_snapshot.GraphBreakdownQueryProtocol`
and wires the concrete repo via
:meth:`~chaoscypher_core.services.graph.snapshot.build_service.BuildGraphSnapshotService.from_adapter`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from sqlalchemy.orm import aliased, load_only
from sqlmodel import col, func, select

from chaoscypher_core.adapters.sqlite.models import GraphEdge, GraphNode, GraphTemplate, SourceRow


if TYPE_CHECKING:
    from sqlmodel import Session


class SourceRowSummary(NamedTuple):
    """Lightweight summary of a SourceRow for breakdown assembly."""

    id: str
    filename: str
    title: str | None
    source_type: str | None


class TemplateSummary(NamedTuple):
    """Lightweight summary of a GraphTemplate for breakdown assembly."""

    name: str
    color: str | None


class GraphBreakdownQueryRepository:
    """Runs the SQL aggregation queries that back :class:`BuildGraphSnapshotService`.

    Each method accepts ``database_name`` and an optional ``source_ids``
    allowlist and returns plain Python types (no SQLModel entities) so
    the service layer never crosses the adapter boundary.
    """

    def __init__(self, session: Session) -> None:
        """Initialise with an active SQLModel session.

        Args:
            session: Active SQLModel / SQLAlchemy session.  Must remain
                open for the lifetime of this object.

        """
        self._session = session

    # ------------------------------------------------------------------
    # Source rows
    # ------------------------------------------------------------------

    def list_source_rows(
        self,
        database_name: str,
        source_ids: list[str] | None,
    ) -> list[SourceRowSummary]:
        """Return lightweight source summaries, optionally filtered by ID.

        Uses ``load_only()`` to project the minimum columns required
        (CC003 compliant).

        Args:
            database_name: Database filter applied to every query.
            source_ids: When provided, only rows with matching IDs are
                returned.  ``None`` returns all sources in the database.

        Returns:
            List of :class:`SourceRowSummary` (may be empty).

        """
        stmt = (
            select(SourceRow)
            .where(SourceRow.database_name == database_name)
            .options(
                load_only(
                    SourceRow.id,  # type: ignore[arg-type]
                    SourceRow.database_name,  # type: ignore[arg-type]
                    SourceRow.filename,  # type: ignore[arg-type]
                    SourceRow.title,  # type: ignore[arg-type]
                    SourceRow.source_type,  # type: ignore[arg-type]
                )
            )
        )
        if source_ids is not None:
            stmt = stmt.where(col(SourceRow.id).in_(source_ids))
        rows = self._session.exec(stmt).all()
        return [
            SourceRowSummary(
                id=row.id,
                filename=row.filename,
                title=row.title,
                source_type=row.source_type,
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Template metadata
    # ------------------------------------------------------------------

    def list_template_rows(
        self,
        database_name: str,
        template_ids: list[str],
    ) -> dict[str, TemplateSummary]:
        """Return template name/color keyed by template ID.

        Uses ``load_only()`` to project only the columns required
        (CC003 compliant).

        Args:
            database_name: Database filter.
            template_ids: Template IDs to look up.  May be empty -- an
                empty list returns an empty dict without querying.

        Returns:
            Dict mapping ``template_id`` to :class:`TemplateSummary`.

        """
        if not template_ids:
            return {}
        stmt = (
            select(GraphTemplate)
            .where(GraphTemplate.database_name == database_name)
            .where(col(GraphTemplate.id).in_(template_ids))
            .options(
                load_only(
                    GraphTemplate.id,  # type: ignore[arg-type]
                    GraphTemplate.name,  # type: ignore[arg-type]
                    GraphTemplate.color,  # type: ignore[arg-type]
                )
            )
        )
        rows = self._session.exec(stmt).all()
        return {row.id: TemplateSummary(name=row.name, color=row.color) for row in rows}

    # ------------------------------------------------------------------
    # Aggregation counts
    # ------------------------------------------------------------------

    def count_nodes(
        self,
        database_name: str,
        source_ids: list[str],
    ) -> int:
        """Total node count across all ``source_ids`` in ``database_name``.

        Args:
            database_name: Database filter.
            source_ids: Source IDs to include.

        Returns:
            Integer node count (0 when source_ids is empty).

        """
        if not source_ids:
            return 0
        # mypy can't infer SQLModel column attributes as QueryableAttribute here.
        stmt = (
            select(func.count(GraphNode.id))  # type: ignore[arg-type]
            .where(GraphNode.database_name == database_name)
            .where(col(GraphNode.source_id).in_(source_ids))
        )
        result = self._session.exec(stmt).one()
        return int(result) if result is not None else 0

    def count_all_nodes(self, database_name: str) -> int:
        """Total node count for all sources in ``database_name``.

        Unlike :meth:`count_nodes`, this method does not filter by source ID,
        making it suitable for a fast staleness check across the whole database.

        Args:
            database_name: Database filter.

        Returns:
            Integer node count (0 when the database has no nodes).

        """
        # mypy can't see SQLModel column attrs as ColumnElement here.
        stmt = select(func.count(GraphNode.id)).where(  # type: ignore[arg-type]
            GraphNode.database_name == database_name
        )
        result = self._session.exec(stmt).one()
        return int(result) if result is not None else 0

    def count_edges(
        self,
        database_name: str,
        source_ids: list[str],
    ) -> int:
        """Count edges where both endpoints belong to ``source_ids``.

        Args:
            database_name: Database filter.
            source_ids: Source IDs both endpoints must belong to.

        Returns:
            Total cross-source-set edge count.

        """
        if not source_ids:
            return 0
        SrcNode = aliased(GraphNode)  # noqa: N806
        TgtNode = aliased(GraphNode)  # noqa: N806

        # mypy can't see SQLModel column attrs as ColumnElement here.
        stmt = (
            select(func.count(GraphEdge.id))  # type: ignore[arg-type]
            .join(SrcNode, GraphEdge.source_node_id == SrcNode.id)  # type: ignore[arg-type]
            .join(TgtNode, GraphEdge.target_node_id == TgtNode.id)  # type: ignore[arg-type]
            .where(GraphEdge.database_name == database_name)
            .where(col(SrcNode.source_id).in_(source_ids))
            .where(col(TgtNode.source_id).in_(source_ids))
        )
        result = self._session.exec(stmt).one()
        return int(result) if result is not None else 0

    def count_nodes_per_source(
        self,
        database_name: str,
        source_ids: list[str],
    ) -> dict[str, int]:
        """Count graph nodes per source_id.

        Args:
            database_name: Database filter.
            source_ids: Source IDs to count nodes for.

        Returns:
            Dict mapping ``source_id`` to node count.

        """
        if not source_ids:
            return {}
        # mypy can't see SQLModel column attrs as ColumnElement here.
        stmt = (
            select(GraphNode.source_id, func.count(GraphNode.id).label("cnt"))  # type: ignore[arg-type]
            .where(GraphNode.database_name == database_name)
            .where(col(GraphNode.source_id).in_(source_ids))
            .group_by(GraphNode.source_id)
        )
        rows = self._session.exec(stmt).all()
        return {row[0]: row[1] for row in rows if row[0] is not None}

    def count_internal_links_per_source(
        self,
        database_name: str,
        source_ids: list[str],
    ) -> dict[str, int]:
        """Count internal edges per source (both endpoints share the same source_id).

        Args:
            database_name: Database filter.
            source_ids: Source IDs to gather internal link counts for.

        Returns:
            Dict mapping ``source_id`` to internal link count.

        """
        if not source_ids:
            return {}
        SrcNode = aliased(GraphNode)  # noqa: N806
        TgtNode = aliased(GraphNode)  # noqa: N806

        # mypy can't see SQLModel column attrs as ColumnElement here.
        stmt = (
            select(SrcNode.source_id, func.count(GraphEdge.id).label("cnt"))  # type: ignore[arg-type]
            .join(SrcNode, GraphEdge.source_node_id == SrcNode.id)  # type: ignore[arg-type]
            .join(TgtNode, GraphEdge.target_node_id == TgtNode.id)  # type: ignore[arg-type]
            .where(GraphEdge.database_name == database_name)
            .where(col(SrcNode.source_id).in_(source_ids))
            .where(SrcNode.source_id == TgtNode.source_id)
            .group_by(SrcNode.source_id)
        )
        rows = self._session.exec(stmt).all()
        return {row[0]: row[1] for row in rows if row[0] is not None}

    def count_template_entities_per_source(
        self,
        database_name: str,
        source_ids: list[str],
    ) -> dict[str, dict[str, int]]:
        """Count entities by template_id, grouped by source_id.

        Args:
            database_name: Database filter.
            source_ids: Source IDs to aggregate template usage for.

        Returns:
            Dict mapping ``source_id`` to ``{template_id: count}``.

        """
        if not source_ids:
            return {}
        # mypy can't see SQLModel column attrs as ColumnElement here.
        stmt = (
            select(
                GraphNode.source_id,
                GraphNode.template_id,
                func.count(GraphNode.id).label("cnt"),  # type: ignore[arg-type]
            )
            .where(GraphNode.database_name == database_name)
            .where(col(GraphNode.source_id).in_(source_ids))
            .group_by(GraphNode.source_id, GraphNode.template_id)
        )
        rows = self._session.exec(stmt).all()
        result: dict[str, dict[str, int]] = {}
        for src_id, tpl_id, cnt in rows:
            if src_id is not None:
                result.setdefault(src_id, {})[tpl_id] = cnt
        return result
