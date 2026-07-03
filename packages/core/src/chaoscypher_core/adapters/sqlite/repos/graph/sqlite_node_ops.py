# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Node operations mixin for GraphRepository."""

import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace

import structlog
from sqlalchemy.orm import load_only
from sqlmodel import col, delete, select

from chaoscypher_core.adapters.sqlite.models import GraphEdge, GraphNode, SourceRow
from chaoscypher_core.adapters.sqlite.repos.graph.graph_mixin_base import GraphMixinBase
from chaoscypher_core.adapters.sqlite.utils import entity_to_dict
from chaoscypher_core.models import Node, NodeCreate, NodePosition, NodeUpdate


logger = structlog.get_logger(__name__)


def _stable_node_id(
    *,
    database_name: str,
    source_id: str | None,
    template_id: str,
    label: str,
) -> str:
    """Derive a content-addressed node ID from commit-time inputs.

    The commit phase needs a deterministic primary key so re-dispatch
    after a crash is idempotent — hashing the (database, source,
    template, canonical_label) tuple gives us that without requiring
    a schema change to add a separate stable_key column.

    Label is normalized with strip+lower so tiny whitespace/case drift
    between extraction runs (which DOES happen — the extractor occasionally
    emits "Albert Einstein" once and " albert einstein " another time)
    doesn't break dedup.

    The ``node_`` prefix keeps the ID shape consistent with the rest
    of the codebase's ``generate_id("node")`` output, so any string
    prefix checks (UI filters, log grepping, etc.) keep working.

    Args:
        database_name: Active database (scopes keys to a single DB).
        source_id: Source the node belongs to. Nodes without a
            source_id (manual/legacy) fall back to the literal
            "no_source" sentinel — those are outside the resumability
            story, which is always source-scoped.
        template_id: Template this node instantiates.
        label: Human-readable label; normalized before hashing.

    Returns:
        A deterministic string of the form ``node_<24-hex-chars>``.
    """
    canonical_label = (label or "").strip().lower()
    scope_source = source_id or "no_source"
    raw = f"{database_name}:{scope_source}:{template_id}:{canonical_label}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"node_{digest}"


