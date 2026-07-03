# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Graph repository interface for chaoscypher-engine.

Defines Protocol for graph data access (nodes, edges, templates).
Main app implements this via an adapter class that wraps its GraphRepository.
"""

from typing import TYPE_CHECKING, Any, Protocol


if TYPE_CHECKING:
    from chaoscypher_core.models import (
        Edge,
        EdgeCreate,
        EdgeUpdate,
        EdgeWithNodes,
        Node,
        NodeCreate,
        NodeUpdate,
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

    def get_node(self, node_id: str) -> Node | None:
        """Get a node by ID.

        Args:
            node_id: Unique node identifier

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

    def create_node(self, node_create: NodeCreate) -> Node:
        """Create a new node in the graph.

        Args:
            node_create: Node creation data

        Returns:
            Created Node object with generated ID

        Raises:
            ValueError: If template_id invalid or required fields missing

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
    ) -> int:
        """Back-fill ``source_id`` on nodes that lack one; return rows changed.

        The CCX importer creates nodes before it knows their source (the
        node->source link lives in the citation records, imported last), so it
        stamps ``graph_nodes.source_id`` here once citations resolve. Nodes
        that already carry a source id are left untouched (first link wins).

        Args:
            node_ids: Node ids to link.
            source_id: Source id to assign to those nodes.
            database_name: Database that owns the nodes.
        """
        ...

    def assign_source_to_templates(
        self,
        template_ids: list[str],
        source_id: str,
        database_name: str,
    ) -> int:
        """Back-fill ``source_id`` on templates that lack one; return rows changed.

        Templates are source-owned and cascade-delete with their source. The CCX
        importer creates templates before the source exists (FK order), so it
        links them here once the source lands. Templates that already carry a
        source id are left untouched.

        Args:
            template_ids: Template ids to link.
            source_id: Source id to assign.
            database_name: Database that owns the templates.
        """
        ...

    def update_node_embeddings_batch(self, embeddings: dict[str, list[float]]) -> int:
        """Persist embeddings for many nodes in one transaction; return rows changed.

        Used to re-embed an imported source's nodes without N per-node writes.

        Args:
            embeddings: Mapping of node id to its embedding vector (list[float]).
        """
        ...

    def get_nodes_batch(self, node_ids: list[str]) -> list[Node]:
        """Get multiple nodes by ID in a single operation.

        Args:
            node_ids: List of node IDs to retrieve

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

    def create_edge(self, edge_create: EdgeCreate) -> Edge:
        """Create a new edge between two nodes.

        Args:
            edge_create: Edge creation data

        Returns:
            Created Edge object with generated ID

        Raises:
            ValueError: If source or target node not found

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

    def update_edge(self, edge_id: str, edge_update: EdgeUpdate) -> Edge | None:
        """Update an existing edge.

        Args:
            edge_id: Edge ID to update
            edge_update: Edge update data

        Returns:
            Updated Edge object or None if not found

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

    def delete_graph_data_by_source(self, source_id: str) -> dict[str, Any]:
        """Delete all graph data (edges, nodes, templates) for a given source.

        Used for idempotent commit: cleans up previously committed graph objects
        before re-committing.

        Args:
            source_id: Source ID whose graph data should be deleted.

        Returns:
            Dict with edges_deleted, nodes_deleted, templates_deleted counts
            and deleted_node_ids list.

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

    async def upsert_nodes_batch(self, node_creates: list[NodeCreate]) -> tuple[list[Node], int]:
        """Idempotently create or reuse nodes by stable content key.

        Used on the commit path: re-dispatched commits observe pre-existing
        rows via a bulk SELECT-by-id and leave them untouched.

        Args:
            node_creates: List of node creation data.

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

    async def upsert_edges_batch(self, edge_creates: list[EdgeCreate]) -> tuple[list[Edge], int]:
        """Idempotently create or reuse edges by stable content key.

        Used on the commit path; mirror of :meth:`upsert_nodes_batch`.

        Args:
            edge_creates: List of edge creation data.

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
        """Return IDs of nodes whose source_id references a missing SourceRow.

        Nodes with ``source_id IS NULL`` are NOT considered orphaned.

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
        """Return IDs of non-system templates whose source_id references a missing SourceRow.

        System templates (``is_system=True``) are never considered orphaned.

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
