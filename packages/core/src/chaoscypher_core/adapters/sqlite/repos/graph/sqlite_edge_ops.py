# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Edge operations mixin for GraphRepository."""

import hashlib
from collections.abc import Sequence
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

import structlog
from sqlalchemy import func, or_
from sqlalchemy.orm import load_only
from sqlmodel import col, delete, select

from chaoscypher_core.adapters.sqlite.models import GraphEdge, GraphNode
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_membership_ops import (
    MembershipOperationsMixin,
)
from chaoscypher_core.adapters.sqlite.repos.source_visibility import (
    attested_by,
    in_source_scope,
    visible,
)
from chaoscypher_core.adapters.sqlite.utils import (
    entity_to_dict,
    id_batches,
    json_property_is_true,
    json_property_path,
)
from chaoscypher_core.exceptions import NotFoundError
from chaoscypher_core.models import Edge, EdgeCreate, EdgeUpdate, EdgeWithNodes
from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    INVERSE_OF_KEY,
    PIPELINE_EDGE_FLAG_KEYS,
)


logger = structlog.get_logger(__name__)


def _minimal_edge_properties(inverse_of: Any, flags: Sequence[Any]) -> dict[str, Any]:
    """A minimal edge's ``properties``: its pipeline flags as ``True``, then a synthetic inverse's ``inverse_of``."""
    properties: dict[str, Any] = {
        key: True for key, on in zip(PIPELINE_EDGE_FLAG_KEYS, flags, strict=True) if on
    }
    if isinstance(inverse_of, str) and inverse_of:
        properties[INVERSE_OF_KEY] = inverse_of
    return properties


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


# The columns an edge listing loads in full (every one: GraphEdge has no embedding to exclude),
# shared by list_edges and list_edges_by_node so the two read the same rows the same way.
_EDGE_COLUMNS = (
    GraphEdge.id,
    GraphEdge.database_name,
    GraphEdge.graph_name,
    GraphEdge.template_id,
    GraphEdge.source_node_id,
    GraphEdge.target_node_id,
    GraphEdge.label,
    GraphEdge.properties,
    GraphEdge.source_id,
    GraphEdge.person_made,
    # migration 0020's validity window: _db_edge_to_model reads all three, so a
    # projection without them lazy-loads each per edge (one SELECT per column per row).
    GraphEdge.valid_from,
    GraphEdge.valid_to,
    GraphEdge.closed_by_source_id,
    GraphEdge.created_at,
    GraphEdge.updated_at,
)


def _iso_date(value: str | None) -> date | None:
    """A stored ``YYYY-MM-DD`` as a date; None stays None."""
    return date.fromisoformat(value) if value else None


