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

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import structlog
from sqlmodel import col, delete, func, select

from chaoscypher_core.adapters.sqlite.models import (
    GraphEdge,
    GraphNode,
    GraphNodeSource,
    GraphTemplate,
    SourceRow,
)
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_edge_ops import EdgeOperationsMixin
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_node_ops import NodeOperationsMixin
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_template_ops import TemplateOperationsMixin
from chaoscypher_core.adapters.sqlite.repos.source_visibility import attested_by, visible
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
        if scoped is not None and scoped.bind is self._fallback_session.bind:
            # Adopt the scoped session only when it targets the same
            # engine as this repo's own session — a repo built for a
            # different database (task-database rebind) must not read
            # or write through the scope owner's engine.
            return scoped
        return self._fallback_session

    @session.setter
    def session(self, value: SafeSession) -> None:
        """Backward-compatible setter; assigns to the fallback session."""
        self._fallback_session = value

    @contextmanager
    def write_locked(self) -> Iterator[None]:
        """A unit of work holding SQLite's writer lock from its first statement (``BEGIN IMMEDIATE``).

        The driver opens a transaction only at the first write, so the reads
        before it run outside any transaction and another writer may commit
        between a read and the write built on it: the post-commit identity
        pass folded a node read before a source release rebuilt it, then wrote
        the stale fold over the rebuild (#795). Inside this block the lock
        is taken before the first read.

        Taking the lock also reloads the rows the session cached: pending
        changes are flushed first, then every cached row is expired, so what
        is read inside is the database as of the lock. A long-lived session
        would otherwise hand back a row it loaded before another process
        changed it (SQLAlchemy's identity map; spec 2026-10-24 § 3).

        On this repository's session, sharing ``adapter.transaction()``'s
        depth: the outermost block commits on a clean exit and rolls back on
        an exception. Inside a caller's transaction it only takes the lock
        (when the transaction has not written yet, so holds no lock) and
        leaves the commit to the caller.
        """
        session = self.session
        session._transaction_depth += 1  # noqa: SLF001 — coordinated transaction depth with SafeSession
        outermost = session._transaction_depth == 1  # noqa: SLF001 — coordinated transaction depth with SafeSession
        try:
            connection = session.connection()
            if not connection.connection.driver_connection.in_transaction:  # type: ignore[union-attr]
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                session.flush()
                session.expire_all()
            yield
            if outermost:
                session.commit()  # noqa: CC011 — this block is the transaction boundary
        except BaseException:
            if outermost:
                session.rollback()
            raise
        finally:
            session._transaction_depth -= 1  # noqa: SLF001 — coordinated transaction depth with SafeSession

    def _generate_id(self, prefix: str = "node") -> str:
        """Generate a unique ID with prefix."""
        return generate_id(prefix)

    def discard_failed_work(self) -> None:
        """Roll back what a failed call left in this repository's session (``SafeSession``)."""
        self.session.discard_failed_work()

    def _get_graph_name_for_template(self, template_id: str) -> str:
        """Determine which graph to use based on template ID."""
        if template_id.startswith("lens_"):
            return "lenses"
        return "knowledge"

    # ========================================================================
    # Count Operations
    # ========================================================================

    def _exclude_disabled_node_sources(self, statement: Any, include_disabled_sources: bool) -> Any:
        """Restrict a node count statement to nodes a listing may return.

        The predicate ``list_nodes`` reads (:func:`.source_visibility.visible`), so
        a count and its paired list agree: nodes with no source (legacy/manual)
        stay counted, nodes no enabled source attests drop out. No-op when
        including disabled.
        """
        if include_disabled_sources:
            return statement
        return statement.where(visible(GraphNode))

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
        """Count the nodes the given source documents attest (a merged node once).

        Args:
            source_ids: Source document IDs to count nodes for.
            include_disabled_sources: When False, also drops nodes whose source
                is disabled (mirrors ``list_nodes``), so a paginated total never
                exceeds the rows rendered.

        """
        statement = select(func.count(GraphNode.id)).where(
            GraphNode.database_name == self.database_name,
            attested_by(GraphNode, source_ids),
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
            source_ids: Restrict to edges these source documents attest.
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
            statement = statement.where(attested_by(GraphEdge, source_ids))

        if not include_disabled_sources:
            # Mirror list_edges: keep NULL-source edges, drop edges no enabled
            # source attests.
            statement = statement.where(visible(GraphEdge))

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
            source_ids: When given, restrict nodes/edges to those a source in
                the set attests (source-scoped à-la-carte export).
                Edges are kept only when BOTH endpoints survive the node
                filter, so the knowledge graph stays internally consistent.
            max_items: Safety cap on rows fetched per entity type.

        Returns:
            ``{"nodes": [node dicts], "edges": [edge dicts]}`` where each dict
            includes ``ccx_iri`` (possibly ``None`` for not-yet-exported rows).
        """
        node_stmt = select(GraphNode).where(GraphNode.database_name == self.database_name)
        if source_ids is not None:
            node_stmt = node_stmt.where(attested_by(GraphNode, source_ids))
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
                .where(attested_by(GraphNode, source_ids))
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
        """Return IDs of nodes whose source_id references a missing SourceRow and no source attests.

        The home naming no source row is the orphan sign; a membership is the
        attestation (#795, D1). A merged node another source still attests is
        that source's, whatever its home says, so it is never swept: only a
        release may take a source's share of it.
        """
        from sqlmodel import exists

        stmt = (
            select(GraphNode.id)
            .where(GraphNode.database_name == database_name)
            .where(GraphNode.source_id.is_not(None))
            .where(~exists().where(SourceRow.id == GraphNode.source_id))
            .where(~exists().where(GraphNodeSource.node_id == GraphNode.id))
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
        """Return IDs of unused non-system templates whose source_id references a missing SourceRow.

        A template a node or edge still uses is kept (D6, #795), as a release
        keeps it; deleting it would also fail on the users' ``RESTRICT`` key.
        """
        from sqlmodel import exists

        stmt = (
            select(GraphTemplate.id)
            .where(GraphTemplate.database_name == database_name)
            .where(GraphTemplate.is_system.is_(False))
            .where(GraphTemplate.source_id.is_not(None))
            .where(~exists().where(SourceRow.id == GraphTemplate.source_id))
            .where(~exists().where(GraphNode.template_id == GraphTemplate.id))
            .where(~exists().where(GraphEdge.template_id == GraphTemplate.id))
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
            # Mirror list_templates (the shared template reading). Skipped when
            # filtering by a specific source.
            stmt = stmt.where(visible(GraphTemplate))
        return int(self.session.exec(stmt).one())
