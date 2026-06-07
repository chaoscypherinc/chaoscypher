# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Nodes Service.

Backend wrapper for engine NodeService with SQLModel-specific extensions (VSA compliance).
"""

from typing import TYPE_CHECKING

import structlog

from chaoscypher_core.adapters.sqlite.repos import extract_searchable_text
from chaoscypher_core.app_config.engine_factory import (
    build_engine_settings,
)
from chaoscypher_core.exceptions import NotFoundError
from chaoscypher_core.models import NodeUpdate
from chaoscypher_core.services.graph.management.node import (
    NodeService as EngineNodeService,
)
from chaoscypher_cortex.features.nodes.models import (
    ChunkReference,
    CitationListResponse,
    CitationResponse,
    ConnectedNodeResponse,
    ConnectionsResponse,
    NodePositionUpdateRequest,
    NodeResponse,
    PaginatedNodesResponse,
    SourceReference,
)


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository, SearchRepository
    from chaoscypher_core.app_config import Settings
    from chaoscypher_core.models import NodeCreate
    from chaoscypher_cortex.features.nodes.graph_repository import GraphNodeRepository
    from chaoscypher_cortex.features.nodes.sql_repository import SqlNodeRepository

logger = structlog.get_logger(__name__)


class NodeService:
    """Backend wrapper for engine NodeService with SQLModel-specific extensions.

    Provides VSA-compliant service layer that:
    - Delegates core CRUD and search integration to chaoscypher service
    - Adds backend-specific features (source_id lookup, citations)
    - Converts dict results to Pydantic response models
    """

    def __init__(
        self,
        graph_node_repository: GraphNodeRepository,
        sql_node_repository: SqlNodeRepository,
        graph_repository: GraphRepository,
        search_repository: SearchRepository,
        settings: Settings,
    ):
        """Initialize node service.

        Args:
            graph_node_repository: GraphNodeRepository for RDF operations
            sql_node_repository: SqlNodeRepository for SQL queries (citations, sources)
            graph_repository: Core GraphRepository instance
            search_repository: SearchRepository instance
            settings: Application settings

        """
        self.graph_node_repository = graph_node_repository
        self.sql_node_repository = sql_node_repository
        self.settings = settings

        # Create chaoscypher service for core business logic
        self.engine_service = EngineNodeService(
            graph_repository=graph_repository,
            search_repository=search_repository,
            settings=build_engine_settings(settings),
        )

    # ========================================================================
    # Node CRUD Operations (Delegated to Engine)
    # ========================================================================

    def list_nodes(
        self,
        template_id: str | None = None,
        source_ids: list[str] | None = None,
        page: int = 1,
        page_size: int | None = None,
        minimal: bool = False,
        include_stats: bool = False,
    ) -> PaginatedNodesResponse:
        """List nodes with pagination.

        Delegates to engine NodeService for business logic.

        Args:
            template_id: Filter by template (optional)
            source_ids: Filter by source document IDs (optional)
            page: Page number (1-indexed)
            page_size: Items per page (uses settings default if not provided)
            minimal: If True, only load essential fields (excludes embedding, properties)
                     for better performance with large graphs
            include_stats: If True, include edge/citation stats for each node

        Returns:
            PaginatedNodesResponse with data and pagination metadata

        """
        # Use settings default if not provided
        if page_size is None:
            page_size = self.settings.pagination.default_page_size

        # Enforce max page size
        page_size = min(page_size, self.settings.pagination.max_page_size)

        # Delegate to chaoscypher service. List views never render the embedding
        # vector, so skip loading/serializing it — otherwise the non-minimal path
        # lazy-loads one embedding per row (N+1) and ships them all to the client.
        result = self.engine_service.list_nodes(
            template_id=template_id,
            source_ids=source_ids,
            page=page,
            page_size=page_size,
            minimal=minimal,
            include_embedding=False,
        )

        # Convert dict to Pydantic models for API response
        nodes = [NodeResponse(**node_dict) for node_dict in result["data"]]

        # Optionally add stats for each node
        if include_stats and nodes:
            node_ids = [node.id for node in nodes]
            stats = self.sql_node_repository.get_node_stats_batch(node_ids)

            for node in nodes:
                if node.id in stats:
                    node_stats = stats[node.id]
                    node.edge_count = node_stats["edge_count"]
                    node.incoming_edge_count = node_stats["incoming_edge_count"]
                    node.outgoing_edge_count = node_stats["outgoing_edge_count"]
                    node.citation_count = node_stats["citation_count"]
                    node.relationship_type_count = node_stats["relationship_type_count"]

        return PaginatedNodesResponse(
            data=nodes,
            pagination=result["pagination"],
        )

    def get_node(self, node_id: str) -> NodeResponse:
        """Get node by ID with source_id lookup.

        If the node is a source document node, also includes the source_id.

        Args:
            node_id: Node ID

        Returns:
            NodeResponse with node data

        Raises:
            NotFoundError: If node not found

        """
        node = self.graph_node_repository.get_node(node_id)
        if not node:
            msg = "Node"
            raise NotFoundError(msg, node_id)

        # Convert to response
        response = NodeResponse.model_validate(node)

        # Populate stats fields so the detail page can render them eagerly
        # (matches what list views get and avoids the citation-count-stays-zero
        # bug where the sidebar only updated after the Sources tab was opened).
        stats_batch = self.sql_node_repository.get_node_stats_batch([node_id])
        node_stats = stats_batch.get(node_id)
        if node_stats:
            response.edge_count = node_stats["edge_count"]
            response.incoming_edge_count = node_stats["incoming_edge_count"]
            response.outgoing_edge_count = node_stats["outgoing_edge_count"]
            response.citation_count = node_stats["citation_count"]
            response.relationship_type_count = node_stats["relationship_type_count"]

        # Check if this node is a source document and add source_id
        source_id = self.sql_node_repository.get_source_id_for_node(
            node_id=node_id,
            node_label=node.label,
            node_definition=node.properties.get("definition"),
        )
        if source_id:
            response.source_id = source_id

        return response

    async def create_node(self, node_create: NodeCreate) -> NodeResponse:
        """Create new node with template validation, search indexing, and embedding generation.

        Delegates to engine NodeService for business logic.
        Automatically generates an embedding if one was not provided,
        making the node visible to semantic (vector) search.

        Args:
            node_create: Node creation data

        Returns:
            Created NodeResponse

        Raises:
            NotFoundError: If template not found (404)

        """
        # Delegate to chaoscypher service (handles validation, creation, and search indexing)
        node_dict = self.engine_service.create_node(node_create)

        # Generate embedding if not provided
        if not node_dict.get("embedding"):
            node_dict = await self._generate_and_store_embedding(
                node_id=node_dict["id"],
                node_dict=node_dict,
            )

        # Convert dict to Pydantic model
        return NodeResponse(**node_dict)

    async def update_node(self, node_id: str, node_update: NodeUpdate) -> NodeResponse:
        """Update node, refresh search index, and regenerate embedding if content changed.

        Delegates to engine NodeService for business logic.
        Regenerates the embedding when label or properties change to keep
        semantic search up to date.

        Args:
            node_id: Node ID
            node_update: Node update data

        Returns:
            Updated NodeResponse

        Raises:
            NotFoundError: If node not found (404)

        """
        # Delegate to chaoscypher service (handles update and search indexing)
        node_dict = self.engine_service.update_node(node_id, node_update)

        # Regenerate embedding if label or properties changed (content that affects search)
        content_changed = node_update.label is not None or node_update.properties is not None
        if content_changed:
            node_dict = await self._generate_and_store_embedding(
                node_id=node_id,
                node_dict=node_dict,
            )

        # Convert dict to Pydantic model
        return NodeResponse(**node_dict)

    def update_node_position(
        self, node_id: str, position_update: NodePositionUpdateRequest
    ) -> NodeResponse:
        """Update only node position (optimized for layout saving).

        This operation doesn't trigger events for performance reasons.
        Backend-specific optimization - uses direct repository access.

        Args:
            node_id: Node ID
            position_update: Position update data

        Returns:
            Updated NodeResponse

        Raises:
            NotFoundError: If node not found (404)

        """
        node = self.graph_node_repository.update_node_position(
            node_id, x=position_update.position.x, y=position_update.position.y
        )

        if not node:
            msg = "Node"
            raise NotFoundError(msg, node_id)

        # Still update search index with error handling
        try:
            self.engine_service.safe_index_node(node.id, node)
        except Exception as e:
            logger.warning(
                "failed_to_index_node_after_position_update",
                node_id=node_id,
                error_type=type(e).__name__,
                error_message=str(e),
            )

        return NodeResponse.model_validate(node)

    def delete_node(self, node_id: str) -> None:
        """Delete node and remove from search index.

        Delegates to engine NodeService for business logic.

        Args:
            node_id: Node ID

        Raises:
            NotFoundError: If node not found (404)

        """
        # Delegate to chaoscypher service (handles deletion and search index removal)
        self.engine_service.delete_node(node_id)

    # ========================================================================
    # Embedding Generation
    # ========================================================================

    async def _generate_and_store_embedding(
        self,
        node_id: str,
        node_dict: dict,
    ) -> dict:
        """Generate an embedding for a node, store it, and update the search index.

        Uses the singleton embedding provider to generate a vector from the
        node's searchable text (label + properties). The embedding is persisted
        on the node and indexed in the vector search table.

        If embedding generation fails, the node is still usable via keyword
        search -- the failure is logged as a warning and silently swallowed.

        Args:
            node_id: Node ID
            node_dict: Node data dictionary (modified in-place on success)

        Returns:
            The node_dict, updated with the embedding if generation succeeded

        """
        try:
            from chaoscypher_core.models import Node
            from chaoscypher_core.repo_factories import get_embedding_service

            embedding_service = get_embedding_service()

            # Build a lightweight Node for text extraction
            node_obj = Node(
                id=node_id,
                template_id=node_dict["template_id"],
                label=node_dict["label"],
                properties=node_dict.get("properties", {}),
                created_at=node_dict["created_at"],
                updated_at=node_dict["updated_at"],
            )
            text = extract_searchable_text(node_obj)

            # Generate embedding
            result = await embedding_service.embed(text)
            embedding = result.embedding
            if not embedding:
                logger.warning("node_embedding_empty_result", node_id=node_id)
                return node_dict

            # Persist embedding on the node
            updated_node = self.engine_service.graph_repository.update_node(
                node_id, NodeUpdate(embedding=embedding)
            )
            if updated_node:
                node_dict["embedding"] = embedding

            # Update the vector search index
            self.engine_service.search_repository.index_node_embedding(node_id, embedding)

            logger.info(
                "node_embedding_generated",
                node_id=node_id,
                embedding_dimensions=len(embedding),
            )
        except Exception as e:
            logger.warning(
                "node_embedding_generation_failed",
                node_id=node_id,
                error_type=type(e).__name__,
                error_message=str(e),
            )

        return node_dict

    # ========================================================================
    # Citation Operations
    # ========================================================================

    def get_node_citations(
        self,
        node_id: str,
        page: int = 1,
        page_size: int | None = None,
    ) -> CitationListResponse:
        """Get all citations (source attributions) for a node.

        Returns where this entity was mentioned in source documents,
        with chunk content and source metadata.

        Args:
            node_id: Node ID (entity URI in RDF graph)
            page: Page number
            page_size: Items per page (uses settings default if not provided)

        Returns:
            Paginated list of citations with source and chunk data

        Raises:
            NotFoundError: If node not found

        """
        # Verify node exists
        node = self.graph_node_repository.get_node(node_id)
        if not node:
            msg = "Node"
            raise NotFoundError(msg, node_id)

        # Use settings default if not provided
        if page_size is None:
            page_size = self.settings.pagination.default_page_size

        # Enforce max page size for citations
        # Citations include full chunk content so we limit them more than simple node lists
        page_size = min(page_size, self.settings.pagination.max_citation_page_size)

        # Calculate offset
        offset = (page - 1) * page_size

        # Get citations from repository
        results, total = self.sql_node_repository.get_citations_for_node(
            node_id=node_id,
            offset=offset,
            limit=page_size,
        )

        # Build response objects
        citations = []
        for citation, source, chunk in results:
            citations.append(
                CitationResponse(
                    id=citation.id,
                    source=SourceReference(
                        id=source.id,
                        title=source.title or source.filename,  # Fallback to filename
                        source_type=source.source_type or "unknown",
                        origin_url=source.origin_url,
                    ),
                    chunk=ChunkReference(
                        id=chunk.id,
                        content=chunk.content,
                        page_number=chunk.page_number,
                        section=chunk.section,
                        chunk_metadata=chunk.chunk_metadata,
                    ),
                    confidence=citation.confidence,
                    extraction_method=citation.extraction_method,
                    context_snippet=citation.context_snippet,
                    citation_metadata=citation.citation_metadata,
                    created_at=citation.created_at,
                )
            )

        total_pages = (total + page_size - 1) // page_size

        return CitationListResponse(
            data=citations,
            pagination={
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1,
            },
        )

    def get_node_connections(
        self,
        node_id: str,
        sort_by: str = "edge_count",
        page: int = 1,
        page_size: int | None = None,
    ) -> ConnectionsResponse:
        """Get connected nodes for a given node.

        Returns nodes connected to this node with their total edge counts,
        sorted by the specified field.

        Args:
            node_id: Node ID to get connections for
            sort_by: Sort field (edge_count, label, relationship)
            page: Page number
            page_size: Items per page (uses settings default if not provided)

        Returns:
            ConnectionsResponse with connected nodes and pagination

        Raises:
            NotFoundError: If node not found

        """
        # Verify node exists
        node = self.graph_node_repository.get_node(node_id)
        if not node:
            msg = "Node"
            raise NotFoundError(msg, node_id)

        # Use settings default if not provided
        if page_size is None:
            page_size = self.settings.pagination.default_page_size

        # Enforce max page size
        page_size = min(page_size, self.settings.pagination.max_page_size)

        # Calculate offset
        offset = (page - 1) * page_size

        # Get connected nodes
        results, total = self.sql_node_repository.get_connected_nodes(
            node_id=node_id,
            sort_by=sort_by,
            offset=offset,
            limit=page_size,
        )

        # Convert to response models
        connections = [
            ConnectedNodeResponse(
                id=r["id"],
                label=r["label"],
                template_id=r["template_id"],
                edge_count=r["edge_count"],
                relationship=r["relationship"],
                direction=r["direction"],
            )
            for r in results
        ]

        total_pages = (total + page_size - 1) // page_size if page_size > 0 else 0

        return ConnectionsResponse(
            data=connections,
            pagination={
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1,
            },
        )