class EdgeOperationsMixin(MembershipOperationsMixin):
    """Mixin providing edge CRUD and batch operations for GraphRepository."""

    def create_edge(
        self, edge_create: EdgeCreate, custom_id: str | None = None, *, person_made: bool = False
    ) -> Edge:
        """Create a new edge in the graph; ``person_made`` marks a person's (migration 0025)."""
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
            person_made=person_made,
        )

        self.session.add(db_edge)
        self.attest_edges([db_edge])
        self.session.maybe_commit()
        self.session.refresh(db_edge)

        return self._db_edge_to_model(db_edge)

    def get_edge_by_ccx_iri(self, ccx_iri: str, database_name: str) -> dict | None:
        """Look up an edge by its stable CCX IRI.

        Returns the ORM-row dict (via ``entity_to_dict``) rather than an
        ``Edge`` Pydantic model so the ``ccx_iri`` column survives — the
        domain ``Edge`` model has no ``ccx_iri`` field. Scoped to
        ``(database_name, ccx_iri)``.

        Args:
            ccx_iri: The CCX 3.0 stable IRI to match.
            database_name: Database that owns the edge.

        Returns:
            The edge row dict (including ``ccx_iri``), or ``None`` if absent.
        """
        statement = select(GraphEdge).where(
            GraphEdge.ccx_iri == ccx_iri,
            GraphEdge.database_name == database_name,
        )
        db_edge = self.session.exec(statement).first()
        if db_edge is None:
            return None
        return entity_to_dict(db_edge)

    def upsert_edge_by_ccx_iri(
        self,
        ccx_iri: str,
        edge_create: EdgeCreate,
        database_name: str,
        source_id: str | None = None,
    ) -> dict:
        """Idempotently create or update an edge keyed by CCX IRI.

        Mirror of ``upsert_node_by_ccx_iri``: SELECT by
        ``(database_name, ccx_iri)``; if a row exists, UPDATE its
        ``label`` / ``properties`` (and endpoints, in case the importer
        re-resolved them) incoming-wins and return it (no duplicate);
        otherwise CREATE with the ``ccx_iri`` column set.

        ``EdgeCreate`` forbids extra fields and has no ``ccx_iri`` field, so
        the IRI is set directly on the ORM row. An explicit ``source_id``
        overrides ``edge_create.source_id`` when given.

        Args:
            ccx_iri: Stable CCX IRI used as the merge key.
            edge_create: Edge payload (template_id, endpoints, label,
                properties, ...).
            database_name: Database to scope the upsert to.
            source_id: Optional source id override for the created/updated row.

        Returns:
            The created or updated edge row dict (including ``ccx_iri``).
        """
        statement = select(GraphEdge).where(
            GraphEdge.ccx_iri == ccx_iri,
            GraphEdge.database_name == database_name,
        )
        db_edge = self.session.exec(statement).first()
        resolved_source_id = source_id if source_id is not None else edge_create.source_id

        if db_edge is not None:
            db_edge.label = edge_create.label
            db_edge.properties = edge_create.properties or {}
            db_edge.source_node_id = edge_create.source_node_id
            db_edge.target_node_id = edge_create.target_node_id
            db_edge.updated_at = datetime.now(UTC)
            if resolved_source_id is not None:
                db_edge.source_id = resolved_source_id
                self.attest_edges([db_edge])
            # no writer named: no membership touched (see upsert_node_by_ccx_iri);
            # the importer's edges name none and get no membership
            self.session.add(db_edge)
            self.session.maybe_commit()
            self.session.refresh(db_edge)
            updated = entity_to_dict(db_edge)
            assert updated is not None  # a non-None row always converts
            return updated

        db_edge = GraphEdge(
            id=self._generate_id("edge"),
            database_name=database_name,
            graph_name="knowledge",
            template_id=edge_create.template_id,
            source_node_id=edge_create.source_node_id,
            target_node_id=edge_create.target_node_id,
            label=edge_create.label,
            properties=edge_create.properties or {},
            source_id=resolved_source_id,
            ccx_iri=ccx_iri,
        )
        self.session.add(db_edge)
        self.attest_edges([db_edge])
        self.session.maybe_commit()
        self.session.refresh(db_edge)
        created = entity_to_dict(db_edge)
        assert created is not None  # a non-None row always converts
        return created

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

    def open_edges(self, node_id: str, label: str) -> list[Edge]:
        """The edges from ``node_id`` with ``label`` whose ``valid_to`` is unset."""
        statement = select(GraphEdge).where(
            GraphEdge.database_name == self.database_name,
            GraphEdge.source_node_id == node_id,
            GraphEdge.label == label,
            col(GraphEdge.valid_to).is_(None),
        )
        return [self._db_edge_to_model(e) for e in self.session.exec(statement).all()]

    def edges_from(self, node_id: str, label: str) -> list[Edge]:
        """Every edge from ``node_id`` with ``label``, open or closed."""
        statement = select(GraphEdge).where(
            GraphEdge.database_name == self.database_name,
            GraphEdge.source_node_id == node_id,
            GraphEdge.label == label,
        )
        return [self._db_edge_to_model(e) for e in self.session.exec(statement).all()]

    def set_edge_validity(
        self,
        edge_id: str,
        *,
        valid_from: date | None,
        valid_to: date | None,
        closed_by_source_id: str | None,
    ) -> None:
        """Write an edge's validity window; joins the caller's transaction."""
        db_edge = self.session.get(GraphEdge, edge_id)
        if db_edge is None or db_edge.database_name != self.database_name:
            return
        db_edge.valid_from = valid_from.isoformat() if valid_from else None
        db_edge.valid_to = valid_to.isoformat() if valid_to else None
        db_edge.closed_by_source_id = closed_by_source_id
        self.session.add(db_edge)
        self.session.maybe_commit()

    def edges_closed_by(self, source_id: str) -> list[Edge]:
        """The edges ``source_id`` closed (``closed_by_source_id``)."""
        statement = select(GraphEdge).where(
            GraphEdge.database_name == self.database_name,
            GraphEdge.closed_by_source_id == source_id,
        )
        return [self._db_edge_to_model(e) for e in self.session.exec(statement).all()]

    def reopen_edges_closed_by(self, source_id: str) -> int:
        """Reopen the edges ``source_id`` closed (``valid_to`` and ``closed_by_source_id`` unset); how many."""
        rows = self.session.exec(
            select(GraphEdge).where(
                GraphEdge.database_name == self.database_name,
                GraphEdge.closed_by_source_id == source_id,
            )
        ).all()
        for row in rows:
            row.valid_to = None
            row.closed_by_source_id = None
            self.session.add(row)
        if rows:
            self.session.flush()
        return len(rows)

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
                    GraphEdge.person_made,
                    # Excluded: properties, created_at, updated_at, graph_name
                )
            )
        else:
            statement = statement.options(load_only(*_EDGE_COLUMNS))

        if source_node_id is not None:
            statement = statement.where(GraphEdge.source_node_id == source_node_id)

        if target_node_id is not None:
            statement = statement.where(GraphEdge.target_node_id == target_node_id)

        if source_ids is not None:
            statement = statement.where(attested_by(GraphEdge, source_ids))

        if not include_disabled_sources:
            statement = self._from_enabled_sources(statement)

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

    def _from_enabled_sources(self, statement: Any) -> Any:
        """``statement`` narrowed to edges with no source (legacy, manual) or one an enabled source attests.

        The shared predicate (:func:`.source_visibility.visible`): a merged edge
        stays while any member source is on.
        """
        return statement.where(visible(GraphEdge))

    def list_edges_by_node(
        self,
        node_ids: Sequence[str],
        *,
        outgoing: bool,
        limit_per_node: int,
        include_disabled_sources: bool = False,
    ) -> dict[str, list[Edge]]:
        """Each node's edges out of it (``outgoing``) or into it, as ``list_edges`` lists them.

        For every id in ``node_ids``, exactly ``list_edges(source_node_id=id, limit=limit_per_node)``
        (``target_node_id`` when not ``outgoing``): the same filter, the first ``limit_per_node``
        edges by id, in id order, loaded in full. One windowed query per ``SQL_IN_BATCH`` ids
        instead of one per node: the graph context listed edges node by node, 9,248 queries for
        one retrieval sample run, 40% of its time (2026-10-16). Every id is a key, an edgeless
        node's list empty.
        """
        key = GraphEdge.source_node_id if outgoing else GraphEdge.target_node_id
        found: dict[str, list[Edge]] = {node_id: [] for node_id in node_ids}
        for batch in id_batches(node_ids):
            rank = func.row_number().over(partition_by=key, order_by=GraphEdge.id).label("rank")
            ranked = select(GraphEdge.id.label("edge_id"), rank).where(
                GraphEdge.database_name == self.database_name, col(key).in_(batch or [])
            )
            if not include_disabled_sources:
                ranked = self._from_enabled_sources(ranked)
            window = ranked.subquery()
            statement = (
                select(GraphEdge)
                .join(window, col(GraphEdge.id) == window.c.edge_id)
                .where(
                    GraphEdge.database_name == self.database_name, window.c.rank <= limit_per_node
                )
                .options(load_only(*_EDGE_COLUMNS))
                .order_by(key, GraphEdge.id)
            )
            for db_edge in self.session.exec(statement).all():
                node_id = db_edge.source_node_id if outgoing else db_edge.target_node_id
                found[node_id].append(self._db_edge_to_model(db_edge))
        return found

    def edges_with_true_properties(
        self,
        keys: Sequence[str],
        carry: Sequence[str] = (),
        ids: Sequence[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """The edges whose properties hold JSON ``true`` under any of ``keys`` (the port's contract).

        The edge counterpart of ``nodes_with_true_properties``: one query per
        ``ids`` batch (or one scan of this database's edges when ``ids`` is
        None) reading every key as a ``json_type(...) = 'true'`` column
        (``json_property_is_true``) and only the ``carry`` values into
        Python: ``true`` exactly, never the string ``"true"`` or the number 1.
        """
        if not keys:
            return {}
        flags = [json_property_is_true(GraphEdge.properties, key) for key in keys]
        carried = [func.json_extract(GraphEdge.properties, json_property_path(c)) for c in carry]
        out: dict[str, dict[str, Any]] = {}
        for batch in id_batches(ids):
            statement = select(GraphEdge.id, *flags, *carried).where(
                GraphEdge.database_name == self.database_name, or_(*flags)
            )
            if batch is not None:
                statement = statement.where(col(GraphEdge.id).in_(batch))
            for edge_id, *values in self.session.execute(statement).all():
                found: dict[str, Any] = {
                    key: True for key, hit in zip(keys, values[: len(keys)], strict=True) if hit
                }
                found.update(
                    (name, value)
                    for name, value in zip(carry, values[len(keys) :], strict=True)
                    if value is not None
                )
                out[str(edge_id)] = found
        return out

    def list_edges_minimal(
        self,
        limit: int = 10000,
        include_disabled_sources: bool = False,
        source_ids: list[str] | None = None,
    ) -> list[SimpleNamespace]:
        """List edges with minimal fields for analytics (fast).

        Only loads source_node_id, target_node_id, label - excludes properties,
        but for the pipeline's edge flags (``name_identity.PIPELINE_EDGE_FLAG_KEYS``,
        read in SQL) and a synthetic inverse's marker: ``properties`` holds
        each flag the edge carries as ``True`` and, on an inverse edge,
        ``inverse_of`` (``name_identity.INVERSE_OF_KEY``: the forward relation
        it mirrors), and nothing else. So the walk weighs a capped edge
        (``name_identity.edge_walk_weight``) and the graph-context walk folds
        an inverse onto its forward twin (``graphrag_blocks.walk_chains``)
        without loading the rest.
        Use this for analytics operations that don't need full edge data.

        Args:
            limit: Maximum number of results
            include_disabled_sources: If False (default), excludes edges from disabled sources
            source_ids: When set, restricts to edges whose SOURCE node a listed
                source attests (source-less nodes are kept, matching
                ``list_nodes_minimal``). Applied in SQL so the WHERE
                runs before ``limit``; endpoint-level scope (both ends in the
                loaded node set) remains the caller's responsibility.

        Returns:
            List of SimpleNamespace objects with minimal edge data

        """
        flag_columns = [
            json_property_is_true(GraphEdge.properties, key).label(f"flag_{i}")
            for i, key in enumerate(PIPELINE_EDGE_FLAG_KEYS)
        ]
        inverse_column = func.json_extract(
            GraphEdge.properties, json_property_path(INVERSE_OF_KEY)
        ).label("inverse_of")
        # Plain column select: rows, not ORM entities, so a 200k-edge PPR load
        # skips entity construction + identity-map registration per row.
        statement = select(
            GraphEdge.id,
            GraphEdge.source_node_id,
            GraphEdge.target_node_id,
            GraphEdge.label,
            GraphEdge.template_id,
            inverse_column,
            *flag_columns,
        ).where(GraphEdge.database_name == self.database_name)

        # Both the enabled filter and the source scope read the source node;
        # join it once.
        if not include_disabled_sources or source_ids is not None:
            statement = statement.join(GraphNode, GraphEdge.source_node_id == GraphNode.id)

        # Filter by source enabled status via source node
        if not include_disabled_sources:
            statement = statement.where(visible(GraphNode))

        if source_ids is not None:
            statement = statement.where(in_source_scope(GraphNode, source_ids))

        # Ordered by id like list_edges: a LIMIT with no ORDER BY hands the query
        # planner the choice of which rows to keep (audit wave 4).
        statement = statement.order_by(GraphEdge.id).limit(limit)
        rows = self.session.exec(statement).all()

        return [
            SimpleNamespace(
                id=row[0],
                source_node_id=row[1],
                target_node_id=row[2],
                label=row[3],
                template_id=row[4],
                properties=_minimal_edge_properties(row[5], row[6:]),
            )
            for row in rows
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

    def repoint_edge(self, edge_id: str, *, source_node_id: str, target_node_id: str) -> None:
        """Move an edge to new ends in place: its id, ``created_at`` and ``ccx_iri`` stay.

        A merge moves a person's edge this way (it is attached, never written again).
        Joins the caller's transaction: nothing is committed here.

        Raises:
            NotFoundError: No edge of this database has the id.
        """
        row = self.session.exec(
            select(GraphEdge).where(
                GraphEdge.id == edge_id, GraphEdge.database_name == self.database_name
            )
        ).first()
        if row is None:
            raise NotFoundError("Edge", edge_id)
        row.source_node_id = source_node_id
        row.target_node_id = target_node_id
        row.updated_at = datetime.now(UTC)
        self.session.add(row)
        self.session.flush()

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

        self.attest_edges(created_edges)

        # Convert to models BEFORE commit — all fields are set from
        # constructor values and accessible without refresh. This avoids
        # N individual SELECT queries from session.refresh() per edge.
        result = [self._db_edge_to_model(db_edge) for db_edge in created_edges]

        self.session.maybe_commit()

        logger.info("edges_batch_created", count=len(result))
        return result

    async def upsert_edges_batch(
        self, edge_creates: list[EdgeCreate], *, reattested: set[str] | None = None
    ) -> tuple[list[Edge], int]:
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
        does NOT overwrite. A reused row the incoming source does not
        attest gets its membership back, as ``upsert_nodes_batch`` does
        (``reattest_edges``), and its id joins ``reattested``.

        Args:
            edge_creates: List of EdgeCreate objects, each with
                source_node_id / target_node_id already stable from
                the prior upsert_nodes_batch call.
            reattested: Filled with the ids of reused rows that gained the
                incoming source's membership.

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
        reused: list[tuple[str, EdgeCreate]] = []
        for stable_id, ec in zip(stable_ids, edge_creates, strict=True):
            if stable_id in existing_rows:
                result_entities.append(existing_rows[stable_id])
                reused.append((stable_id, ec))
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

        gained = self.reattest_edges(reused)
        if reattested is not None:
            reattested.update(gained)
        if new_entities:
            self.attest_edges(new_entities)
        if new_entities or gained:
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
            source_id=db_edge.source_id,
            person_made=bool(db_edge.person_made),
            valid_from=_iso_date(db_edge.valid_from),
            valid_to=_iso_date(db_edge.valid_to),
            closed_by_source_id=db_edge.closed_by_source_id,
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
            source_id=db_edge.source_id,
            person_made=bool(db_edge.person_made),
            # created_at and updated_at will use default_factory (datetime.now(UTC))
        )
