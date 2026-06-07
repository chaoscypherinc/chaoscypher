# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Search repository interface for chaoscypher-engine.

Defines Protocol for search operations (keyword, vector, semantic, hybrid).
Vector search uses sqlite-vec stored in app.db for WAL-mode concurrency safety.

Tracks the active embedding model name and vector dimensions in a
``search_metadata`` table.  When the configured model or dimensions
change, sets ``needs_full_reindex`` so callers can trigger background
re-embedding.  Per-item dimension mismatches during indexing are queued
via ``schedule_reindex()`` and flushed asynchronously by the caller.

Main implementation: chaoscypher_core.adapters.sqlite.repos.SearchRepository
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol


if TYPE_CHECKING:
    from chaoscypher_core.models import Node
    from chaoscypher_core.ports.transactional import TransactionalSession


class SearchRepositoryProtocol(Protocol):
    """Interface for search operations.

    Implementations provide keyword search (fulltext) and vector similarity
    search (semantic) over graph nodes and document chunks.

    Search methods:
    - keyword_search: Fast full-text search (no LLM needed)
    - vector_search: Direct vector similarity (embedding provided)
    - semantic_search: Text-to-embedding-to-vector search (async, needs callback)
    - hybrid_search: Semantic with keyword fallback (async, needs callback)
    """

    def keyword_search(self, query: str, limit: int = 10) -> list[tuple[str, float]]:
        """Perform full-text keyword search.

        Args:
            query: Search query string
            limit: Maximum number of results (default 10)

        Returns:
            List of (node_id, score) tuples, sorted by relevance descending.

        """
        ...

    def vector_search(
        self,
        query_embedding: list[float],
        k: int = 10,
        item_type: str | None = None,
    ) -> list[tuple[str, float]]:
        """Find k nearest neighbors to query embedding.

        Args:
            query_embedding: Query embedding vector (must match dimensionality
                            of indexed vectors, typically 1024)
            k: Number of results to return (default 10)
            item_type: Optional filter by item type ("node", "chunk", "template")

        Returns:
            List of (node_id, similarity_score) tuples,
            sorted by similarity descending (highest first).

            Similarity scores in range [0.0, 1.0] where:
            - 1.0 = perfect match (identical vectors)
            - 0.0 = no similarity (orthogonal vectors)

        Notes:
            - Empty list if no nodes have embeddings
            - May return fewer than k results if not enough nodes exist

        """
        ...

    async def semantic_search(
        self,
        query_text: str,
        k: int = 10,
        embedding_provider_callback: Callable[[str], Any] | None = None,
    ) -> list[tuple[str, float]]:
        """Perform semantic search using query text.

        Generates embedding for the query via callback, then performs vector search.

        Args:
            query_text: Text to search for
            k: Number of results to return (default 10)
            embedding_provider_callback: Async callback that takes query text
                and returns dict with "embedding" key containing the vector.
                Example: async def(text: str) -> {"embedding": [...]}

        Returns:
            List of (node_id, similarity_score) tuples

        """
        ...

    async def hybrid_search(
        self,
        query_text: str,
        k: int = 10,
        embedding_provider_callback: Callable[[str], Any] | None = None,
        min_similarity: float = 0.55,
    ) -> list[tuple[str, float]]:
        """Perform hybrid search: semantic with keyword fallback.

        Strategy:
        - Short queries (< 3 chars): keyword only
        - Otherwise: semantic first, keyword fallback if no good results

        Args:
            query_text: Text to search for
            k: Number of results to return (default 10)
            embedding_provider_callback: Async callback for generating embeddings
            min_similarity: Minimum similarity score (0-1) to accept semantic results

        Returns:
            List of (node_id, similarity_score) tuples

        """
        ...

    def index_node(self, node: Node, *, session: TransactionalSession | None = None) -> None:
        """Index a node for full-text and vector search.

        When ``session`` is passed, the write joins the caller's
        transaction: no auto-commit, and exceptions propagate so the
        caller can roll back. When ``session`` is None, opens a
        standalone connection with best-effort semantics (errors logged,
        not raised) to preserve historical behavior.

        Args:
            node: Node to index
            session: Optional caller session to share a transaction with.

        """
        ...

    def delete_node(self, node_id: str, *, session: TransactionalSession | None = None) -> None:
        """Remove a node from both keyword and vector indexes.

        See :meth:`index_node` for the ``session`` contract.

        Args:
            node_id: Node ID to remove
            session: Optional caller session to share a transaction with.

        """
        ...

    def delete_nodes_batch(
        self, node_ids: list[str], *, session: TransactionalSession | None = None
    ) -> int:
        """Remove multiple nodes from both keyword and vector indexes.

        Used for idempotent commit: cleans up previously indexed nodes
        before re-committing. See :meth:`index_node` for the ``session``
        contract.

        Args:
            node_ids: List of node IDs to remove from search indexes.
            session: Optional caller session to share a transaction with.

        Returns:
            Number of nodes removed.

        """
        ...

    def get_index_stats(self) -> dict[str, Any]:
        """Get statistics about the search indexes.

        Returns:
            Dict with index statistics

        """
        ...

    def reindex_all_nodes(
        self, nodes: list[Node], *, session: TransactionalSession | None = None
    ) -> None:
        """Reindex all nodes (useful after bulk import or index corruption).

        See :meth:`index_node` for the ``session`` contract.

        Args:
            nodes: List of all nodes to reindex
            session: Optional caller session to share a transaction with.

        """
        ...

    def index_nodes_batch(
        self, nodes: list[Node], *, session: TransactionalSession | None = None
    ) -> None:
        """Index multiple nodes in batch.

        See :meth:`index_node` for the ``session`` contract.

        Args:
            nodes: List of Node objects to index
            session: Optional caller session to share a transaction with.

        """
        ...

    def template_semantic_search(
        self,
        query_embedding: list[float],
        k: int = 10,
        min_similarity: float = 0.5,
    ) -> list[tuple[str, float]]:
        """Perform semantic search over templates.

        Args:
            query_embedding: Query embedding vector
            k: Number of results to return
            min_similarity: Minimum similarity score (0-1) to include (default 0.5)

        Returns:
            List of (template_id, similarity_score) tuples

        """
        ...

    def index_node_embedding(
        self,
        node_id: str,
        embedding: list[float],
        *,
        session: TransactionalSession | None = None,
    ) -> None:
        """Index a single node's embedding for vector search.

        See :meth:`index_node` for the ``session`` contract.

        Args:
            node_id: ID of the node
            embedding: Embedding vector
            session: Optional caller session to share a transaction with.

        """
        ...

    def index_template(
        self,
        template_id: str,
        embedding: list[float],
        *,
        session: TransactionalSession | None = None,
    ) -> None:
        """Index a template embedding for semantic search.

        See :meth:`index_node` for the ``session`` contract.

        Args:
            template_id: Template ID to index
            embedding: Embedding vector for the template
            session: Optional caller session to share a transaction with.

        """
        ...

    def index_embeddings_batch(
        self,
        embeddings: list[tuple[str, list[float]]],
        item_type: str = "node",
        text_lookup: dict[str, str] | None = None,
        *,
        session: TransactionalSession | None = None,
    ) -> int:
        """Batch index embeddings.

        See :meth:`index_node` for the ``session`` contract.

        Args:
            embeddings: List of (item_id, embedding) tuples
            item_type: Type of items ("node", "chunk", "template")
            text_lookup: Optional mapping of item_id to source text for
                re-embedding items with dimension mismatches.
            session: Optional caller session to share a transaction with.

        Returns:
            Number of embeddings indexed

        """
        ...

    def remove_embedding(
        self,
        item_id: str,
        item_type: str,
        *,
        session: TransactionalSession | None = None,
    ) -> None:
        """Remove an embedding from the per-type vector index.

        See :meth:`index_node` for the ``session`` contract.

        Args:
            item_id: ID of item to remove (may include prefix like "chunk:xxx")
            item_type: Type of item ("node", "chunk", "template") — selects
                which per-type vec0 table to delete from.
            session: Optional caller session to share a transaction with.

        """
        ...

    @property
    def has_pending_reindex(self) -> bool:
        """Check if there are items queued for re-embedding.

        Returns:
            True if the reindex queue is non-empty.

        """
        ...

    @property
    def needs_full_reindex(self) -> bool:
        """Whether the embedding model or dimensions changed since last init.

        When True, every per-type vec0 table is stale and should be
        re-embedded with the current model.

        Returns:
            True if a full reindex is needed.

        """
        ...

    def schedule_reindex(
        self,
        item_id: str,
        text: str,
        item_type: str,
    ) -> None:
        """Queue an item for async re-embedding.

        Args:
            item_id: ID of the item
            text: Source text to re-embed
            item_type: Type of item ("node", "chunk", "template")

        """
        ...

    async def flush_reindex(
        self,
        batch_embed_fn: Callable[[list[str]], Any],
        *,
        session: TransactionalSession | None = None,
    ) -> int:
        """Re-embed and index all queued items.

        See :meth:`index_node` for the ``session`` contract.

        Args:
            batch_embed_fn: Async callable taking list of texts,
                returning list of embedding vectors.
            session: Optional caller session to share a transaction with.

        Returns:
            Number of items re-indexed.

        """
        ...

    async def flush_reindex_with_service(
        self,
        embedding_service: Any,
        *,
        session: TransactionalSession | None = None,
    ) -> int:
        """Convenience wrapper that flushes using an embedding provider.

        See :meth:`index_node` for the ``session`` contract.

        Args:
            embedding_service: Embedding provider implementing
                ``EmbeddingProviderProtocol`` with ``batch_embed(texts)``
                method returning an object with ``.embeddings`` attribute.
            session: Optional caller session to share a transaction with.

        Returns:
            Number of items re-indexed.

        """
        ...