class NodeOperationsMixin(GraphMixinBase):
    """Mixin providing node CRUD operations for GraphRepository."""

    # ========================================================================
    # Node Operations
    # ========================================================================

    def create_node(self, node_create: NodeCreate, custom_id: str | None = None) -> Node:
        """Create a new node in the graph."""
        node_id = custom_id or self._generate_id("node")
        graph_name = self._get_graph_name_for_template(node_create.template_id)

        # Create SQLModel entity
        db_node = GraphNode(
            id=node_id,
            database_name=self.database_name,
            graph_name=graph_name,
            template_id=node_create.template_id,
            label=node_create.label,
            entity_type=node_create.entity_type,
            properties=node_create.properties or {},
            position_x=node_create.position.x if node_create.position else None,
            position_y=node_create.position.y if node_create.position else None,
            embedding=node_create.embedding,
            source_id=node_create.source_id,
        )

        self.session.add(db_node)
        self.session.maybe_commit()
        self.session.refresh(db_node)

        # Convert to Pydantic model
        return self._db_node_to_model(db_node)

    def get_node_by_ccx_iri(self, ccx_iri: str, database_name: str) -> dict | None:
        """Look up a node by its stable CCX IRI.

        Returns the ORM-row dict (via ``entity_to_dict``) rather than a
        ``Node`` Pydantic model so the ``ccx_iri`` column survives — the
        domain ``Node`` model has no ``ccx_iri`` field. Scoped to
        ``(database_name, ccx_iri)`` for multi-database isolation.

        Args:
            ccx_iri: The CCX 3.0 stable IRI to match.
            database_name: Database that owns the node.

        Returns:
            The node row dict (including ``ccx_iri``), or ``None`` if no row
            in ``database_name`` carries that IRI.
        """
        statement = select(GraphNode).where(
            GraphNode.ccx_iri == ccx_iri,
            GraphNode.database_name == database_name,
        )
        db_node = self.session.exec(statement).first()
        if db_node is None:
            return None
        return entity_to_dict(db_node)

    def upsert_node_by_ccx_iri(
        self,
        ccx_iri: str,
        node_create: NodeCreate,
        database_name: str,
        source_id: str | None = None,
    ) -> dict:
        """Idempotently create or update a node keyed by CCX IRI.

        The upsert primitive the CCX 3.0 importer relies on: SELECT by
        ``(database_name, ccx_iri)``; if a row exists, UPDATE its
        ``label`` / ``properties`` / ``entity_type`` (incoming-wins) and
        return it (no duplicate); otherwise CREATE a new node with the
        ``ccx_iri`` column set to the given value.

        ``NodeCreate`` forbids extra fields and has no ``ccx_iri`` field, so
        the IRI is written directly onto the ORM row here rather than passed
        through the DTO. An explicit ``source_id`` overrides
        ``node_create.source_id`` when given (the importer resolves the
        source row separately from the node payload).

        Args:
            ccx_iri: Stable CCX IRI used as the merge key.
            node_create: Node payload (template_id, label, entity_type,
                properties, ...).
            database_name: Database to scope the upsert to.
            source_id: Optional source id override for the created/updated row.

        Returns:
            The created or updated node row dict (including ``ccx_iri``).
        """
        statement = select(GraphNode).where(
            GraphNode.ccx_iri == ccx_iri,
            GraphNode.database_name == database_name,
        )
        db_node = self.session.exec(statement).first()
        resolved_source_id = source_id if source_id is not None else node_create.source_id

        if db_node is not None:
            db_node.label = node_create.label
            db_node.properties = node_create.properties or {}
            db_node.entity_type = node_create.entity_type
            if resolved_source_id is not None:
                db_node.source_id = resolved_source_id
            db_node.updated_at = datetime.now(UTC)
            self.session.add(db_node)
            self.session.maybe_commit()
            self.session.refresh(db_node)
            result = entity_to_dict(db_node)
            assert result is not None  # a non-None row always converts
            return result

        db_node = GraphNode(
            id=self._generate_id("node"),
            database_name=database_name,
            graph_name=self._get_graph_name_for_template(node_create.template_id),
            template_id=node_create.template_id,
            label=node_create.label,
            entity_type=node_create.entity_type,
            properties=node_create.properties or {},
            position_x=node_create.position.x if node_create.position else None,
            position_y=node_create.position.y if node_create.position else None,
            embedding=node_create.embedding,
            source_id=resolved_source_id,
            ccx_iri=ccx_iri,
        )
        self.session.add(db_node)
        self.session.maybe_commit()
        self.session.refresh(db_node)
        created = entity_to_dict(db_node)
        assert created is not None  # a non-None row always converts
        return created

    def assign_source_to_nodes(
        self,
        node_ids: list[str],
        source_id: str,
        database_name: str,
    ) -> int:
        """Back-fill ``source_id`` on a batch of nodes that lack one.

        The CCX importer creates nodes before it knows which source cites
        them (the node->source link lives in the citation records, which are
        imported last), so it stamps ``graph_nodes.source_id`` here once
        citations resolve. Restoring the link keeps source-scoped node
        queries correct and lets ``ON DELETE CASCADE`` remove an imported
        source's nodes on re-import. Nodes that already carry a source id are
        left untouched (first link wins), so this is safe to call per source.

        It also re-points each node's denormalized ``source_document_id``
        property to ``source_id``. An imported node's bundle property holds the
        ORIGINAL export-machine source id — stale, pointing at no local source —
        so syncing it to the local source id makes imported nodes consistent
        with extracted ones for any consumer that reads the property.

        Returns the number of node rows updated.
        """
        if not node_ids:
            return 0
        statement = select(GraphNode).where(
            col(GraphNode.id).in_(node_ids),
            GraphNode.database_name == database_name,
            col(GraphNode.source_id).is_(None),
        )
        changed = 0
        now = datetime.now(UTC)
        for node in self.session.exec(statement).all():
            node.source_id = source_id
            props = dict(node.properties) if node.properties else {}
            props["source_document_id"] = source_id
            node.properties = props
            node.updated_at = now
            self.session.add(node)
            changed += 1
        if changed:
            self.session.maybe_commit()
        return changed

    def update_node_embeddings_batch(self, embeddings: dict[str, list[float]]) -> int:
        """Persist embeddings for many nodes in one transaction; rows updated.

        Re-embedding an imported source touches every node, so calling
        ``update_node`` per node (a SELECT + UPDATE + commit each) is N
        round-trips. This loads the batch and commits once. ``graph_nodes.
        embedding`` is a JSON ``list[float]`` column (NOT base64), so the vector
        is written as-is.
        """
        if not embeddings:
            return 0
        statement = select(GraphNode).where(
            col(GraphNode.id).in_(list(embeddings)),
            GraphNode.database_name == self.database_name,
        )
        changed = 0
        now = datetime.now(UTC)
        for node in self.session.exec(statement).all():
            vector = embeddings.get(node.id)
            if vector is None:
                continue
            node.embedding = vector
            node.updated_at = now
            self.session.add(node)
            changed += 1
        if changed:
            self.session.maybe_commit()
        return changed

    def get_node(self, node_id: str) -> Node | None:
        """Get a node by ID."""
        statement = select(GraphNode).where(
            GraphNode.id == node_id,
            GraphNode.database_name == self.database_name,
        )
        db_node = self.session.exec(statement).first()

        if db_node is None:
            return None

        return self._db_node_to_model(db_node)

    def list_nodes(
        self,
        template_id: str | None = None,
        source_ids: list[str] | None = None,
        skip: int = 0,
        limit: int = 100,
        include_disabled_sources: bool = False,
        minimal: bool = False,
        include_embedding: bool = True,
    ) -> list[Node]:
        """List all nodes, optionally filtered by template, source, and enabled status.

        Args:
            template_id: Optional template ID filter
            source_ids: Optional list of source document IDs to filter by
            skip: Number of results to skip
            limit: Maximum number of results
            include_disabled_sources: If False (default), excludes nodes from disabled sources
            minimal: If True, only load essential fields (excludes embedding, properties)
                     for better performance with large graphs
            include_embedding: If True (default), the embedding vector is loaded in
                     the same query. List/display callers that never read embeddings
                     should pass False: it keeps the column deferred *and* stops the
                     converter from touching it, avoiding a lazy SELECT per row
                     (N+1) plus the wasted payload. Ignored when minimal=True
                     (minimal never loads embeddings).

        Returns:
            List of nodes matching the filters

        """
        statement = select(GraphNode).where(GraphNode.database_name == self.database_name)

        # Apply load_only to exclude large columns (embedding BLOB, properties JSON)
        if minimal:
            statement = statement.options(
                load_only(
                    GraphNode.id,
                    GraphNode.database_name,
                    GraphNode.template_id,
                    GraphNode.label,
                    GraphNode.entity_type,
                    GraphNode.position_x,
                    GraphNode.position_y,
                    GraphNode.source_id,
                    # Excluded: properties, embedding, created_at, updated_at, graph_name
                )
            )
        else:
            full_columns = [
                GraphNode.id,
                GraphNode.database_name,
                GraphNode.graph_name,
                GraphNode.template_id,
                GraphNode.label,
                GraphNode.entity_type,
                GraphNode.properties,
                GraphNode.position_x,
                GraphNode.position_y,
                GraphNode.source_id,
                GraphNode.created_at,
                GraphNode.updated_at,
            ]
            # Load the embedding eagerly in this single query only when a caller
            # actually needs it. Otherwise it stays deferred and is never read,
            # so SQLAlchemy issues no extra per-row SELECT (the embedding N+1).
            if include_embedding:
                full_columns.append(GraphNode.embedding)
            statement = statement.options(load_only(*full_columns))

        if template_id is not None:
            statement = statement.where(GraphNode.template_id == template_id)

        if source_ids is not None:
            statement = statement.where(col(GraphNode.source_id).in_(source_ids))

        # Filter by source enabled status
        if not include_disabled_sources:
            # Include nodes with NULL source_id (legacy/manual nodes) OR enabled sources
            statement = statement.outerjoin(SourceRow, GraphNode.source_id == SourceRow.id).where(
                (GraphNode.source_id.is_(None)) | (SourceRow.enabled == True)  # noqa: E712
            )

        statement = statement.order_by(GraphNode.id).offset(skip).limit(limit)
        db_nodes = self.session.exec(statement).all()

        if minimal:
            return [self._db_node_to_model_minimal(n) for n in db_nodes]
        return [self._db_node_to_model(n, include_embedding=include_embedding) for n in db_nodes]

    def list_nodes_minimal(
        self,
        limit: int = 10000,
        include_disabled_sources: bool = False,
    ) -> list[SimpleNamespace]:
        """List nodes with minimal fields for analytics (fast).

        Only loads id, label, template_id - excludes embedding, properties, etc.
        Use this for analytics operations that don't need full node data.

        Args:
            limit: Maximum number of results
            include_disabled_sources: If False (default), excludes nodes from disabled sources

        Returns:
            List of SimpleNamespace objects with minimal node data (id, label, template_id)

        """
        statement = (
            select(GraphNode)
            .options(
                load_only(
                    GraphNode.id,
                    GraphNode.label,
                    GraphNode.template_id,
                    GraphNode.source_id,  # Needed for join
                )
            )
            .where(GraphNode.database_name == self.database_name)
        )

        # Filter by source enabled status
        if not include_disabled_sources:
            statement = statement.outerjoin(SourceRow, GraphNode.source_id == SourceRow.id).where(
                (GraphNode.source_id.is_(None)) | (SourceRow.enabled == True)  # noqa: E712
            )

        statement = statement.limit(limit)
        db_nodes = self.session.exec(statement).all()

        return [
            SimpleNamespace(
                id=n.id, label=n.label, template_id=n.template_id, source_id=n.source_id
            )
            for n in db_nodes
        ]

    def list_nodes_without_embeddings(self, limit: int = 10000) -> list[Node]:
        """List nodes that have no embedding vector (NULL or empty JSON array).

        Used by embedding generation to find nodes needing processing without
        loading the full node table with existing embeddings.

        Args:
            limit: Maximum number of results.

        Returns:
            List of nodes missing embeddings (minimal fields plus label for text).

        """
        statement = (
            select(GraphNode)
            .options(
                load_only(
                    GraphNode.id,
                    GraphNode.database_name,
                    GraphNode.template_id,
                    GraphNode.label,
                    GraphNode.source_id,
                    GraphNode.properties,
                )
            )
            .where(
                GraphNode.database_name == self.database_name,
                (GraphNode.embedding.is_(None)) | (GraphNode.embedding == "[]"),  # type: ignore[union-attr,comparison-overlap]  # SQLAlchemy column comparison; mypy treats Mapped[list[float]|None] literally
            )
            .limit(limit)
        )
        db_nodes = self.session.exec(statement).all()
        return [self._db_node_to_model(n) for n in db_nodes]

    def update_node(self, node_id: str, node_update: NodeUpdate) -> Node | None:
        """Update an existing node."""
        statement = select(GraphNode).where(
            GraphNode.id == node_id,
            GraphNode.database_name == self.database_name,
        )
        db_node = self.session.exec(statement).first()

        if db_node is None:
            return None

        # Update fields
        if node_update.label is not None:
            db_node.label = node_update.label

        if node_update.properties is not None:
            db_node.properties = node_update.properties

        if node_update.position is not None:
            db_node.position_x = node_update.position.x
            db_node.position_y = node_update.position.y

        if node_update.embedding is not None:
            db_node.embedding = node_update.embedding

        db_node.updated_at = datetime.now(UTC)

        self.session.add(db_node)
        self.session.maybe_commit()
        self.session.refresh(db_node)

        return self._db_node_to_model(db_node)

    def delete_node(self, node_id: str) -> bool:
        """Delete a node by ID.

        Also deletes all edges that reference this node.
        """
        statement = select(GraphNode).where(
            GraphNode.id == node_id,
            GraphNode.database_name == self.database_name,
        )
        db_node = self.session.exec(statement).first()

        if db_node is None:
            return False

        # Delete edges referencing this node (cascade)
        edge_delete = delete(GraphEdge).where(
            GraphEdge.database_name == self.database_name,
            (GraphEdge.source_node_id == node_id) | (GraphEdge.target_node_id == node_id),
        )
        self.session.exec(edge_delete)

        # Delete the node
        self.session.delete(db_node)
        self.session.maybe_commit()

        return True

    def delete_nodes_batch(self, node_ids: list[str]) -> dict:
        """Batch delete multiple nodes with cascade edge deletion."""
        deleted_count = 0
        not_found = []

        # Find which nodes exist
        statement = select(GraphNode).where(
            GraphNode.database_name == self.database_name,
            col(GraphNode.id).in_(node_ids),
        )
        existing_nodes = self.session.exec(statement).all()
        existing_ids = {n.id for n in existing_nodes}

        not_found = [nid for nid in node_ids if nid not in existing_ids]

        if existing_ids:
            # Delete edges referencing any of these nodes
            edge_delete = delete(GraphEdge).where(
                GraphEdge.database_name == self.database_name,
                (col(GraphEdge.source_node_id).in_(existing_ids))
                | (col(GraphEdge.target_node_id).in_(existing_ids)),
            )
            result = self.session.exec(edge_delete)
            edges_deleted = result.rowcount if hasattr(result, "rowcount") else 0

            # Delete the nodes
            node_delete = delete(GraphNode).where(
                GraphNode.database_name == self.database_name,
                col(GraphNode.id).in_(existing_ids),
            )
            self.session.exec(node_delete)
            deleted_count = len(existing_ids)

            self.session.maybe_commit()
        else:
            edges_deleted = 0

        logger.info(
            "nodes_batch_deleted_with_edges",
            nodes_deleted=deleted_count,
            edges_deleted=edges_deleted,
            not_found_count=len(not_found),
            total_requested=len(node_ids),
        )

        return {
            "nodes_deleted": deleted_count,
            "edges_deleted": edges_deleted,
            "not_found": not_found,
            "errors": [],
        }

    def get_nodes_batch(self, node_ids: list[str]) -> list[Node]:
        """Get multiple nodes by ID in a single operation."""
        if not node_ids:
            return []

        statement = select(GraphNode).where(
            GraphNode.database_name == self.database_name,
            col(GraphNode.id).in_(node_ids),
        )
        db_nodes = self.session.exec(statement).all()

        return [self._db_node_to_model(n) for n in db_nodes]

    def update_node_position(self, node_id: str, x: float, y: float) -> Node | None:
        """Update only the node's position (optimized for layout saving)."""
        statement = select(GraphNode).where(
            GraphNode.id == node_id,
            GraphNode.database_name == self.database_name,
        )
        db_node = self.session.exec(statement).first()

        if db_node is None:
            return None

        db_node.position_x = x
        db_node.position_y = y
        # Note: Don't update updated_at for position-only changes

        self.session.add(db_node)
        self.session.maybe_commit()
        self.session.refresh(db_node)

        return self._db_node_to_model(db_node)

    async def create_nodes_batch(self, node_creates: list[NodeCreate]) -> list[Node]:
        """Create multiple nodes in a single batch operation."""
        if not node_creates:
            return []

        created_nodes = []

        for node_create in node_creates:
            node_id = self._generate_id("node")
            graph_name = self._get_graph_name_for_template(node_create.template_id)

            db_node = GraphNode(
                id=node_id,
                database_name=self.database_name,
                graph_name=graph_name,
                template_id=node_create.template_id,
                label=node_create.label,
                entity_type=node_create.entity_type,
                properties=node_create.properties or {},
                position_x=node_create.position.x if node_create.position else None,
                position_y=node_create.position.y if node_create.position else None,
                embedding=node_create.embedding,
                source_id=node_create.source_id,
            )
            self.session.add(db_node)
            created_nodes.append(db_node)

        # Convert to models BEFORE commit — all fields are set from
        # constructor values and accessible without refresh. This avoids
        # N individual SELECT queries from session.refresh() per node.
        result = [self._db_node_to_model(db_node) for db_node in created_nodes]

        self.session.maybe_commit()

        logger.info("nodes_batch_created", count=len(result))
        return result

    async def upsert_nodes_batch(self, node_creates: list[NodeCreate]) -> tuple[list[Node], int]:
        """Idempotently create graph nodes keyed by content hash.

        Every node gets a deterministic primary key derived from
        ``(database_name, source_id, template_id, normalized label)``.
        A second call with the same NodeCreate list observes the
        existing rows via a bulk SELECT-by-id and leaves them
        untouched. Only genuinely new stable keys are inserted.

        This is the commit-path entry point for resumability:
        if the commit handler crashes after writing half its nodes
        and is re-dispatched, the second attempt sees the already-
        written rows, skips them, and lands the remainder without
        creating duplicates.

        Semantics detail: when a stable key already exists, this
        method deliberately does NOT update the stored row — first
        write wins. The extractor's output for the same source should
        be stable enough that re-running doesn't produce diverging
        properties, and preserving the first write prevents a partial
        re-run from silently clobbering already-committed state.

        Args:
            node_creates: List of NodeCreate objects, each carrying
                at least template_id, label, and source_id.

        Returns:
            Tuple of:
            - List of Node Pydantic models in the same order as the
              input, with stable .id values (includes pre-existing).
            - Count of rows actually inserted (not counting dedup
              reuses). Use this for ``commit_nodes_created``.
        """
        if not node_creates:
            return [], 0

        # Step 1: compute stable keys for every input
        stable_ids: list[str] = [
            _stable_node_id(
                database_name=self.database_name,
                source_id=nc.source_id,
                template_id=nc.template_id,
                label=nc.label,
            )
            for nc in node_creates
        ]

        # Step 2: single bulk SELECT for all pre-existing stable keys
        existing_rows: dict[str, GraphNode] = {}
        if stable_ids:
            existing_stmt = select(GraphNode).where(
                GraphNode.database_name == self.database_name,
                col(GraphNode.id).in_(stable_ids),
            )
            for row in self.session.scalars(existing_stmt).all():
                existing_rows[row.id] = row

        # Step 3: insert only the stable keys that don't yet exist,
        # preserving input order for the returned Node list
        new_entities: list[GraphNode] = []
        result_entities: list[GraphNode] = []
        batch_seen: dict[str, GraphNode] = {}
        for stable_id, nc in zip(stable_ids, node_creates, strict=True):
            if stable_id in existing_rows:
                result_entities.append(existing_rows[stable_id])
                continue
            if stable_id in batch_seen:
                result_entities.append(batch_seen[stable_id])
                continue
            graph_name = self._get_graph_name_for_template(nc.template_id)
            db_node = GraphNode(
                id=stable_id,
                database_name=self.database_name,
                graph_name=graph_name,
                template_id=nc.template_id,
                label=nc.label,
                entity_type=nc.entity_type,
                properties=nc.properties or {},
                position_x=nc.position.x if nc.position else None,
                position_y=nc.position.y if nc.position else None,
                embedding=nc.embedding,
                source_id=nc.source_id,
            )
            self.session.add(db_node)
            new_entities.append(db_node)
            result_entities.append(db_node)
            batch_seen[stable_id] = db_node

        inserted_count = len(new_entities)

        if new_entities:
            self.session.maybe_commit()

        logger.info(
            "nodes_batch_upserted",
            total=len(node_creates),
            new=inserted_count,
            reused=len(node_creates) - inserted_count,
        )
        return [self._db_node_to_model(e) for e in result_entities], inserted_count

    def _db_node_to_model(self, db_node: GraphNode, *, include_embedding: bool = True) -> Node:
        """Convert database node to Pydantic model.

        ``include_embedding=False`` skips reading ``db_node.embedding`` entirely.
        When the list query left that column deferred, merely touching it would
        trigger a lazy per-row SELECT, so the caller must opt out here too.
        """
        position = None
        if db_node.position_x is not None and db_node.position_y is not None:
            position = NodePosition(x=db_node.position_x, y=db_node.position_y)

        return Node(
            id=db_node.id,
            template_id=db_node.template_id,
            label=db_node.label,
            entity_type=db_node.entity_type,
            properties=db_node.properties or {},
            position=position,
            embedding=db_node.embedding if include_embedding else None,
            created_at=db_node.created_at,
            updated_at=db_node.updated_at,
            source_id=db_node.source_id,
        )

    def _db_node_to_model_minimal(self, db_node: GraphNode) -> Node:
        """Convert database node to Pydantic model with minimal fields.

        Used for graph canvas rendering where properties/embedding aren't needed.
        Timestamps use default values since they're not loaded in minimal mode.
        """
        position = None
        if db_node.position_x is not None and db_node.position_y is not None:
            position = NodePosition(x=db_node.position_x, y=db_node.position_y)

        return Node(
            id=db_node.id,
            template_id=db_node.template_id,
            label=db_node.label,
            entity_type=db_node.entity_type,
            properties={},  # Empty for performance
            position=position,
            embedding=None,  # Not loaded
            source_id=db_node.source_id,
            # created_at and updated_at will use default_factory (datetime.now(UTC))
        )
