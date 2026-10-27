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

from collections.abc import Callable, Container, Iterable, Mapping
from typing import TYPE_CHECKING, Any, Protocol


if TYPE_CHECKING:
    from chaoscypher_core.models import Node
    from chaoscypher_core.ports.transactional import TransactionalSession


def has_no_source(source_id: str | None) -> bool:
    """Whether an item names no source: NULL and the empty string read alike.

    An item with no source (a manual or legacy node) is in every source
    scope, and no switched-off source hides it. Until 2026-10-06 the search's
    node scope read ``''`` as no source while the graph listings read NULL
    only (audit wave 6). The SQL form is
    :func:`chaoscypher_core.adapters.sqlite.repos.source_visibility.no_source_sql`;
    a test pins the two alike.
    """
    return not source_id


def node_in_source_scope(source_id: str | None, scope: Container[str]) -> bool:
    """Whether a node of ``source_id`` is in a source scope: it names no source, or one in it."""
    return has_no_source(source_id) or source_id in scope


def source_visible(source_id: str | None, disabled: Container[str]) -> bool:
    """Whether an item of ``source_id`` may answer: it names no source, or one not switched off.

    ``disabled`` holds the ids of the sources set ``enabled = 0``. The SQL
    form, which every search arm, scope count and graph listing reads, is
    :func:`chaoscypher_core.adapters.sqlite.repos.source_visibility.source_visible_sql`;
    a test pins the two alike.
    """
    return has_no_source(source_id) or source_id not in disabled


def attesting_sources(item: object) -> list[str]:
    """The sources that attest a graph row: its members when it has any, else its home source.

    A merged node is attested by every source it was merged from
    (``member_source_ids``, which the repository's node loads fill), and its
    ``source_id`` is only its home (#795, D7). A row with no members, or one
    loaded without them, reads its ``source_id`` as before; a row naming no
    source returns ``[]``. The SQL form is
    :func:`chaoscypher_core.adapters.sqlite.repos.source_visibility.attested_by_sql`.
    """
    members = getattr(item, "member_source_ids", None)
    if isinstance(members, list | tuple | set | frozenset) and members:
        return [str(sid) for sid in members]
    home = getattr(item, "source_id", None)
    return [] if has_no_source(home) else [str(home)]


def item_in_source_scope(item: object, scope: Container[str]) -> bool:
    """Whether a graph row is in a source scope: a person made it, it names no source, or a source in the scope attests it."""
    if getattr(item, "person_made", False):  # a person's row is in every lens and scope
        return True
    sources: list[str | None] = [*attesting_sources(item)] or [None]
    return any(node_in_source_scope(sid, scope) for sid in sources)


def item_visible(item: object, disabled: Container[str]) -> bool:
    """Whether a graph row may answer: a person made it, it names no source, or a source not switched off attests it."""
    if getattr(item, "person_made", False):  # a person's row is the person's: always shown
        return True
    sources: list[str | None] = [*attesting_sources(item)] or [None]
    return any(source_visible(sid, disabled) for sid in sources)


class SearchHits(list[tuple[str, float]]):
    """A search's ``(item_id, score)`` hits, best first, and what it found before its ``k`` cut.

    ``candidates_seen`` is how many distinct items the search's arms found
    that it would rank (for the hybrid search: keyword hits and vector hits
    at or above the similarity floor), counted before the cut to ``k``; None
    when the search does not say. A search whose candidates did not reach
    ``k`` read everything its scope holds that answers the query
    (:attr:`scope_exhausted`): a shorter result is the scope running out,
    not a fault. Until 2026-10-06 the GraphRAG handler compared its hybrid
    arm with the number of chunks in the scope, which ignores the floor and
    keyword misses, so ordinary low-relevance shortfalls were recorded as
    ``hybrid_search:scope_short`` (audit wave 6).
    """

    def __init__(
        self,
        hits: Iterable[tuple[str, float]] = (),
        *,
        k: int = 0,
        candidates_seen: int | None = None,
    ) -> None:
        """The hits, the ``k`` asked for and the candidates seen before the cut."""
        super().__init__(hits)
        self.k = k
        self.candidates_seen = candidates_seen

    @property
    def scope_exhausted(self) -> bool | None:
        """Whether the candidates fell short of ``k`` (everything that answers was read); None when unknown."""
        if self.candidates_seen is None:
            return None
        return self.candidates_seen < self.k


