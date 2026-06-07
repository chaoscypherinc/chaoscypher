# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Node Service for chaoscypher-engine.

Business logic for node operations with search integration.
"""

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.exceptions import NotFoundError


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter
    from chaoscypher_core.models import NodeCreate, NodeUpdate
    from chaoscypher_core.ports.graph import GraphRepositoryProtocol
    from chaoscypher_core.ports.search import SearchRepositoryProtocol
    from chaoscypher_core.settings import EngineSettings

logger = structlog.get_logger(__name__)


class _NullSearchRepository:
    """No-op search repository for NodeService.from_adapter() when no SearchRepository is provided."""

    def index_node(self, node: Any) -> None:
        """No-op index."""

    def delete_node(self, node_id: str) -> None:
        """No-op delete."""


class NodeService:
    """Service for node business logic with search integration.

    Orchestrates node CRUD operations across GraphRepository and SearchRepository.
    Provides template validation and automatic search indexing.
    """

    def __init__(
        self,
        graph_repository: GraphRepositoryProtocol,
        search_repository: SearchRepositoryProtocol,
        settings: EngineSettings,
    ) -> None:
        """Initialize node service.

        Args:
            graph_repository: GraphRepository implementation
            search_repository: SearchRepository implementation (for auto-indexing)
            settings: Engine settings for pagination configuration

        """
        self.graph_repository = graph_repository
        self.search_repository = search_repository
        self.settings = settings

    @classmethod
    def from_engine(cls, engine: Any) -> NodeService:
        """Create a NodeService wired from an Engine instance.

        Args:
            engine: Engine instance with graph_repository, search_repository,
                and settings.

        Returns:
            NodeService with all dependencies injected.

        """
        return cls(
            graph_repository=engine.graph_repository,
            search_repository=engine.search_repository,
            settings=engine.settings,
        )

    @classmethod
    def from_adapter(
        cls,
        adapter: SqliteAdapter,
        settings: EngineSettings,
        *,
        search_repository: SearchRepositoryProtocol | None = None,
    ) -> NodeService:
        """Create a NodeService from a storage adapter.

        Wires the adapter into graph repository and (optionally) search
        repository slots. For full search integration, pass a
        SearchRepository explicitly.

        Args:
            adapter: SqliteAdapter (or compatible) implementing
                GraphRepositoryProtocol.
            settings: Engine settings.
            search_repository: Optional SearchRepository for auto-indexing.
                When omitted, search indexing is skipped.

        Returns:
            Configured NodeService.

        Example:
            from chaoscypher_core import NodeService, SqliteAdapter, EngineSettings

            adapter = SqliteAdapter("app.db", "default")
            service = NodeService.from_adapter(adapter, EngineSettings())

        """
        from chaoscypher_core.adapters.sqlite.repos import GraphRepository

        if adapter.session is None:
            msg = "SqliteAdapter.session is None — call adapter.connect() first."
            raise RuntimeError(  # nosemgrep: cc-045-bare-stdlib-raise-in-core - programmer error: adapter must be connected before constructing NodeService
                msg
            )
        graph_repo = GraphRepository(adapter.session, settings.current_database)
        search_repo: Any = search_repository or _NullSearchRepository()
        return cls(
            graph_repository=graph_repo,
            search_repository=search_repo,
            settings=settings,
        )

    # ========================================================================
    # Search Index Helpers
    # ========================================================================

    def safe_index_node(self, node_id: str, node: Any) -> None:
        """Index node in search with error handling.

        Args:
            node_id: Node ID
            node: Node object to index

        """
        try:
            self.search_repository.index_node(node)
        except Exception as e:
            logger.warning(
                "failed_to_index_node",
                node_id=node_id,
                error_type=type(e).__name__,
                error_message=str(e),
            )

    def safe_delete_node_index(self, node_id: str) -> None:
        """Remove node from search index with error handling.

        Args:
            node_id: Node ID

        """
        try:
            self.search_repository.delete_node(node_id)
        except Exception as e:
            logger.warning(
                "failed_to_remove_node_from_index",
                node_id=node_id,
                error_type=type(e).__name__,
                error_message=str(e),
            )

    # ========================================================================
    # Node CRUD Operations
    # ========================================================================

    def list_nodes(
        self,
        template_id: str | None = None,
        source_ids: list[str] | None = None,
        page: int = 1,
        page_size: int = 50,
        minimal: bool = False,
        include_embedding: bool = True,
    ) -> dict[str, Any]:
        """List nodes with pagination.

        Args:
            template_id: Filter by template (optional)
            source_ids: Filter by source document IDs (optional)
            page: Page number (1-indexed)
            page_size: Items per page
            minimal: If True, only load essential fields (excludes embedding, properties)
                     for better performance with large graphs
            include_embedding: If True (default), include the embedding vector. List
                     views that never use embeddings pass False to skip loading and
                     serializing them. Ignored when minimal=True.

        Returns:
            Dict with keys:
                - data: List of node dicts
                - pagination: Pagination metadata

        """
        # Calculate skip from page
        skip = (page - 1) * page_size

        # Get total count. list_nodes (below) hides disabled-source rows by
        # default, so the count must do the same or pagination shows phantom
        # trailing pages and a total that ignores source enable/disable.
        if source_ids:
            total = self.graph_repository.count_nodes_by_source(
                source_ids, include_disabled_sources=False
            )
        elif template_id:
            total = self.graph_repository.count_nodes_by_template(
                [template_id], exclude=False, include_disabled_sources=False
            )
        else:
            total = self.graph_repository.count_nodes(include_disabled_sources=False)

        # Get paginated nodes
        nodes = self.graph_repository.list_nodes(
            template_id=template_id,
            source_ids=source_ids,
            skip=skip,
            limit=page_size,
            minimal=minimal,
            include_embedding=include_embedding,
        )

        total_pages = (total + page_size - 1) // page_size

        # Minimal mode excludes heavyweight fields (properties, embedding,
        # timestamps) to reduce JSON payload for graph canvas rendering.
        data: list[dict[str, Any]]
        if minimal:
            data = [
                {
                    "id": n.id,
                    "template_id": n.template_id,
                    "label": n.label,
                    "position": {"x": n.position.x, "y": n.position.y} if n.position else None,
                    "source_id": n.source_id,
                }
                for n in nodes
            ]
        else:
            data = [
                {
                    "id": n.id,
                    "template_id": n.template_id,
                    "label": n.label,
                    "properties": n.properties,
                    "position": {"x": n.position.x, "y": n.position.y} if n.position else None,
                    "embedding": n.embedding,
                    "created_at": n.created_at,
                    "updated_at": n.updated_at,
                }
                for n in nodes
            ]

        return {
            "data": data,
            "pagination": {
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1,
            },
        }

    def get_node(self, node_id: str) -> dict[str, Any]:
        """Get node by ID.

        Args:
            node_id: Node ID

        Returns:
            Node dictionary

        Raises:
            NotFoundError: If node not found

        """
        node = self.graph_repository.get_node(node_id)
        if not node:
            msg = "Node"
            raise NotFoundError(msg, node_id)

        return {
            "id": node.id,
            "template_id": node.template_id,
            "label": node.label,
            "properties": node.properties,
            "position": {"x": node.position.x, "y": node.position.y} if node.position else None,
            "embedding": node.embedding,
            "created_at": node.created_at,
            "updated_at": node.updated_at,
        }

    def create_node(self, node_create: NodeCreate) -> dict[str, Any]:
        """Create new node with template validation and search indexing.

        Args:
            node_create: Node creation data

        Returns:
            Created node dictionary

        Raises:
            NotFoundError: If template not found

        """
        # Validate template exists
        template = self.graph_repository.get_template(node_create.template_id)
        if not template:
            msg = "Template"
            raise NotFoundError(msg, node_create.template_id)

        # Create node
        node = self.graph_repository.create_node(node_create)

        # Index for search (with error handling)
        self.safe_index_node(node.id, node)

        return {
            "id": node.id,
            "template_id": node.template_id,
            "label": node.label,
            "properties": node.properties,
            "position": {"x": node.position.x, "y": node.position.y} if node.position else None,
            "embedding": node.embedding,
            "created_at": node.created_at,
            "updated_at": node.updated_at,
        }

    def update_node(self, node_id: str, node_update: NodeUpdate) -> dict[str, Any]:
        """Update node and refresh search index.

        Args:
            node_id: Node ID
            node_update: Node update data

        Returns:
            Updated node dictionary

        Raises:
            NotFoundError: If node not found

        """
        node = self.graph_repository.update_node(node_id, node_update)
        if not node:
            msg = "Node"
            raise NotFoundError(msg, node_id)

        # Update search index (with error handling)
        self.safe_index_node(node.id, node)

        return {
            "id": node.id,
            "template_id": node.template_id,
            "label": node.label,
            "properties": node.properties,
            "position": {"x": node.position.x, "y": node.position.y} if node.position else None,
            "embedding": node.embedding,
            "created_at": node.created_at,
            "updated_at": node.updated_at,
        }

    def update_node_position(self, node_id: str, x: float, y: float) -> dict[str, Any]:
        """Update only node position (optimized for layout saving).

        This operation updates search index but doesn't trigger other events
        for performance reasons.

        Args:
            node_id: Node ID
            x: X coordinate
            y: Y coordinate

        Returns:
            Updated node dictionary

        Raises:
            NotFoundError: If node not found

        """
        node = self.graph_repository.update_node_position(node_id, x, y)

        if not node:
            msg = "Node"
            raise NotFoundError(msg, node_id)

        # Still update search index (with error handling)
        self.safe_index_node(node.id, node)

        return {
            "id": node.id,
            "template_id": node.template_id,
            "label": node.label,
            "properties": node.properties,
            "position": {"x": node.position.x, "y": node.position.y} if node.position else None,
            "embedding": node.embedding,
            "created_at": node.created_at,
            "updated_at": node.updated_at,
        }

    def delete_node(self, node_id: str) -> None:
        """Delete node and remove from search index.

        Args:
            node_id: Node ID

        Raises:
            NotFoundError: If node not found

        """
        success = self.graph_repository.delete_node(node_id)
        if not success:
            msg = "Node"
            raise NotFoundError(msg, node_id)

        # Remove from search index (with error handling)
        self.safe_delete_node_index(node_id)
