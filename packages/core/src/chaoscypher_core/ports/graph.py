# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Graph repository interface for chaoscypher-engine.

Defines Protocol for graph data access (nodes, edges, templates).
Main app implements this via an adapter class that wraps its GraphRepository.
"""

from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Protocol


if TYPE_CHECKING:
    from contextlib import AbstractContextManager
    from datetime import date

    from chaoscypher_core.models import (
        Edge,
        EdgeCreate,
        EdgeUpdate,
        EdgeWithNodes,
        Node,
        NodeCreate,
        NodeUpdate,
        ReleasedRows,
        Template,
        TemplateCreate,
        TemplateUpdate,
    )


class GraphRepositoryProtocol(Protocol):
    """Interface for knowledge graph operations.

    Implementations provide access to the knowledge graph
    for node, edge, and template operations.

    Protocol-based design allows any class with matching methods
    to satisfy this interface (structural typing).
    """

    # ==================== Node Operations ====================

    def get_node(self, node_id: str, *, include_embedding: bool = True) -> Node | None:
        """Get a node by ID.

        Args:
            node_id: Unique node identifier
            include_embedding: Default True. False leaves the 1024-float
                embedding out of the query (the ``get_nodes_batch``
                projection contract); existence checks must pass False.

        Returns:
            Node object or None if not found

        Example:
            node = graph_repo.get_node("person_123")
            if node:
                print(f"Found node: {node.label}")

        """
        ...

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
            template_id: Optional template ID to filter by (None = all nodes)
            source_ids: Optional list of source document IDs to filter by
            skip: Number of results to skip (for pagination)
            limit: Maximum number of results to return (default 100)
            include_disabled_sources: If False (default), excludes nodes from disabled sources
            minimal: If True, only load essential fields
            include_embedding: If True (default), embeddings are loaded with the
                nodes. Display/list callers that never read embeddings should pass
                False to avoid loading and serializing them. Ignored when minimal=True.

        Returns:
            List of Node objects (empty list if none found)

        Example:
            # Get all nodes
            all_nodes = graph_repo.list_nodes()

            # Get nodes of specific type
            people = graph_repo.list_nodes(template_id="person")

            # Get nodes from specific sources
            source_nodes = graph_repo.list_nodes(source_ids=["src_1", "src_2"])

        """
        ...

    def nodes_with_true_properties(
        self, keys: Sequence[str], ids: Sequence[str] | None = None
    ) -> dict[str, list[str]]:
        """The nodes whose properties hold JSON ``true`` under any of ``keys``.

        For a projection that loads no properties (the canvas's ``minimal``
        listing) and must still show the pipeline's flags
        (``name_identity.PIPELINE_FLAG_KEYS``). ``true`` exactly: a string
        ``"true"`` or a number is no flag.

        Args:
            keys: Top-level property keys.
            ids: When set, only these nodes are read (the ids a capped,
                source-scoped listing returned), so the cost follows what
                is shown, not the whole graph. None reads every node.

        Returns:
            Node id -> the keys it holds ``true`` under, in ``keys`` order;
            nodes holding none are absent.
        """
        ...

    def edges_with_true_properties(
        self,
        keys: Sequence[str],
        carry: Sequence[str] = (),
        ids: Sequence[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """The edges whose properties hold JSON ``true`` under any of ``keys``, with ``carry`` values.

        The edge counterpart of :meth:`nodes_with_true_properties`, for the
        projections that load no edge properties (the canvas's ``minimal``
        listing, PageRank's ``list_edges_minimal``) and must still read the
        pipeline's edge flags (``name_identity.PIPELINE_EDGE_FLAG_KEYS``: a
        capped edge is drawn thin, an unevidenced one dashed). ``true``
        exactly: a string ``"true"`` or a number is no flag.

        Args:
            keys: Top-level property keys read as flags.
            carry: Top-level property keys whose values a flagged edge also
                returns when it holds them (the canvas shows
                ``evidence_reason`` beside ``unevidenced``).
            ids: When set, only these edges are read (as for
                :meth:`nodes_with_true_properties`). None reads every edge.

        Returns:
            Edge id -> ``{key: True}`` for each flag it holds, in ``keys``
            order, plus each ``carry`` key it holds; edges holding no flag
            are absent.
        """
        ...

    def create_node(
        self, node_create: NodeCreate, custom_id: str | None = None, *, person_made: bool = False
    ) -> Node:
        """Create a new node in the graph.

        Args:
            node_create: Node creation data
            custom_id: The row's id; None generates one.
            person_made: A person makes it (the create tools, the API, the SDK, the
                CLI): no release deletes or rewrites it (migration 0025). Never request
                input: only the person paths pass it.

        Returns:
            Created Node object with generated ID

        Note:
            This is a raw write: implementations do not validate that
            ``template_id`` exists or that required properties are set.
            Use ``NodeService.create_node`` for validated creation (it
            raises ``NotFoundError``). On the SQLite adapter a dangling
            ``template_id`` surfaces as the backend's
            ``sqlalchemy.exc.IntegrityError``, not ``ValueError``.

        Example:
            from chaoscypher_core.models import NodeCreate

            node_create = NodeCreate(
                template_id="person",
                label="Alice Smith",
                properties={"age": 30, "email": "alice@example.com"}
            )
            created_node = graph_repo.create_node(node_create)
            print(f"Created node with ID: {created_node.id}")

        """
        ...

    def get_node_by_ccx_iri(self, ccx_iri: str, database_name: str) -> dict[str, Any] | None:
        """Look up a node by its stable CCX IRI.

        Returns the ORM-row dict (so the ``ccx_iri`` column survives — the
        ``Node`` model has no such field) or ``None`` when no row in
        ``database_name`` carries that IRI. The lookup primitive the CCX 3.0
        importer uses for upsert-by-IRI.

        Args:
            ccx_iri: The CCX 3.0 stable IRI to match.
            database_name: Database that owns the node.

        """
        ...

    def upsert_node_by_ccx_iri(
        self,
        ccx_iri: str,
        node_create: NodeCreate,
        database_name: str,
        source_id: str | None = None,
    ) -> dict[str, Any]:
        """Idempotently create or update a node keyed by CCX IRI.

        SELECT by ``(database_name, ccx_iri)``; update the existing row's
        label / properties / entity_type when found (incoming-wins, no
        duplicate), else create a new node carrying the given ``ccx_iri``.
        ``NodeCreate`` has no ``ccx_iri`` field, so the IRI is set on the ORM
        row directly. Used by the CCX 3.0 importer for idempotent re-import.

        Args:
            ccx_iri: Stable CCX IRI used as the merge key.
            node_create: Node payload (template_id, label, entity_type, ...).
            database_name: Database to scope the upsert to.
            source_id: Optional source id override for the created/updated row.

        Returns:
            The created or updated node row dict (including ``ccx_iri``).
        """
        ...

    def assign_source_to_nodes(
        self,
        node_ids: list[str],
        source_id: str,
        database_name: str,
        readings: Mapping[str, dict[str, Any]] | None = None,
    ) -> int:
        """Link nodes to ``source_id``: back-fill the home where missing, record the membership.

        The CCX importer creates nodes before it knows their source (the
        node->source link lives in the citation records, imported last), so it
        stamps ``graph_nodes.source_id`` here once citations resolve. Nodes
        that already carry a source id keep it (first link wins). Every node
        gains ``source_id`` as a member, its snapshot ``readings[node_id]``
        (what the source wrote: the package's own entity), else the node's
        own pre-merge reading (its home's snapshot), never a merged row.

        Args:
            node_ids: Node ids to link.
            source_id: Source id to assign to those nodes.
            database_name: Database that owns the nodes.
            readings: Per node id, the record ``source_id`` wrote for it
                (``node_create_snapshot``).
        """
        ...

    def assign_source_to_templates(
        self,
        template_ids: list[str],
        source_id: str,
        database_name: str,
    ) -> int:
        """Back-fill ``source_id`` on templates that lack one; return rows changed.

        Templates are source-owned: a source's release deletes the ones nothing
        else uses (D6). The CCX
        importer creates templates before the source exists (FK order), so it
        links them here once the source lands. Templates that already carry a
        source id are left untouched.

        Args:
            template_ids: Template ids to link.
            source_id: Source id to assign.
            database_name: Database that owns the templates.
        """
        ...

    def list_nodes_without_embeddings(
        self, *, after_id: str | None = None, limit: int = 10000
    ) -> list[Node]:
        """Nodes with no embedding vector, ordered by id, after ``after_id`` when given.

        Args:
            after_id: Keyset cursor: only ids after it; None starts at the beginning.
            limit: Maximum number of results.
        """
        ...

    def update_node_embeddings_batch(self, embeddings: dict[str, list[float]]) -> int:
        """Persist embeddings for many nodes in one transaction; return rows changed.

        Used to re-embed an imported source's nodes without N per-node writes.

        Args:
            embeddings: Mapping of node id to its embedding vector (list[float]).
        """
        ...

    def clear_node_embeddings(self, node_ids: list[str]) -> int:
        """Clear the stored embedding of each node; return rows changed.

        For a node whose text changed under a vector a caller cannot refresh
        at once: an indexing step that embeds only vectorless nodes then
        re-embeds it.

        Args:
            node_ids: The nodes whose ``embedding`` is cleared.
        """
        ...

    def get_nodes_batch(self, node_ids: list[str], *, include_embedding: bool = True) -> list[Node]:
        """Get multiple nodes by ID in a single operation.

        Args:
            node_ids: List of node IDs to retrieve
            include_embedding: When False, the embedding column is neither
                queried nor decoded and ``Node.embedding`` is ``None`` —
                use for hydration paths that never read the vector.

        Returns:
            List of Node objects (may be less than requested if some not found)

        """
        ...

    def update_node(self, node_id: str, node_update: NodeUpdate) -> Node | None:
        """Update an existing node.

        Args:
            node_id: Node ID to update
            node_update: Node update data

        Returns:
            Updated Node object or None if not found

        """
        ...

    def update_node_position(self, node_id: str, x: float, y: float) -> Node | None:
        """Update only the node's position.

        Args:
            node_id: Node ID to update
            x: X coordinate
            y: Y coordinate

        Returns:
            Updated Node object or None if not found

        """
        ...

    def delete_node(self, node_id: str) -> bool:
        """Delete a node by ID.

        Args:
            node_id: Node ID to delete

        Returns:
            True if node was deleted, False if not found

        """
        ...

    def count_nodes(self, include_disabled_sources: bool = True) -> int:
        """Count total nodes.

        Args:
            include_disabled_sources: When False, excludes nodes from disabled
                sources so the count matches ``list_nodes`` (used for pagination
                totals). Defaults True (true storage total).

        Returns:
            Count of nodes

        """
        ...

    def count_nodes_by_source(
        self, source_ids: list[str], include_disabled_sources: bool = True
    ) -> int:
        """Count nodes from specific source documents.

        Args:
            source_ids: List of source document IDs
            include_disabled_sources: When False, also drops nodes from disabled
                sources (mirrors ``list_nodes``).

        Returns:
            Count of nodes from those sources

        """
        ...

    def count_nodes_by_template(
        self,
        template_ids: list[str],
        exclude: bool = False,
        include_disabled_sources: bool = True,
    ) -> int:
        """Count nodes with specific template IDs (or excluding them).

        Args:
            template_ids: List of template IDs
            exclude: If True, count nodes NOT in template_ids
            include_disabled_sources: When False, also drops nodes from disabled
                sources (mirrors ``list_nodes``).

        Returns:
            Count of nodes

        """
        ...

    # ==================== Edge Operations ====================

    def create_edge(
        self, edge_create: EdgeCreate, custom_id: str | None = None, *, person_made: bool = False
    ) -> Edge:
        """Create a new edge between two nodes.

        Args:
            edge_create: Edge creation data
            custom_id: The row's id; None generates one.
            person_made: A person makes it (the create tools, the API, the SDK, the
                CLI): no release deletes or rewrites it (migration 0025). Never request
                input: only the person paths pass it.

        Returns:
            Created Edge object with generated ID

        Note:
            This is a raw write: implementations do not check that the
            source/target nodes or the template exist. Use
            ``EdgeService.create_edge`` for validated creation (it raises
            ``NotFoundError`` / ``ValidationError``). On the SQLite adapter
            a dangling endpoint or template surfaces as the backend's
            ``sqlalchemy.exc.IntegrityError``, not ``ValueError``.

        Example:
            from chaoscypher_core.models import EdgeCreate

            edge_create = EdgeCreate(
                template_id="knows",
                source_node_id="person_123",
                target_node_id="person_456",
                label="knows",
                properties={"since": "2020"}
            )
            created_edge = graph_repo.create_edge(edge_create)

        """
        ...

    def get_edge_by_ccx_iri(self, ccx_iri: str, database_name: str) -> dict[str, Any] | None:
        """Look up an edge by its stable CCX IRI.

        Returns the ORM-row dict (so ``ccx_iri`` survives — the ``Edge`` model
        has no such field) or ``None`` when no row in ``database_name`` carries
        that IRI. Lookup primitive for the CCX 3.0 importer's upsert-by-IRI.

        Args:
            ccx_iri: The CCX 3.0 stable IRI to match.
            database_name: Database that owns the edge.

        """
        ...

    def upsert_edge_by_ccx_iri(
        self,
        ccx_iri: str,
        edge_create: EdgeCreate,
        database_name: str,
        source_id: str | None = None,
    ) -> dict[str, Any]:
        """Idempotently create or update an edge keyed by CCX IRI.

        SELECT by ``(database_name, ccx_iri)``; update the existing row's
        label / properties / endpoints when found (incoming-wins, no
        duplicate), else create a new edge carrying the given ``ccx_iri``.
        ``EdgeCreate`` has no ``ccx_iri`` field, so the IRI is set on the ORM
        row directly. Used by the CCX 3.0 importer for idempotent re-import.

        Args:
            ccx_iri: Stable CCX IRI used as the merge key.
            edge_create: Edge payload (template_id, endpoints, label, ...).
            database_name: Database to scope the upsert to.
            source_id: Optional source id override for the created/updated row.

        Returns:
            The created or updated edge row dict (including ``ccx_iri``).
        """
        ...

    def get_edge(self, edge_id: str) -> Edge | None:
        """Get an edge by ID.

        Args:
            edge_id: Edge ID to retrieve

        Returns:
            Edge object or None if not found

        """
        ...

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
        """List edges, optionally filtered by source/target node or source document.

        Args:
            source_node_id: Optional source node ID filter
            target_node_id: Optional target node ID filter
            source_ids: Optional list of source document IDs to filter by
            skip: Number of results to skip
            limit: Maximum number of results
            include_disabled_sources: If False (default), excludes edges from disabled sources
            minimal: If True, only load essential fields
            with_nodes: If True, batch-load source_node and target_node for each edge
                        and return EdgeWithNodes instances.

        Returns:
            List of Edge objects, or EdgeWithNodes when with_nodes=True.

        """
        ...

    def list_edges_by_node(
        self,
        node_ids: Sequence[str],
        *,
        outgoing: bool,
        limit_per_node: int,
        include_disabled_sources: bool = False,
    ) -> dict[str, list[Edge]]:
        """Each node's edges out of it (``outgoing``) or into it, as :meth:`list_edges` lists them.

        For every id in ``node_ids``, exactly what :meth:`list_edges` returns
        with ``limit=limit_per_node`` and the id as ``source_node_id`` (as
        ``target_node_id`` when not ``outgoing``), in its order. Every id is a
        key; a node with no edges maps to ``[]``. An adapter answers in as few
        queries as it can: the graph context reads every seed's edges in both
        directions.
        """
        ...

    def update_edge(self, edge_id: str, edge_update: EdgeUpdate) -> Edge | None:
        """Update an existing edge.

        Args:
            edge_id: Edge ID to update
            edge_update: Edge update data

        Returns:
            Updated Edge object or None if not found

        """
        ...

    def repoint_edge(self, edge_id: str, *, source_node_id: str, target_node_id: str) -> None:
        """Move an edge to new ends in place, keeping its id, ``created_at`` and ``ccx_iri``.

        Joins the caller's transaction: nothing is committed here.

        Raises:
            NotFoundError: No edge of this database has the id.
        """
        ...

    def delete_edge(self, edge_id: str) -> bool:
        """Delete an edge by ID.

        Args:
            edge_id: Edge ID to delete

        Returns:
            True if edge was deleted, False if not found

        """
        ...

    def count_edges(
        self,
        source_node_id: str | None = None,
        target_node_id: str | None = None,
        source_ids: list[str] | None = None,
        include_disabled_sources: bool = True,
    ) -> int:
        """Count edges, optionally filtered by source/target node or source document.

        Args:
            source_node_id: Optional source node ID filter
            target_node_id: Optional target node ID filter
            source_ids: Optional list of source document IDs to filter by
            include_disabled_sources: When False, also drops edges from disabled
                sources (mirrors ``list_edges``) for pagination totals.

        Returns:
            Count of edges

        """
        ...

    def count_edges_per_node(self, node_ids: list[str]) -> dict[str, int]:
        """Return total incident edge count for each node ID.

        Counts both incoming and outgoing edges for the given nodes in a
        single pair of grouped queries (one per direction). Useful for
        list/search projections that need a per-hit "connections" number
        without a round-trip per node.

        Args:
            node_ids: Node IDs to count edges for. Empty input returns ``{}``.

        Returns:
            ``{node_id: total_incident_edges}`` for every input ID. Nodes
            with no edges still appear with a count of ``0``.

        """
        ...

    # ==================== Template Operations ====================

    def list_templates(
        self,
        template_type: str | None = None,
        include_disabled_sources: bool = False,
        source_id: str | None = None,
        skip: int = 0,
        limit: int | None = None,
    ) -> list[Template]:
        """List templates (node and edge types).

        Args:
            template_type: Optional filter by type ("node" or "edge")
            include_disabled_sources: If False (default), hide templates from disabled sources
            source_id: Optional filter by source ID
            skip: Number of results to skip (for SQL-level pagination)
            limit: Maximum number of results (None returns all)

        Returns:
            List of Template objects (both node and edge templates)

        Example:
            templates = graph_repo.list_templates()
            node_templates = [t for t in templates if t.template_type == "node"]
            edge_templates = [t for t in templates if t.template_type == "edge"]

        """
        ...

    def get_template(self, template_id: str) -> Template | None:
        """Get a template by ID.

        Args:
            template_id: Template ID to retrieve

        Returns:
            Template object or None if not found

        """
        ...

    def create_template(
        self,
        template_create: TemplateCreate,
        custom_id: str | None = None,
        is_system: bool = False,
    ) -> Template:
        """Create a new template.

        Args:
            template_create: Template creation data
            custom_id: Optional custom ID (if None, auto-generated)
            is_system: Whether this is a system template

        Returns:
            Created Template object

        """
        ...

    def discard_failed_work(self) -> None:
        """Roll back what a failed call left in this repository's session.

        For a catch that answers a failure and goes on: the next call must not find the
        session needing a rollback. Inside a transaction block, the block rolls back.
        """
        ...

    def ensure_default_templates_exist(
        self, default_templates_provider: Callable[[], list[dict[str, Any]]] | None = None
    ) -> int:
        """Insert each template the provider gives that the database lacks; return how many.

        A template is present when a row has its id; a present one is never rewritten.

        Args:
            default_templates_provider: Returns the template definitions
                (``templates/default_templates.py``'s shape); None inserts nothing.

        Returns:
            How many were inserted.

        """
        ...

    def update_template(self, template_id: str, template_update: TemplateUpdate) -> Template | None:
        """Update an existing template.

        Args:
            template_id: Template ID to update
            template_update: Template update data

        Returns:
            Updated Template object or None if not found

        """
        ...

    def delete_template(self, template_id: str, force: bool = False) -> bool:
        """Delete a template by ID.

        Args:
            template_id: Template ID to delete
            force: If True, delete even if template is in use

        Returns:
            True if template was deleted, False if not found

        """
        ...

    def count_templates_by_system(self, is_system: bool) -> int:
        """Count user or system templates.

        Args:
            is_system: True to count system templates, False for user templates

        Returns:
            Count of templates

        """
        ...

    def get_template_usage_counts(
        self, template_ids: list[str] | None = None
    ) -> dict[str, dict[str, int]]:
        """Get usage counts (nodes and edges) for templates.

        Args:
            template_ids: Optional list of template IDs to check (None = all)

        Returns:
            Dict mapping template_id to {"nodes": count, "edges": count}

        """
        ...

    def export_graph(self, max_items: int = 100000) -> dict[str, Any]:
        """Export all graph data (nodes, edges, templates) for CCX package creation.

        Args:
            max_items: Maximum nodes/edges to export.

        Returns:
            Dict with ``nodes``, ``edges``, ``templates`` lists of model_dump()s.
        """
        ...

    def export_graph_records(
        self,
        *,
        source_ids: list[str] | None = None,
        max_items: int = 100000,
    ) -> dict[str, list[dict[str, Any]]]:
        """Export graph nodes + edges as dicts carrying the persisted ``ccx_iri``.

        Unlike :meth:`export_graph` (which returns Pydantic-model dumps that
        drop the ``ccx_iri`` stable-identity column), this projects the ORM
        rows directly so the CCX 3.0 exporter can key identity on ``ccx_iri``.

        Args:
            source_ids: When given, restrict to nodes/edges of these sources;
                edges survive only when BOTH endpoints survive the node filter.
            max_items: Maximum nodes/edges to export per entity type.

        Returns:
            ``{"nodes": [node dicts], "edges": [edge dicts]}`` including ``ccx_iri``.
        """
        ...

    async def create_nodes_batch(self, node_creates: list[NodeCreate]) -> list[Node]:
        """Create multiple nodes in batch.

        Args:
            node_creates: List of node creation data

        Returns:
            List of created Node objects

        """
        ...

    async def upsert_nodes_batch(
        self, node_creates: list[NodeCreate], *, reattested: set[str] | None = None
    ) -> tuple[list[Node], int]:
        """Idempotently create or reuse nodes by stable content key.

        Used on the commit path: re-dispatched commits observe pre-existing
        rows via a bulk SELECT-by-id and leave them untouched. A reused row
        the incoming source does not attest (one that outlived the source's
        release under another member) gets the source's membership back with
        the reading it writes now, and its id is added to ``reattested`` for
        the caller to rebuild (#795).

        Args:
            node_creates: List of node creation data.
            reattested: Filled with the reused ids that gained a membership.

        Returns:
            Tuple of:
            - List of Node objects (created or pre-existing) in input order.
            - Count of rows actually inserted (not counting dedup reuses).
        """
        ...

    async def create_edges_batch(self, edge_creates: list[EdgeCreate]) -> list[Edge]:
        """Create multiple edges in batch.

        Args:
            edge_creates: List of edge creation data

        Returns:
            List of created Edge objects

        """
        ...

    async def upsert_edges_batch(
        self, edge_creates: list[EdgeCreate], *, reattested: set[str] | None = None
    ) -> tuple[list[Edge], int]:
        """Idempotently create or reuse edges by stable content key.

        Used on the commit path; mirror of :meth:`upsert_nodes_batch`,
        ``reattested`` included.

        Args:
            edge_creates: List of edge creation data.
            reattested: Filled with the reused ids that gained a membership.

        Returns:
            Tuple of:
            - List of Edge objects (created or pre-existing) in input order.
            - Count of rows actually inserted (not counting dedup reuses).
        """
        ...

    async def create_templates_batch(
        self, template_creates: list[TemplateCreate]
    ) -> list[Template]:
        """Create multiple templates in batch.

        Args:
            template_creates: List of template creation data

        Returns:
            List of created Template objects

        """
        ...

    def upsert_template(
        self,
        template_create: TemplateCreate,
        is_system: bool = False,
    ) -> tuple[Template, bool]:
        """Idempotently create a template by stable content key.

        Args:
            template_create: Template to create (or reuse).
            is_system: Whether this is a system template.

        A reused template owned by another source (or by none) since a
        release is handed back to the source its stable id names.

        Returns:
            Tuple of:
            - Template Pydantic model with a stable .id.
            - True if the template was newly inserted, False if pre-existing.
        """
        ...

    async def upsert_templates_batch(
        self, template_creates: list[TemplateCreate]
    ) -> tuple[list[Template], int]:
        """Idempotently create a batch of templates by stable content key.

        Args:
            template_creates: Templates to create or reuse.

        Returns:
            Tuple of:
            - List of Template objects in input order (created or pre-existing).
            - Count of rows actually inserted (not counting dedup reuses).
        """
        ...

    # ------------------------------------------------------------------
    # Graph cleanup operations (PR2a Task 12).
    # Consumed by GraphCleanupService once it moves down into core in PR2b.
    # ------------------------------------------------------------------

    def find_orphaned_edges_by_source_node(self, *, database_name: str) -> list[str]:
        """Return IDs of edges whose source_node_id has no matching GraphNode.

        Args:
            database_name: Database to scope to.

        Returns:
            List of GraphEdge IDs (may be empty).
        """
        ...

    def find_orphaned_edges_by_target_node(self, *, database_name: str) -> list[str]:
        """Return IDs of edges whose target_node_id has no matching GraphNode.

        Args:
            database_name: Database to scope to.

        Returns:
            List of GraphEdge IDs.
        """
        ...

    def delete_edges_batch(self, *, edge_ids: list[str]) -> int:
        """Delete GraphEdge rows by ID list.

        Args:
            edge_ids: IDs to delete.

        Returns:
            Number of rows deleted.
        """
        ...

    def find_orphaned_nodes_by_source(self, *, database_name: str) -> list[str]:
        """Return IDs of nodes whose source_id references a missing SourceRow and no source attests.

        Nodes with ``source_id IS NULL`` are NOT considered orphaned, nor is a
        node with any source membership (a merged node another source attests).

        Args:
            database_name: Database to scope to.

        Returns:
            List of GraphNode IDs.
        """
        ...

    def delete_nodes_batch(self, *, node_ids: list[str]) -> int:
        """Delete GraphNode rows by ID list. Returns count."""
        ...

    def find_orphaned_templates_by_source(self, *, database_name: str) -> list[str]:
        """Return IDs of unused non-system templates whose source_id references a missing SourceRow.

        System templates (``is_system=True``) are never considered orphaned,
        nor is a template a node or edge still uses.

        Args:
            database_name: Database to scope to.

        Returns:
            List of GraphTemplate IDs.
        """
        ...

    def delete_templates_batch(self, *, template_ids: list[str]) -> int:
        """Delete GraphTemplate rows by ID list. Returns count."""
        ...

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
            database_name: Database scope. Defaults to the repo's bound database.
            template_type: Optional filter by template_type ('node' or 'edge').
            source_id: Optional filter by source_id.
            include_disabled_sources: When False, excludes templates from disabled
                sources (mirrors ``list_templates``) for pagination totals; ignored
                when ``source_id`` is given.
        """
        ...

    # ---- Source memberships (which sources attest a node or edge) ----

    def record_node_sources(self, rows: Sequence[tuple[str, str, dict[str, Any]]]) -> None:
        """Record ``(node_id, source_id, snapshot)`` memberships.

        A source writing a node again replaces its own snapshot. Joins the
        caller's transaction; nothing is committed.
        """
        ...

    def record_edge_sources(self, rows: Sequence[tuple[str, str, dict[str, Any]]]) -> None:
        """Record ``(edge_id, source_id, snapshot)`` memberships; same rules as nodes."""
        ...

    def absorb_node(self, keep_id: str, drop_id: str) -> None:
        """Fold a merged-away node into the kept one: memberships and entity citations.

        ``drop_id``'s memberships move to ``keep_id``; where both hold one for the
        same source, the kept row stays and the dropped snapshot is appended to its
        ``absorbed`` list. Every ``SourceCitation`` naming ``drop_id`` names
        ``keep_id`` after. Call before deleting ``drop_id``. Joins the caller's
        transaction; nothing is committed.
        """
        ...

    def absorb_edge(self, keep_id: str, drop_id: str) -> None:
        """Fold a moved or duplicate edge into its new edge: memberships and citations.

        Same rules as :meth:`absorb_node`, for ``RelationshipCitation.edge_id``.
        """
        ...

    def drop_edge_citations(self, edge_id: str) -> None:
        """Delete an edge's relationship citations (a merge turned it into a self-loop)."""
        ...

    def reattest_nodes(self, rows: Sequence[tuple[str, NodeCreate]]) -> set[str]:
        """Give each ``(node_id, create)`` its source's membership when that source has none.

        The snapshot is the record ``create`` writes. Returns the ids that
        gained a membership. Joins the caller's transaction; nothing is committed.
        """
        ...

    def reattest_edges(self, rows: Sequence[tuple[str, EdgeCreate]]) -> set[str]:
        """:meth:`reattest_nodes` for edges."""
        ...

    def member_source_ids(self) -> set[str]:
        """Every source that attests some node: the graph's sources, by membership."""
        ...

    def write_locked(self) -> AbstractContextManager[None]:
        """A unit of work that holds the database's writer lock from its first statement.

        For a read-then-write that must not interleave with another writer:
        what is read inside is what the writes inside build on. The rows the
        session cached are reloaded when the lock is taken, so what is read
        inside is the database as of the lock. Commits on a
        clean exit and rolls back on an exception; inside a caller's
        transaction it joins it (taking the lock now if the transaction has
        not written yet) and commits nothing.
        """
        ...

    def member_node_ids(self, source_id: str) -> list[str]:
        """The nodes ``source_id`` attests, in membership order."""
        ...

    def nodes_attested_by(self, source_ids: Collection[str], limit: int) -> list[str]:
        """At most ``limit`` nodes ``source_ids`` attest, most memberships among them first.

        Ties go by node id. A scoped memory's seed reads its scope's nodes with it.
        """
        ...

    def member_edge_ids(self, source_id: str) -> list[str]:
        """The edges ``source_id`` attests, in membership order."""
        ...

    def open_edges(self, node_id: str, label: str) -> list[Edge]:
        """The edges from ``node_id`` with ``label`` whose ``valid_to`` is unset."""
        ...

    def edges_from(self, node_id: str, label: str) -> list[Edge]:
        """Every edge from ``node_id`` with ``label``, open or closed."""
        ...

    def set_edge_validity(
        self,
        edge_id: str,
        *,
        valid_from: date | None,
        valid_to: date | None,
        closed_by_source_id: str | None,
    ) -> None:
        """Write an edge's validity window; joins the caller's transaction."""
        ...

    def edges_closed_by(self, source_id: str) -> list[Edge]:
        """The edges ``source_id`` closed (``closed_by_source_id``)."""
        ...

    def reopen_edges_closed_by(self, source_id: str) -> int:
        """Reopen the edges ``source_id`` closed (``valid_to`` and ``closed_by_source_id`` unset); how many."""
        ...

    def source_valid_from(self, source_ids: Iterable[str]) -> dict[str, date | None]:
        """Each source's ``valid_from`` (a non-memory source maps to None)."""
        ...

    def memory_source_fields(self, source_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        """Each source's ``source_type``, ``memory_by``, ``valid_from`` (a date or None), ``created_at``."""
        ...

    def node_members(self, node_id: str) -> dict[str, dict[str, Any]]:
        """Map source_id to the snapshot that source wrote for this node."""
        ...

    def edge_members(self, edge_id: str) -> dict[str, dict[str, Any]]:
        """Map source_id to the snapshot that source wrote for this edge."""
        ...

    def node_member_sources(self, node_ids: Iterable[str]) -> dict[str, set[str]]:
        """Map each of ``node_ids`` that has members to the ids of its member sources.

        One batched read (no snapshots): for a caller that only needs to know
        who attests a set of nodes.
        """
        ...

    def rehome(
        self,
        nodes: Mapping[str, str],
        edges: Mapping[str, str],
        *,
        entity_types: Mapping[str, str] | None = None,
    ) -> None:
        """Re-home each node (edge) id to the source it maps to.

        A survivor of a release is re-homed to a member that still attests it:
        its ``source_id``, a node's ``entity_type`` (``entity_types``), and its
        template, moved to that source's template of the same type and name
        when there is one. Joins the caller's transaction; nothing is committed.
        """
        ...

    # ---- Release (what a source delete, re-commit, re-extract or reset removes) ----

    def release_source(self, source_id: str) -> ReleasedRows:
        """Take a source out of the graph: its memberships, and what only it attested.

        Candidates are the nodes and edges the source is a member of, plus the
        rows homed at it. Each loses the source's membership; a candidate no
        source attests any more is deleted (an edge also goes with a deleted
        endpoint); the surviving candidates are reported as touched. The
        source's templates follow D6: deleted when unused, else re-homed to a
        member source of a surviving user (unowned only when no user has a
        member). Joins the caller's transaction; never deletes the source row
        or its citations.
        """
        ...

    def release_all_sources(self) -> None:
        """Release every source of this database in one pass (the reset paths)."""
        ...