class SearchRepositoryProtocol(Protocol):
    """Interface for search operations.

    Implementations provide keyword search (fulltext) and vector similarity
    search (semantic) over graph nodes and document chunks.

    Search methods:
    - keyword_search: Fast full-text search (no LLM needed)
    - vector_search: Direct vector similarity (embedding provided)
    - semantic_search: Text-to-embedding-to-vector search (async, needs callback)
    - hybrid_search: Keyword and vector rankings fused by reciprocal rank (async)
    """

    def keyword_search(
        self,
        query: str,
        limit: int = 10,
        item_type: str | None = "node",
        source_ids: Iterable[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Perform full-text keyword search (any significant word or quoted phrase matches).

        Args:
            query: Search query string
            limit: Maximum number of results (default 10)
            item_type: "node" (default), "chunk", or None for both.
            source_ids: A source scope applied before the ``limit``; None for every source.
                A switched-off source's items never match (:func:`source_visible`).

        Returns:
            List of (node_id, score) tuples, sorted by relevance descending.

        """
        ...

    def vector_search(
        self,
        query_embedding: list[float],
        k: int = 10,
        item_type: str | None = None,
        source_ids: Iterable[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Find k nearest neighbors to query embedding.

        Args:
            query_embedding: Query embedding vector (must match dimensionality
                            of indexed vectors, typically 1024)
            k: Number of results to return (default 10)
            item_type: Optional filter by item type ("node", "chunk", "template");
                None searches nodes and chunks (templates only by name).
            source_ids: A source scope applied before the ``k`` cut (chunks
                and nodes; templates come from no source); None for every source.
                A switched-off source's items never match (:func:`source_visible`).

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
        item_type: str | None = None,
    ) -> list[tuple[str, float]]:
        """Perform semantic search using query text.

        Generates embedding for the query via callback, then performs vector search.

        Args:
            query_text: Text to search for
            k: Number of results to return (default 10)
            embedding_provider_callback: Async callback that takes query text
                and returns dict with "embedding" key containing the vector.
                Example: async def(text: str) -> {"embedding": [...]}
            item_type: Optional filter ("node", "chunk", "template"); None
                searches nodes and chunks.

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
        item_type: str | None = None,
        source_ids: Iterable[str] | None = None,
    ) -> SearchHits:
        """Perform hybrid search: keyword and vector rankings fused by reciprocal rank.

        Strategy:
        - Short queries (< 3 chars): keyword only
        - Otherwise: both arms search ``item_type`` and their rankings are fused

        Args:
            query_text: Text to search for
            k: Number of results to return (default 10)
            embedding_provider_callback: Async callback for generating embeddings
            min_similarity: Minimum similarity score (0-1) to accept semantic results
            item_type: "node", "chunk", or None for both.
            source_ids: A source scope applied inside both arms, before their cut;
                None for every source.

        Returns:
            The (node_id, score) hits, best first, with how many candidates
            both arms found before the cut (:class:`SearchHits`).

        """
        ...

    def count_in_scope(self, item_type: str, source_ids: Iterable[str] | None = None) -> int:
        """How many items of ``item_type`` (``"chunk"``, ``"node"``) a source scope holds that may answer.

        A switched-off source's items are never counted (:func:`source_visible`).

        Args:
            item_type: ``"chunk"`` or ``"node"``.
            source_ids: The scope; None counts every item.

        Returns:
            The number of items a search in that scope could return.

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

    def rebuild_node_fulltext(
        self,
        nodes: Iterable[Node | Mapping[str, Any]] | None = None,
        *,
        session: TransactionalSession | None = None,
    ) -> int:
        """Make the node full-text index hold exactly the graph's nodes (labels and current aliases).

        Rows of nodes no longer in the graph are deleted; vectors are untouched.
        Called by a full reindex; a caller that knows which nodes it changed
        (the identity resolver) takes :meth:`refresh_node_fulltext`. The nodes are read from the graph inside
        the rewrite's own transaction (a list read earlier can miss a node
        committed since); ``nodes`` is checked against it, and used only by
        an index with no graph behind it.

        Args:
            nodes: The nodes the caller expects the graph to hold (graph nodes,
                or mappings with ``id``, ``label`` and ``properties``), or None.
            session: Optional caller session to share a transaction with.

        Returns:
            The number of rows the index holds afterwards.

        """
        ...

    def refresh_node_fulltext(
        self, node_ids: Iterable[str], *, session: TransactionalSession | None = None
    ) -> int:
        """Rewrite the full-text rows of ``node_ids`` from the graph as it is now; nothing else.

        The scoped form of :meth:`rebuild_node_fulltext` for a caller that
        knows which nodes it changed (the identity resolver): each id still in
        the graph gets its row rewritten from the node as stored, an id no
        longer there loses its row, and no other row is read or written.
        Vectors are untouched.

        Args:
            node_ids: The nodes whose rows follow the graph.
            session: Optional caller session to share a transaction with.

        Returns:
            The number of rows written (ids still in the graph).

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
