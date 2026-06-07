# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Edge operations mixin for GraphRepository."""

import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace

import structlog
from sqlalchemy.orm import load_only
from sqlmodel import col, delete, select

from chaoscypher_core.adapters.sqlite.models import GraphEdge, GraphNode, SourceRow
from chaoscypher_core.adapters.sqlite.repos.graph.graph_mixin_base import GraphMixinBase
from chaoscypher_core.models import Edge, EdgeCreate, EdgeUpdate, EdgeWithNodes


logger = structlog.get_logger(__name__)


def _stable_edge_id(
    *,
    database_name: str,
    source_id: str | None,
    template_id: str,
    source_node_id: str,
    target_node_id: str,
) -> str:
    """Derive a content-addressed edge ID from commit-time inputs.

    An edge is uniquely identified within a source by its endpoint nodes
    and its relationship template. The label is intentionally NOT in the
    key — chunks routinely re-extract the same fact with cosmetic label
    variation (case, spacing, underscore vs. space), and the template
    already carries the relationship type. Including the label produced
    duplicate rows for the same fact across chunks (observed: 11 rows
    for a single Boris→Vicomte interaction in the war_and_peace import).

    Two semantically distinct relationships between the same endpoints
    (e.g. "founded" vs "acquired") get different template_ids during
    extraction, so they still produce distinct edge IDs.

    Args:
        database_name: Active database name.
        source_id: Source the edge came from. Edges without a source_id
            fall back to a "no_source" sentinel — outside the resumability
            story.
        template_id: Edge template — carries the relationship type.
        source_node_id: Stable node ID of the edge's source endpoint.
        target_node_id: Stable node ID of the edge's target endpoint.

    Returns:
        Deterministic string of the form ``edge_<24-hex-chars>``.
    """
    scope_source = source_id or "no_source"
    raw = f"{database_name}:{scope_source}:{template_id}:{source_node_id}:{target_node_id}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"edge_{digest}"


