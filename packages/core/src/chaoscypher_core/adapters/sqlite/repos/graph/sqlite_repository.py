# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""SQLite-backed Graph Repository.

Direct SQLite storage with no in-memory caching: every read and write
goes through the database under WAL mode, so concurrent Cortex and
Neuron processes always see the same state.

Composed from focused mixins:
- NodeOperationsMixin: Node CRUD + batch operations
- EdgeOperationsMixin: Edge CRUD + batch operations
- TemplateOperationsMixin: Template CRUD + batch + defaults
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from sqlmodel import col, delete, func, select

from chaoscypher_core.adapters.sqlite.models import GraphEdge, GraphNode, GraphTemplate, SourceRow
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_edge_ops import EdgeOperationsMixin
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_node_ops import NodeOperationsMixin
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_template_ops import TemplateOperationsMixin
from chaoscypher_core.adapters.sqlite.utils import entity_to_dict
from chaoscypher_core.utils import generate_id


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.safe_session import SafeSession
    from chaoscypher_core.services.workflows.triggers.engine.executor import TriggerExecutor


logger = structlog.get_logger(__name__)


class GraphRepository(
    NodeOperationsMixin,
    EdgeOperationsMixin,
    TemplateOperationsMixin,
):
    """SQLite-backed repository for graph operations.

    Provides clean interface for graph nodes, edges, and templates stored in SQLite.
    All operations go directly to the database — no in-memory caching — so
    concurrent processes always observe the same state.

    Args:
        session: SQLModel session for database operations
        database_name: Name of the database (for multi-database isolation)

    Example:
        >>> from chaoscypher_core.adapters.sqlite.repos import GraphRepository
        >>> from chaoscypher_core.adapters.sqlite.session import get_db_session
        >>>
        >>> with get_db_session("/data/databases/default/app.db") as session:
        ...     repo = GraphRepository(session, "default")
        ...     node = repo.create_node(NodeCreate(
        ...         template_id="system_template_item",
        ...         label="Albert Einstein",
        ...         properties={"definition": "Theoretical physicist"}
        ...     ))

    """

    trigger_service: TriggerExecutor | None

    def __init__(self, session: SafeSession, database_name: str = "default"):
        """Initialize graph repository.

        Args:
            session: SafeSession providing maybe_commit() transaction coordination.
                Stored as ``_fallback_session``; ``GraphRepository.session``
                resolves to the per-task ``ContextVar`` from
                ``SqliteAdapter.session_scope()`` when one is active, so
                adapter + graph_repo always share a single session per
                queue handler dispatch.
            database_name: Name of the database for multi-database isolation

        """
        self._fallback_session: SafeSession = session
        self.database_name = database_name
        self.trigger_service = None

    @property
    def session(self) -> SafeSession:
        """Active session: per-task scope if entered, else fallback.

        Mirrors ``SqliteAdapter.session`` so the adapter and graph repo
        stay on the **same** session per queue handler dispatch — sharing
        a session is what keeps the commit handler from self-deadlocking
        against the SQLite writer lock (see
        ``packages/neuron/src/chaoscypher_neuron/setup/shared.py`` for
        the original constraint write-up).
        """
        # Lazy import avoids a hard circular dependency: the adapter module
        # imports from this repos package via the mixins barrel.
        from chaoscypher_core.adapters.sqlite.adapter import _current_session

        scoped = _current_session.get()
        if scoped is not None:
            return scoped
        return self._fallback_session

    @session.setter
    def session(self, value: SafeSession) -> None:
        """Backward-compatible setter; assigns to the fallback session."""
        self._fallback_session = value

    def _generate_id(self, prefix: str = "node") -> str:
        """Generate a unique ID with prefix."""
        return generate_id(prefix)

    def _get_graph_name_for_template(self, template_id: str) -> str:
        """Determine which graph to use based on template ID."""
        if template_id.startswith("lens_"):
            return "lenses"
        return "knowledge"

    # ========================================================================
    # Count Operations
    # ========================================================================

    def _exclude_disabled_node_sources(self, statement: Any, include_disabled_sources: bool) -> Any:
        """Restrict a node count statement to enabled (or NULL) sources.

        Mirrors the enabled-source filter in ``list_nodes`` so a count and its
        paired list agree: nodes with no source (legacy/manual) stay counted,
        nodes from disabled sources drop out. No-op when including disabled.
        """
        if include_disabled_sources:
            return statement
        return statement.outerjoin(SourceRow, GraphNode.source_id == SourceRow.id).where(
            (GraphNode.source_id.is_(None)) | (SourceRow.enabled == True)  # noqa: E712
        )

    def count_nodes(self, include_disabled_sources: bool = True) -> int:
        """Count total nodes.

        Args:
            include_disabled_sources: When True (default), counts every node —
                the true storage total used by stats/health/reset callers. When
                False, excludes nodes from disabled sources so the count matches
                ``list_nodes`` (which filters them by default), keeping listing
                pagination totals consistent with the rows shown.

        """
        statement = select(func.count(GraphNode.id)).where(
            GraphNode.database_name == self.database_name
        )
        statement = self._exclude_disabled_node_sources(statement, include_disabled_sources)
        return self.session.exec(statement).one()

    def count_nodes_by_source(
        self, source_ids: list[str], include_disabled_sources: bool = True
    ) -> int:
        """Count nodes from specific source documents.

        Args:
            source_ids: Source document IDs to count nodes for.
            include_disabled_sources: When False, also drops nodes whose source
                is disabled (mirrors ``list_nodes``), so a paginated total never
                exceeds the rows rendered.

        """
        statement = select(func.count(GraphNode.id)).where(
            GraphNode.database_name == self.database_name,
            col(GraphNode.source_id).in_(source_ids),
        )
        statement = self._exclude_disabled_node_sources(statement, include_disabled_sources)
        return self.session.exec(statement).one()

    def count_edges_per_node(self, node_ids: list[str]) -> dict[str, int]:
        """Return ``{node_id: total_incident_edges}`` for the given nodes.

        Two grouped queries (one per direction), summed in Python. Every
        input ID appears in the result; nodes with no incident edges
        return ``0``.
        """
        if not node_ids:
            return {}
        out: dict[str, int] = dict.fromkeys(node_ids, 0)
        run = self.session.exec  # local alias keeps the call site short
        for direction_col in (GraphEdge.source_node_id, GraphEdge.target_node_id):
            grouped = (
                select(direction_col, func.count(GraphEdge.id))  # type: ignore[arg-type, call-overload]
                .where(
                    GraphEdge.database_name == self.database_name,
                    col(direction_col).in_(node_ids),
                )
                .group_by(direction_col)
            )
            for nid, cnt in run(grouped).all():
                out[nid] = out.get(nid, 0) + int(cnt)
        return out

    def count_edges(
        self,
        source_node_id: str | None = None,
        target_node_id: str | None = None,
        source_ids: list[str] | None = None,
        include_disabled_sources: bool = True,
    ) -> int:
        """Count edges, optionally filtered by source/target node or source document.

        Args:
            source_node_id: Restrict to edges leaving this node.
            target_node_id: Restrict to edges entering this node.
            source_ids: Restrict to edges from these source documents.
            include_disabled_sources: When False, also drops edges from disabled
                sources (mirrors ``list_edges``) so listing pagination totals
                match the rows rendered.

        """
        statement = select(func.count(GraphEdge.id)).where(
            GraphEdge.database_name == self.database_name
        )

        if source_node_id is not None:
            statement = statement.where(GraphEdge.source_node_id == source_node_id)
        if target_node_id is not None:
            statement = statement.where(GraphEdge.target_node_id == target_node_id)
        if source_ids is not None:
            statement = statement.where(col(GraphEdge.source_id).in_(source_ids))

        if not include_disabled_sources:
            # Mirror list_edges: keep NULL-source edges, drop disabled-source
            # edges. The join carries database_name to bind the right Source.
            statement = statement.outerjoin(
                SourceRow,
                (GraphEdge.source_id == SourceRow.id)
                & (SourceRow.database_name == self.database_name),
            ).where(
                (GraphEdge.source_id.is_(None)) | (SourceRow.enabled == True)  # noqa: E712
            )

        return self.session.exec(statement).one()

    def count_nodes_by_template(
        self,
        template_ids: list[str],
        exclude: bool = False,
        include_disabled_sources: bool = True,
    ) -> int:
        """Count nodes with specific template IDs (or excluding them).

        Args:
            template_ids: Template IDs to match (or exclude).
            exclude: When True, count nodes whose template is NOT in the list.
            include_disabled_sources: When False, also drops nodes from disabled
                sources (mirrors ``list_nodes``) so paginated totals match rows.

        """
        statement = select(func.count(GraphNode.id)).where(
            GraphNode.database_name == self.database_name
        )

        if exclude:
            statement = statement.where(~col(GraphNode.template_id).in_(template_ids))
        else:
            statement = statement.where(col(GraphNode.template_id).in_(template_ids))

        statement = self._exclude_disabled_node_sources(statement, include_disabled_sources)
        return self.session.exec(statement).one()

    def count_templates_by_system(self, is_system: bool) -> int:
        """Count user or system templates."""
        return self.session.exec(
            select(func.count(GraphTemplate.id)).where(
                GraphTemplate.database_name == self.database_name,
                GraphTemplate.is_system == is_system,
            )
        ).one()

    def get_template_usage_counts(
        self, template_ids: list[str] | None = None
    ) -> dict[str, dict[str, int]]:
        """Get usage counts (nodes and edges) for templates.

        Args:
            template_ids: Optional list of template IDs to get counts for.
                         If None, returns counts for all templates.

        Returns:
            Dict mapping template_id to {"nodes": count, "edges": count}
        """
        result: dict[str, dict[str, int]] = {}

        # Build base filter
        base_filter = GraphNode.database_name == self.database_name

        # Get node counts grouped by template_id
        node_stmt = (
            select(GraphNode.template_id, func.count(GraphNode.id))
            .where(base_filter)
            .group_by(GraphNode.template_id)
        )
        if template_ids is not None:
            node_stmt = node_stmt.where(col(GraphNode.template_id).in_(template_ids))

        for template_id, count in self.session.exec(node_stmt).all():
            if template_id:
                result[template_id] = {"nodes": count, "edges": 0}

        # Get edge counts grouped by template_id
        edge_stmt = (
            select(GraphEdge.template_id, func.count(GraphEdge.id))
            .where(GraphEdge.database_name == self.database_name)
            .group_by(GraphEdge.template_id)
        )
        if template_ids is not None:
            edge_stmt = edge_stmt.where(col(GraphEdge.template_id).in_(template_ids))

        for template_id, count in self.session.exec(edge_stmt).all():
            if template_id:
                if template_id in result:
                    result[template_id]["edges"] = count
                else:
                    result[template_id] = {"nodes": 0, "edges": count}

        return result

    # ========================================================================
    # Utility Operations
    # ========================================================================

    def clear_all(self) -> dict[str, int]:
        """Clear all graph data (nodes, edges, templates).

        WARNING: This operation cannot be undone!
        """
        # Count before clearing
        node_count = self.count_nodes()
        edge_count = self.count_edges()
        template_count = self.count_templates(database_name=self.database_name)

        # Delete all
        self.session.exec(delete(GraphEdge).where(GraphEdge.database_name == self.database_name))
        self.session.exec(delete(GraphNode).where(GraphNode.database_name == self.database_name))
        self.session.exec(
            delete(GraphTemplate).where(GraphTemplate.database_name == self.database_name)
        )
        self.session.maybe_commit()

        logger.info(
            "graphs_cleared",
            nodes_removed=node_count,
            edges_removed=edge_count,
            templates_removed=template_count,
        )

        return {
            "nodes_removed": node_count,
            "edges_removed": edge_count,
            "templates_removed": template_count,
        }

    # ------------------------------------------------------------------
    # Private per-source deletion helpers shared by delete_graph_data_by_source
    # and delete_source_artifacts.  Both maintain the same deletion order and
    # FK-respecting sequence without duplicating SQL.
    # ------------------------------------------------------------------

    def _delete_edges_for_source(self, source_id: str, session: SafeSession | None = None) -> int:
        """Delete all edges whose source_id matches.  Returns rowcount."""
        _session = session if session is not None else self.session
        result = _session.exec(
            delete(GraphEdge).where(
                GraphEdge.database_name == self.database_name,
                GraphEdge.source_id == source_id,
            )
        )
        return int(result.rowcount or 0)  # type: ignore[union-attr]

    def _delete_nodes_for_source(self, source_id: str, session: SafeSession | None = None) -> int:
        """Delete all nodes whose source_id matches.  Returns rowcount."""
        _session = session if session is not None else self.session
        result = _session.exec(
            delete(GraphNode).where(
                GraphNode.database_name == self.database_name,
                GraphNode.source_id == source_id,
            )
        )
        return int(result.rowcount or 0)  # type: ignore[union-attr]

    def _delete_templates_for_source(
        self, source_id: str, session: SafeSession | None = None
    ) -> int:
        """Delete all templates whose source_id matches.

        GraphTemplate.source_id is populated for extraction-derived templates.
        Templates whose source_id is NULL (global / manually created) are
        unaffected because the WHERE clause only matches non-NULL equality.
        Returns rowcount.
        """
        _session = session if session is not None else self.session
        result = _session.exec(
            delete(GraphTemplate).where(
                GraphTemplate.database_name == self.database_name,
                GraphTemplate.source_id == source_id,
            )
        )
        return int(result.rowcount or 0)  # type: ignore[union-attr]

    def _collect_node_ids_for_source(self, source_id: str) -> list[str]:
        """Return IDs of all nodes for *source_id* (needed by search-index cleanup)."""
        return list(
            self.session.exec(
                select(GraphNode.id).where(
                    GraphNode.database_name == self.database_name,
                    GraphNode.source_id == source_id,
                )
            ).all()
        )

    def delete_graph_data_by_source(self, source_id: str) -> dict[str, Any]:
        """Delete all graph data (edges, nodes, templates) for a given source.

        Used for idempotent commit: cleans up previously committed graph objects
        before re-committing. Deletion order respects FK constraints:
        edges first, then nodes, then templates.

        Args:
            source_id: Source ID whose graph data should be deleted.

        Returns:
            Dict with keys:
                - edges_deleted: Number of edges removed
                - nodes_deleted: Number of nodes removed
                - templates_deleted: Number of templates removed
                - deleted_node_ids: List of deleted node IDs (for search index cleanup)

        """
        # Collect node IDs before deletion (needed for search index cleanup)
        deleted_node_ids = self._collect_node_ids_for_source(source_id)

        edges_deleted = self._delete_edges_for_source(source_id)
        nodes_deleted = self._delete_nodes_for_source(source_id)
        templates_deleted = self._delete_templates_for_source(source_id)

        self.session.maybe_commit()

        logger.info(
            "graph_data_deleted_by_source",
            source_id=source_id,
            edges_deleted=edges_deleted,
            nodes_deleted=nodes_deleted,
            templates_deleted=templates_deleted,
        )

        return {
            "edges_deleted": edges_deleted,
            "nodes_deleted": nodes_deleted,
            "templates_deleted": templates_deleted,
            "deleted_node_ids": deleted_node_ids,
        }

    def delete_source_artifacts(
        self,
        source_id: str,
        session: SafeSession | None = None,
    ) -> dict[str, int]:
        """Delete graph nodes, edges, and templates created by a source's commit.

        Used by ``trigger_extraction(force=True)`` before
        ``reset_for_re_extraction`` so the new extraction lands in a clean
        graph rather than overlapping with the prior commit.

        Unlike ``delete_source`` (the full cascade), this method leaves the
        source row, its chunks, and its embeddings intact — indexing is still
        valid; only the extraction-derived graph artifacts are removed.

        Template handling: ``GraphTemplate.source_id`` is set for templates
        that were inferred or created during an extraction commit.  Templates
        whose ``source_id`` is ``NULL`` (global / manually created) are
        unaffected.  This is the correct behaviour — global templates do not
        belong to any single source and must not be deleted on re-extract.

        Deletion order respects FK constraints: edges first (FK to nodes),
        then nodes, then templates.

        Args:
            source_id: Source ID whose graph artifacts should be removed.
            session: Optional SafeSession to use for all writes. When provided
                (e.g. ``storage_adapter.session``), all three deletes and the
                ``maybe_commit`` run on that session so they participate in the
                caller's transaction instead of auto-committing on the
                repository's own session.

        Returns:
            Dict with keys:
                - nodes_deleted: Number of ``GraphNode`` rows removed.
                - edges_deleted: Number of ``GraphEdge`` rows removed.
                - templates_deleted: Number of ``GraphTemplate`` rows removed.

        """
        _session = session if session is not None else self.session
        edges_deleted = self._delete_edges_for_source(source_id, _session)
        nodes_deleted = self._delete_nodes_for_source(source_id, _session)
        templates_deleted = self._delete_templates_for_source(source_id, _session)

        _session.maybe_commit()

        logger.info(
            "source_artifacts_deleted",
            source_id=source_id,
            nodes_deleted=nodes_deleted,
            edges_deleted=edges_deleted,
            templates_deleted=templates_deleted,
        )

        return {
            "nodes_deleted": nodes_deleted,
            "edges_deleted": edges_deleted,
            "templates_deleted": templates_deleted,
        }

    def export_graph(self, max_items: int = 100000) -> dict[str, Any]:
        """Export all graph data for CCX package creation.

        Args:
            max_items: Maximum nodes/edges to export (default: 100000)

        """
        all_nodes = self.list_nodes(limit=max_items)
        nodes_data = [node.model_dump(mode="json") for node in all_nodes]

        all_edges = self.list_edges(limit=max_items)
        edges_data = [edge.model_dump(mode="json") for edge in all_edges]

        all_templates = self.list_templates()
        templates_data = [template.model_dump(mode="json") for template in all_templates]

        logger.info(
            "graph_exported",
            node_count=len(nodes_data),
            edge_count=len(edges_data),
            template_count=len(templates_data),
        )

        return {
            "nodes": nodes_data,
            "edges": edges_data,
            "templates": templates_data,
        }

    def export_graph_records(
        self,
        *,
        source_ids: list[str] | None = None,
        max_items: int = 100000,
    ) -> dict[str, list[dict[str, Any]]]:
        """Export graph nodes + edges as dicts carrying ``ccx_iri``.

        The Pydantic ``Node`` / ``Edge`` domain models returned by the
        public engine methods (``list_nodes`` / ``export_graph``) do NOT
        carry the persisted ``ccx_iri`` stable-identity column. The CCX 3.0
        exporter keys identity on that IRI, so it reads through this method,
        which projects the ORM rows straight to dicts (via ``entity_to_dict``)
        and therefore preserves ``ccx_iri`` (and every other column).

        Args:
            source_ids: When given, restrict nodes/edges to those whose
                ``source_id`` is in the set (source-scoped à-la-carte export).
                Edges are kept only when BOTH endpoints survive the node
                filter, so the knowledge graph stays internally consistent.
            max_items: Safety cap on rows fetched per entity type.

        Returns:
            ``{"nodes": [node dicts], "edges": [edge dicts]}`` where each dict
            includes ``ccx_iri`` (possibly ``None`` for not-yet-exported rows).
        """
        node_stmt = select(GraphNode).where(GraphNode.database_name == self.database_name)
        if source_ids is not None:
            node_stmt = node_stmt.where(col(GraphNode.source_id).in_(source_ids))
        node_stmt = node_stmt.order_by(GraphNode.id).limit(max_items)
        node_rows = self.session.exec(node_stmt).all()
        nodes = [entity_to_dict(row) for row in node_rows]

        edge_stmt = select(GraphEdge).where(GraphEdge.database_name == self.database_name)
        if source_ids is not None:
            # Prune edges at the SQL level to those touching an in-scope node,
            # instead of fetching up to max_items edges for the whole database
            # and discarding most in Python. Uses a subquery (not a
            # materialized id list) so it neither hits SQLite's bound-variable
            # limit nor risks returning zero edges when the first max_items
            # edges of the DB all belong to other sources. Both-endpoints
            # consistency is still enforced against the fetched node set below.
            in_scope_node_ids = (
                select(GraphNode.id)
                .where(GraphNode.database_name == self.database_name)
                .where(col(GraphNode.source_id).in_(source_ids))
            )
            edge_stmt = edge_stmt.where(
                col(GraphEdge.source_node_id).in_(in_scope_node_ids)
                | col(GraphEdge.target_node_id).in_(in_scope_node_ids)
            )
        edge_stmt = edge_stmt.order_by(GraphEdge.id).limit(max_items)
        edge_rows = self.session.exec(edge_stmt).all()
        edges = [entity_to_dict(row) for row in edge_rows]

        if source_ids is not None:
            node_id_set = {node["id"] for node in nodes if node is not None}
            edges = [
                edge
                for edge in edges
                if edge is not None
                and edge.get("source_node_id") in node_id_set
                and edge.get("target_node_id") in node_id_set
            ]

        return {
            "nodes": [node for node in nodes if node is not None],
            "edges": [edge for edge in edges if edge is not None],
        }

    # ========================================================================
    # Graph cleanup operations (PR2a Task 12).
    # Consumed by GraphCleanupService once it moves into core in PR2b.
    # ========================================================================

    def find_orphaned_edges_by_source_node(self, *, database_name: str) -> list[str]:
        """Return IDs of edges whose source_node_id has no matching GraphNode."""
        from sqlmodel import exists

        stmt = (
            select(GraphEdge.id)
            .where(GraphEdge.database_name == database_name)
            .where(~exists().where(GraphNode.id == GraphEdge.source_node_id))
        )
        return list(self.session.exec(stmt).all())

    def find_orphaned_edges_by_target_node(self, *, database_name: str) -> list[str]:
        """Return IDs of edges whose target_node_id has no matching GraphNode."""
        from sqlmodel import exists

        stmt = (
            select(GraphEdge.id)
            .where(GraphEdge.database_name == database_name)
            .where(~exists().where(GraphNode.id == GraphEdge.target_node_id))
        )
        return list(self.session.exec(stmt).all())

    def delete_edges_batch(self, *, edge_ids: list[str]) -> int:
        """Delete GraphEdge rows by ID list."""
        if not edge_ids:
            return 0
        stmt = delete(GraphEdge).where(col(GraphEdge.id).in_(edge_ids))
        result = self.session.exec(stmt)
        self.session.maybe_commit()
        return int(result.rowcount or 0)

    def find_orphaned_nodes_by_source(self, *, database_name: str) -> list[str]:
        """Return IDs of nodes whose source_id references a missing SourceRow."""
        from sqlmodel import exists

        from chaoscypher_core.adapters.sqlite.models import SourceRow

        stmt = (
            select(GraphNode.id)
            .where(GraphNode.database_name == database_name)
            .where(GraphNode.source_id.is_not(None))
            .where(~exists().where(SourceRow.id == GraphNode.source_id))
        )
        return list(self.session.exec(stmt).all())

    def delete_nodes_batch(self, *, node_ids: list[str]) -> int:
        """Delete GraphNode rows by ID list."""
        if not node_ids:
            return 0
        stmt = delete(GraphNode).where(col(GraphNode.id).in_(node_ids))
        result = self.session.exec(stmt)
        self.session.maybe_commit()
        return int(result.rowcount or 0)

    def find_orphaned_templates_by_source(self, *, database_name: str) -> list[str]:
        """Return IDs of non-system templates whose source_id references a missing SourceRow."""
        from sqlmodel import exists

        from chaoscypher_core.adapters.sqlite.models import SourceRow

        stmt = (
            select(GraphTemplate.id)
            .where(GraphTemplate.database_name == database_name)
            .where(GraphTemplate.is_system.is_(False))
            .where(GraphTemplate.source_id.is_not(None))
            .where(~exists().where(SourceRow.id == GraphTemplate.source_id))
        )
        return list(self.session.exec(stmt).all())

    def delete_templates_batch(self, *, template_ids: list[str]) -> int:
        """Delete GraphTemplate rows by ID list."""
        if not template_ids:
            return 0
        stmt = delete(GraphTemplate).where(col(GraphTemplate.id).in_(template_ids))
        result = self.session.exec(stmt)
        self.session.maybe_commit()
        return int(result.rowcount or 0)

    def count_templates(
        self,
        *,
        database_name: str | None = None,
        template_type: str | None = None,
        source_id: str | None = None,
        include_disabled_sources: bool = True,
    ) -> int:
        """Count GraphTemplate rows.

        Args:
            database_name: Database to scope to. Defaults to ``self.database_name``.
            template_type: Optional filter by template_type ('node' or 'edge').
            source_id: Optional filter by source_id.
            include_disabled_sources: When False, excludes templates from disabled
                sources (mirrors ``list_templates``) so listing pagination totals
                match the rows shown. NULL-source (system / manual) templates stay
                counted. Ignored when ``source_id`` is given — an explicit source
                filter selects that source regardless of enabled-state, matching
                ``list_templates``.

        """
        scope_db = database_name if database_name is not None else self.database_name
        stmt = (
            select(func.count())
            .select_from(GraphTemplate)
            .where(GraphTemplate.database_name == scope_db)
        )
        if template_type is not None:
            stmt = stmt.where(GraphTemplate.template_type == template_type)
        if source_id is not None:
            stmt = stmt.where(GraphTemplate.source_id == source_id)
        elif not include_disabled_sources:
            # Mirror list_templates: NULL-source templates always count; otherwise
            # the source must be enabled. Skipped when filtering by a specific source.
            stmt = stmt.outerjoin(SourceRow, GraphTemplate.source_id == SourceRow.id).where(
                (GraphTemplate.source_id.is_(None)) | (SourceRow.enabled == True)  # noqa: E712
            )
        return int(self.session.exec(stmt).one())