class EdgeOperationsMixin(GraphMixinBase):
    """Mixin providing edge CRUD and batch operations for GraphRepository."""

    def create_edge(self, edge_create: EdgeCreate, custom_id: str | None = None) -> Edge:
        """Create a new edge in the graph."""
        edge_id = custom_id or self._generate_id("edge")

        db_edge = GraphEdge(
            id=edge_id,
            database_name=self.database_name,
            graph_name="knowledge",
            template_id=edge_create.template_id,
            source_node_id=edge_create.source_node_id,
            target_node_id=edge_create.target_node_id,
            label=edge_create.label,
            properties=edge_create.properties or {},
            source_id=edge_create.source_id,
        )

        self.session.add(db_edge)
        self.session.maybe_commit()
        self.session.refresh(db_edge)

        return self._db_edge_to_model(db_edge)

    def get_edge(self, edge_id: str) -> Edge | None:
        """Get an edge by ID."""
        statement = select(GraphEdge).where(
            GraphEdge.id == edge_id,
            GraphEdge.database_name == self.database_name,
        )
        db_edge = self.session.exec(statement).first()

        if db_edge is None:
            return None

        return self._db_edge_to_model(db_edge)

    def list_edges(
        self,
        source_node_id: str | None = None,
        target_node_id: str | None = None,
        source_ids: list[str] | None = None,
        skip: int = 0,
        limit: int = 100,
        include_disabled_sources: bool = False,
        minimal: bool = False,
        with_nodes: bool = False,
    ) -> list[Edge] | list[EdgeWithNodes]:
        """List all edges, optionally filtered by source/target node, source doc, and enabled status.

        Args:
            source_node_id: Optional filter by edge source node
            target_node_id: Optional filter by edge target node
            source_ids: Optional list of source document IDs to filter by
            skip: Number of results to skip
            limit: Maximum number of results
            include_disabled_sources: If False (default), excludes edges from disabled sources
            minimal: If True, only load essential fields (excludes properties)
                     for better performance with large graphs
            with_nodes: If True, batch-load source_node and target_node for each edge
                        in a single IN-query and return EdgeWithNodes instances.
                        Eliminates the O(N) get_node()-in-a-loop antipattern.

        Returns:
            List of edges matching the filters.  When with_nodes=True, each
            element is an EdgeWithNodes with .source_node and .target_node set.

        """
        statement = select(GraphEdge).where(GraphEdge.database_name == self.database_name)

        # Apply load_only to control column projection
        if minimal:
            statement = statement.options(
                load_only(
                    GraphEdge.id,
                    GraphEdge.database_name,
                    GraphEdge.template_id,
                    GraphEdge.source_node_id,
                    GraphEdge.target_node_id,
                    GraphEdge.label,
                    GraphEdge.source_id,
                    # Excluded: properties, created_at, updated_at, graph_name
                )
            )
        else:
            statement = statement.options(
                load_only(
                    GraphEdge.id,
                    GraphEdge.database_name,
                    GraphEdge.graph_name,
                    GraphEdge.template_id,
                    GraphEdge.source_node_id,
                    GraphEdge.target_node_id,
                    GraphEdge.label,
                    GraphEdge.properties,
                    GraphEdge.source_id,
                    GraphEdge.created_at,
                    GraphEdge.updated_at,
                    # Note: GraphEdge has no embedding column to exclude
                )
            )

        if source_node_id is not None:
            statement = statement.where(GraphEdge.source_node_id == source_node_id)

        if target_node_id is not None:
            statement = statement.where(GraphEdge.target_node_id == target_node_id)

        if source_ids is not None:
            statement = statement.where(col(GraphEdge.source_id).in_(source_ids))

        # Filter by source enabled status
        if not include_disabled_sources:
            # Include edges with NULL source_id (legacy/manual edges) OR enabled sources
            # Join includes database_name to ensure we find the right Source record
            statement = statement.outerjoin(
                SourceRow,
                (GraphEdge.source_id == SourceRow.id)
                & (SourceRow.database_name == self.database_name),
            ).where(
                (GraphEdge.source_id.is_(None)) | (SourceRow.enabled == True)  # noqa: E712
            )

        statement = statement.order_by(GraphEdge.id).offset(skip).limit(limit)
        db_edges = self.session.exec(statement).all()

        if minimal:
            edges = [self._db_edge_to_model_minimal(e) for e in db_edges]
        else:
            edges = [self._db_edge_to_model(e) for e in db_edges]

        if not with_nodes:
            return edges

        # Batch-load all endpoint nodes in a single IN-query -- O(1) round trips.
        # Collect unique node IDs across both endpoints.
        node_ids: list[str] = list(
            {e.source_node_id for e in edges} | {e.target_node_id for e in edges}
        )
        nodes_stmt = select(GraphNode).where(
            GraphNode.database_name == self.database_name,
            col(GraphNode.id).in_(node_ids),
        )
        db_nodes = self.session.exec(nodes_stmt).all()
        nodes_map = {n.id: self._db_node_to_model(n) for n in db_nodes}

        return [
            EdgeWithNodes(
                id=e.id,
                template_id=e.template_id,
                source_node_id=e.source_node_id,
                target_node_id=e.target_node_id,
                label=e.label,
                properties=e.properties,
                created_at=e.created_at,
                updated_at=e.updated_at,
                source_node=nodes_map.get(e.source_node_id),
                target_node=nodes_map.get(e.target_node_id),
            )
            for e in edges
        ]

    def list_edges_minimal(
        self,
        limit: int = 10000,
        include_disabled_sources: bool = False,
    ) -> list[SimpleNamespace]:
        """List edges with minimal fields for analytics (fast).

        Only loads source_node_id, target_node_id, label - excludes properties.
        Use this for analytics operations that don't need full edge data.

        Args:
            limit: Maximum number of results
            include_disabled_sources: If False (default), excludes edges from disabled sources

        Returns:
            List of SimpleNamespace objects with minimal edge data

        """
        statement = (
            select(GraphEdge)
            .options(
                load_only(
                    GraphEdge.id,
                    GraphEdge.source_node_id,
                    GraphEdge.target_node_id,
                    GraphEdge.label,
                    GraphEdge.template_id,
                )
            )
            .where(GraphEdge.database_name == self.database_name)
        )

        # Filter by source enabled status via source node
        if not include_disabled_sources:
            # Join includes database_name to ensure we find the right Source record
            statement = (
                statement.join(GraphNode, GraphEdge.source_node_id == GraphNode.id)
                .outerjoin(
                    SourceRow,
                    (GraphNode.source_id == SourceRow.id)
                    & (SourceRow.database_name == self.database_name),
                )
                .where(
                    (GraphNode.source_id.is_(None)) | (SourceRow.enabled == True)  # noqa: E712
                )
            )

        statement = statement.limit(limit)
        db_edges = self.session.exec(statement).all()

        return [
            SimpleNamespace(
                id=e.id,
                source_node_id=e.source_node_id,
                target_node_id=e.target_node_id,
                label=e.label,
                template_id=e.template_id,
            )
            for e in db_edges
        ]

    def update_edge(self, edge_id: str, edge_update: EdgeUpdate) -> Edge | None:
        """Update an existing edge."""
        statement = select(GraphEdge).where(
            GraphEdge.id == edge_id,
            GraphEdge.database_name == self.database_name,
        )
        db_edge = self.session.exec(statement).first()

        if db_edge is None:
            return None

        if edge_update.label is not None:
            db_edge.label = edge_update.label

        if edge_update.properties is not None:
            db_edge.properties = edge_update.properties

        db_edge.updated_at = datetime.now(UTC)

        self.session.add(db_edge)
        self.session.maybe_commit()
        self.session.refresh(db_edge)

        return self._db_edge_to_model(db_edge)

    def delete_edge(self, edge_id: str) -> bool:
        """Delete an edge by ID."""
        statement = select(GraphEdge).where(
            GraphEdge.id == edge_id,
            GraphEdge.database_name == self.database_name,
        )
        db_edge = self.session.exec(statement).first()

        if db_edge is None:
            return False

        self.session.delete(db_edge)
        self.session.maybe_commit()

        return True

    def delete_edges_batch(self, edge_ids: list[str]) -> dict:
        """Batch delete multiple edges."""
        if not edge_ids:
            return {"edges_deleted": 0, "not_found": [], "errors": []}

        # Find which edges exist
        statement = select(GraphEdge.id).where(
            GraphEdge.database_name == self.database_name,
            col(GraphEdge.id).in_(edge_ids),
        )
        existing_ids = set(self.session.exec(statement).all())

        not_found = [eid for eid in edge_ids if eid not in existing_ids]

        deleted_count = 0
        if existing_ids:
            edge_delete = delete(GraphEdge).where(
                GraphEdge.database_name == self.database_name,
                col(GraphEdge.id).in_(existing_ids),
            )
            self.session.exec(edge_delete)
            deleted_count = len(existing_ids)
            self.session.maybe_commit()

        logger.info(
            "edges_batch_deleted",
            deleted_count=deleted_count,
            not_found_count=len(not_found),
            total_requested=len(edge_ids),
        )

        return {
            "edges_deleted": deleted_count,
            "not_found": not_found,
            "errors": [],
        }

    async def create_edges_batch(self, edge_creates: list[EdgeCreate]) -> list[Edge]:
        """Create multiple edges in a single batch operation."""
        if not edge_creates:
            return []

        created_edges = []

        for edge_create in edge_creates:
            edge_id = self._generate_id("edge")

            db_edge = GraphEdge(
                id=edge_id,
                database_name=self.database_name,
                graph_name="knowledge",
                template_id=edge_create.template_id,
                source_node_id=edge_create.source_node_id,
                target_node_id=edge_create.target_node_id,
                label=edge_create.label,
                properties=edge_create.properties or {},
                source_id=edge_create.source_id,
            )
            self.session.add(db_edge)
            created_edges.append(db_edge)

        # Convert to models BEFORE commit — all fields are set from
        # constructor values and accessible without refresh. This avoids
        # N individual SELECT queries from session.refresh() per edge.
        result = [self._db_edge_to_model(db_edge) for db_edge in created_edges]

        self.session.maybe_commit()

        logger.info("edges_batch_created", count=len(result))
        return result

    async def upsert_edges_batch(self, edge_creates: list[EdgeCreate]) -> tuple[list[Edge], int]:
        """Idempotently create graph edges keyed by content hash.

        Mirror of ``upsert_nodes_batch``: deterministic stable IDs
        derived from the edge's endpoints, template, and source (label
        is intentionally NOT in the key; the template carries the
        relationship type, and chunks routinely re-extract the same
        fact with cosmetic label variation — see ``_stable_edge_id``
        for the full rationale), plus a bulk SELECT to detect
        pre-existing rows. Relies on ``upsert_nodes_batch`` having
        already produced stable endpoint IDs — otherwise the hash
        changes between runs and dedup breaks.

        First-write-wins semantics: if the stable key already exists,
        the row is returned as-is; a re-run with different properties
        does NOT overwrite.

        Args:
            edge_creates: List of EdgeCreate objects, each with
                source_node_id / target_node_id already stable from
                the prior upsert_nodes_batch call.

        Returns:
            Tuple of:
            - List of Edge Pydantic models in input order with stable
              .id values (includes both newly inserted and pre-existing).
            - Count of rows actually inserted (``session.add`` calls
              that hit the DB), not counting dedup reuses. Use this
              count for ``commit_edges_created`` so the counter reflects
              the true number of new rows, not the input list size.
        """
        if not edge_creates:
            return [], 0

        stable_ids: list[str] = [
            _stable_edge_id(
                database_name=self.database_name,
                source_id=ec.source_id,
                template_id=ec.template_id,
                source_node_id=ec.source_node_id,
                target_node_id=ec.target_node_id,
            )
            for ec in edge_creates
        ]

        existing_rows: dict[str, GraphEdge] = {}
        if stable_ids:
            existing_stmt = select(GraphEdge).where(
                GraphEdge.database_name == self.database_name,
                col(GraphEdge.id).in_(stable_ids),
            )
            for row in self.session.scalars(existing_stmt).all():
                existing_rows[row.id] = row

        new_entities: list[GraphEdge] = []
        result_entities: list[GraphEdge] = []
        batch_seen: dict[str, GraphEdge] = {}
        for stable_id, ec in zip(stable_ids, edge_creates, strict=True):
            if stable_id in existing_rows:
                result_entities.append(existing_rows[stable_id])
                continue
            if stable_id in batch_seen:
                result_entities.append(batch_seen[stable_id])
                continue
            db_edge = GraphEdge(
                id=stable_id,
                database_name=self.database_name,
                graph_name="knowledge",
                template_id=ec.template_id,
                source_node_id=ec.source_node_id,
                target_node_id=ec.target_node_id,
                label=ec.label,
                properties=ec.properties or {},
                source_id=ec.source_id,
            )
            self.session.add(db_edge)
            new_entities.append(db_edge)
            result_entities.append(db_edge)
            batch_seen[stable_id] = db_edge

        inserted_count = len(new_entities)

        if new_entities:
            self.session.maybe_commit()

        logger.info(
            "edges_batch_upserted",
            total=len(edge_creates),
            new=inserted_count,
            reused=len(edge_creates) - inserted_count,
        )
        return [self._db_edge_to_model(e) for e in result_entities], inserted_count

    def _db_edge_to_model(self, db_edge: GraphEdge) -> Edge:
        """Convert database edge to Pydantic model."""
        return Edge(
            id=db_edge.id,
            template_id=db_edge.template_id,
            source_node_id=db_edge.source_node_id,
            target_node_id=db_edge.target_node_id,
            label=db_edge.label,
            properties=db_edge.properties or {},
            created_at=db_edge.created_at,
            updated_at=db_edge.updated_at,
        )

    def _db_edge_to_model_minimal(self, db_edge: GraphEdge) -> Edge:
        """Convert database edge to Pydantic model with minimal fields.

        Used for graph canvas rendering where properties aren't needed.
        Timestamps use default values since they're not loaded in minimal mode.
        """
        return Edge(
            id=db_edge.id,
            template_id=db_edge.template_id,
            source_node_id=db_edge.source_node_id,
            target_node_id=db_edge.target_node_id,
            label=db_edge.label,
            properties={},  # Empty for performance
            # created_at and updated_at will use default_factory (datetime.now(UTC))
        )
