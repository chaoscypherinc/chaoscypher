# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Node Tool Handlers.

Handles node search, retrieval, creation, update, and deletion operations.

Extracted from tool_executor.py for SRP compliance.
"""

from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.models import NodeCreate, NodeUpdate
from chaoscypher_core.services.workflows.tools.engine.chunk_hydration import (
    assign_chunk_aliases,
    clean_chunk_metadata,
    format_chunk_content,
)
from chaoscypher_core.services.workflows.tools.engine.handlers.decorators import tool_handler


if TYPE_CHECKING:
    from chaoscypher_core.ports.graph import GraphRepositoryProtocol
    from chaoscypher_core.ports.index import IndexingProtocol
    from chaoscypher_core.ports.search import SearchRepositoryProtocol
    from chaoscypher_core.settings import SearchSettings

logger = structlog.get_logger(__name__)


class NodeToolHandlers:
    """Handles all node-related tool operations."""

    def __init__(
        self,
        graph_repository: GraphRepositoryProtocol,
        search_repository: SearchRepositoryProtocol,
        indexing_repository: IndexingProtocol | None = None,
        embedding_callback: Callable | None = None,
        search_settings: SearchSettings | None = None,
    ) -> None:
        """Initialize the instance.

        Args:
            graph_repository: Repository for graph operations.
            search_repository: Repository for search operations.
            indexing_repository: Optional repository for chunk operations.
            embedding_callback: Async callback for generating embeddings.
            search_settings: Optional search settings for reranking configuration.

        """
        self.graph = graph_repository
        self.search = search_repository
        self.indexing = indexing_repository
        self.embedding_callback = embedding_callback
        self.search_settings = search_settings
        self._ranker: Any | None = None

    def _check_source_scope(self, node: Any, source_ids: list[str] | None) -> str | None:
        """Check if a node is within source scope.

        Args:
            node: Node object with source_id attribute
            source_ids: Allowed source IDs (None = no restriction)

        Returns:
            Error message if out of scope, None if allowed

        """
        if not source_ids:
            return None
        node_source_id = getattr(node, "source_id", None)
        if node_source_id and node_source_id not in source_ids:
            return f"Node '{node.label}' is not accessible in the current source scope"
        return None

    def _make_embedding_callback(
        self,
    ) -> Callable[[str], Coroutine[Any, Any, dict[str, Any]]] | None:
        """Return the injected embedding callback, or None if unavailable.

        Returns:
            Async callback that embeds text, or None if no callback provided.

        """
        return self.embedding_callback

    def _get_ranker(self) -> Any | None:
        """Get or create the CrossEncoder instance (lazy singleton).

        Returns:
            CrossEncoder instance, or None if unavailable.

        """
        if self._ranker is not None:
            return self._ranker
        try:
            from sentence_transformers import CrossEncoder

            model_name = "Alibaba-NLP/gte-reranker-modernbert-base"
            if self.search_settings:
                model_name = self.search_settings.rerank_model_name
            self._ranker = CrossEncoder(model_name)
            logger.info("cross_encoder_ranker_initialized", model=model_name)
            return self._ranker
        except Exception:
            logger.warning("cross_encoder_unavailable", exc_info=True)
            return None

    async def _rerank_chunks(self, query: str, chunks: list[dict], limit: int) -> list[dict]:
        """Re-rank chunks using sentence-transformers CrossEncoder.

        Args:
            query: User's search query.
            chunks: Hydrated chunk dicts from hybrid search.
            limit: Final number of chunks to return.

        Returns:
            Re-ordered (and trimmed) chunk list, or original on failure.

        """
        ranker = self._get_ranker()
        if not ranker or len(chunks) <= 1:
            return chunks[:limit]

        try:
            import asyncio

            # Build document list for CrossEncoder
            documents = [chunk.get("original_content", "") for chunk in chunks]

            # Run synchronous inference in thread to not block event loop
            results = await asyncio.to_thread(ranker.rank, query, documents, top_k=limit)

            # Reorder chunks by CrossEncoder score (results are sorted desc)
            reordered = []
            for result in results:
                idx = result["corpus_id"]
                if isinstance(idx, int) and 0 <= idx < len(chunks):
                    reordered.append(chunks[idx])

            logger.info(
                "rerank_completed",
                query=query,
                candidates=len(chunks),
                reranked=len(reordered),
                top_score=results[0].get("score") if results else None,
            )
            return reordered

        except Exception:
            logger.warning("rerank_failed_using_original_order", query=query, exc_info=True)
            return chunks[:limit]

    async def search_nodes(  # noqa: C901, PLR0912
        self,
        query: str,
        limit: int = 10,
        template_ids: list[str] | None = None,
        source_ids: list[str] | None = None,
    ) -> dict:
        """Search for nodes using hybrid search.

        Args:
            query: Text to search for
            limit: Maximum number of results (default 10)
            template_ids: Optional list of template IDs to filter results
                         (e.g., ["person", "character"] to find only people)
            source_ids: Optional list of source IDs to filter results

        Note: Search may also find document chunks (from RAG indexing).
        Chunk IDs are returned in chunks_found count but content is not
        hydrated (requires IndexingProtocol which is not available here).
        For full chunk content, use the SearchService directly.
        """
        # Use hybrid search with optional LLM embedding
        search_results = await self.search.hybrid_search(
            query, k=limit, embedding_provider_callback=self._make_embedding_callback()
        )

        # Separate node IDs from chunk IDs
        node_ids = []
        chunk_results = []
        for result_id, score in search_results:
            if result_id.startswith("chunk:"):
                # Chunk result - extract UUID and include in results
                chunk_uuid = result_id[6:]
                chunk_results.append({"chunk_id": chunk_uuid, "score": score})
            else:
                node_ids.append((result_id, score))

        # Fetch nodes
        node_id_list = [nid for nid, _ in node_ids]
        nodes = self.graph.get_nodes_batch(node_id_list)
        nodes_dict = {node.id: node for node in nodes}

        # Build template name lookup for filtering (if template_ids filter is used)
        template_name_map: dict[str, str] = {}  # template_id -> template_name
        if template_ids:
            # Get template info for all nodes to enable name-based filtering
            unique_template_ids = {node.template_id for node in nodes if node}
            for tid in unique_template_ids:
                try:
                    template = self.graph.get_template(tid)
                    if template:
                        # Handle both Pydantic model and dict returns
                        name = getattr(template, "name", None) or (
                            template.get("name", "") if isinstance(template, dict) else ""  # type: ignore[unreachable]
                        )
                        if name:
                            template_name_map[tid] = name
                except Exception:
                    logger.debug("template_lookup_failed", template_id=tid)

        # Build node results list with optional template filtering
        node_results = []
        filtered_count = 0
        for node_id, score in node_ids:
            node = nodes_dict.get(node_id)
            if node:
                # Apply template_id filter if specified
                if template_ids:
                    # Check if node's template matches any in the filter list
                    # Support matching by: template ID, template name, or partial match
                    template_name = template_name_map.get(node.template_id, "").lower()
                    matches_filter = any(
                        tid.lower() in node.template_id.lower()
                        or node.template_id.lower() in tid.lower()
                        or tid.lower() in template_name
                        or template_name in tid.lower()
                        for tid in template_ids
                    )
                    if not matches_filter:
                        filtered_count += 1
                        continue

                # Filter by source scope
                if source_ids:
                    node_source_id = getattr(node, "source_id", None)
                    if node_source_id and node_source_id not in source_ids:
                        filtered_count += 1
                        continue

                node_results.append(
                    {
                        "id": node.id,
                        "label": node.label,
                        "template_id": node.template_id,
                        "properties": node.properties,
                        "score": score,
                    }
                )

        # Analyze result types for better feedback
        result_types: dict[str, int] = {}
        for node_result in node_results:
            template_id = node_result.get("template_id", "unknown")
            # Extract template name from ID (e.g., "template_xxx" or "person")
            if isinstance(template_id, str):
                template_name = template_id.split("_")[-1] if "_" in template_id else template_id
            else:
                template_name = "unknown"
            result_types[template_name] = result_types.get(template_name, 0) + 1

        # Check if results might not match expected entity type
        person_indicators = {"person", "character", "author", "figure", "protagonist"}
        has_person_results = any(
            indicator in template_id.lower()
            for template_id in result_types
            for indicator in person_indicators
        )

        # Generate hint if results seem like abstract concepts (only when not filtering)
        hint = None
        if node_results and not has_person_results and not template_ids:
            hint = (
                "Results appear to be abstract concepts. If searching for a person/character, "
                "try search_nodes with template_ids=['person', 'character'], "
                "or use resolve_node(query) to find the canonical entity."
            )

        return {
            "success": True,
            "count": len(node_results),
            "nodes": node_results,
            "chunks_found": len(chunk_results),
            "filtered_out": filtered_count if template_ids else 0,
            "query": query,
            "template_filter": template_ids,
            "result_types": result_types,
            "hint": hint,
        }

    @tool_handler("search_chunks_failed")
    async def search_chunks(
        self,
        query: str,
        limit: int = 5,
        source_id: str | None = None,
        source_ids: list[str] | None = None,
    ) -> dict:
        """Search document chunks and return full content.

        Use for finding quotes, descriptions, or textual evidence in source documents.

        Args:
            query: Text to search for in document chunks
            limit: Maximum number of chunks to return (default 5)
            source_id: Optional single source ID to filter results
            source_ids: Optional list of source IDs to filter results

        Returns:
            Dict with success status and list of chunks with content

        """
        if not self.indexing:
            return {
                "success": False,
                "error": "Chunk search not available (no indexing repository)",
            }

        # Ensure minimum limit for LLM return count
        min_limit = self.search_settings.search_chunks_min_limit if self.search_settings else 5
        limit = max(limit, min_limit)

        # Determine whether reranking is active
        rerank_enabled = bool(self.search_settings and self.search_settings.enable_rerank)
        fetch_multiplier = (
            self.search_settings.rerank_candidate_multiplier
            if rerank_enabled and self.search_settings
            else 2
        )

        # Fetch enough candidates for reranking quality
        min_candidates = (
            self.search_settings.rerank_min_candidates
            if rerank_enabled and self.search_settings
            else 15
        )
        fetch_count = max(limit * fetch_multiplier, min_candidates)

        # Search with multiplied limit to get candidates for reranking
        # Use lower similarity threshold for RAG retrieval (default 0.55 is
        # too strict for many embedding models)
        search_results = await self.search.hybrid_search(
            query,
            k=fetch_count,
            embedding_provider_callback=self._make_embedding_callback(),
            min_similarity=0.3,
        )

        # Max chunks to collect before reranking trims to final limit
        max_chunks = fetch_count if rerank_enabled else limit

        # Filter to chunks only and hydrate content
        chunks: list[dict] = []
        source_filenames: dict[str, str] = {}
        for result_id, score in search_results:
            if result_id.startswith("chunk:"):
                chunk_uuid = result_id[6:]
                chunk_data = self.indexing.get_chunk_by_id(chunk_uuid)
                if chunk_data:
                    # Filter by source scope
                    chunk_source = chunk_data.get("source_id")
                    if source_ids and chunk_source not in source_ids:
                        continue
                    if source_id and chunk_source != source_id:
                        continue
                    # Resolve source filename for citation labels
                    original_content = chunk_data["content"]
                    chunk_source_id = chunk_data.get("source_id", "")
                    if chunk_source_id and chunk_source_id not in source_filenames:
                        get_source = getattr(self.indexing, "get_source", None)
                        if get_source:
                            db = chunk_data.get("database_name", "")
                            src = get_source(chunk_source_id, db)
                            source_filenames[chunk_source_id] = (
                                src.get("filename", "") if src else ""
                            )

                    # Use short alias (C0, C1, ...) instead of UUID
                    # for LLM readability — mapped back during enrichment
                    chunk_id = chunk_data["id"]
                    alias = f"C{len(chunks)}"
                    filename = source_filenames.get(chunk_source_id, "")

                    # Number sentences and build header via shared utility
                    numbered_content, sentence_count = format_chunk_content(
                        original_content, filename, alias
                    )

                    # Strip combined_content from metadata before sending
                    # to LLM — it contains sibling chunks' text which causes
                    # the LLM to quote from chunks it didn't actually search for.
                    chunk_meta = clean_chunk_metadata(chunk_data.get("chunk_metadata"))

                    chunks.append(
                        {
                            "chunk_id": chunk_id,
                            "chunk_alias": alias,
                            "content": numbered_content,
                            "original_content": original_content,
                            "source_id": chunk_source_id,
                            "filename": source_filenames.get(chunk_source_id, ""),
                            "chunk_index": chunk_data.get("chunk_index"),
                            "page_number": chunk_data.get("page_number"),
                            "section": chunk_data.get("section"),
                            "sentence_count": sentence_count,
                            "chunk_metadata": chunk_meta,
                            "score": score,
                        }
                    )
                    if len(chunks) >= max_chunks:
                        break

        # Re-rank if enabled, then trim to requested limit
        if rerank_enabled and len(chunks) > 1:
            chunks = await self._rerank_chunks(query, chunks, limit)
            # Re-assign aliases after reranking (C0, C1, ...)
            assign_chunk_aliases(chunks)
        else:
            chunks = chunks[:limit]

        logger.info(
            "search_chunks_completed",
            query=query,
            chunks_found=len(chunks),
            source_filter=source_ids or source_id,
            reranked=rerank_enabled,
        )

        return {
            "success": True,
            "count": len(chunks),
            "chunks": chunks,
            "query": query,
        }

    @tool_handler("get_node_failed")
    async def get_node(
        self,
        node_id: str | None = None,
        query: str | None = None,
        source_ids: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """Get node details.

        Args:
            node_id: Optional node ID to retrieve
            query: Optional search query to find the node first
            source_ids: Optional source scope filter
            **kwargs: Additional keyword arguments (unused)

        Returns:
            Dict with success status and node details

        """
        if not node_id and not query:
            return {"success": False, "error": "Either node_id or query must be provided"}

        # If query is provided, search for the node first
        if query and not node_id:
            logger.info("finding_node_via_query", query=query)
            search_results = self.search.keyword_search(query, limit=1)

            if not search_results:
                return {"success": False, "error": f"No nodes found matching query: {query}"}

            node_id = search_results[0][0]  # (node_id, score)
            logger.info("node_found_via_query", node_id=node_id, query=query)

        # At this point, node_id must be set (either passed in or from search)
        assert node_id is not None, "node_id must be set"

        # Get the node
        node = self.graph.get_node(node_id)
        if not node:
            return {"success": False, "error": f"Node not found: {node_id}"}

        scope_error = self._check_source_scope(node, source_ids)
        if scope_error:
            return {"success": False, "error": scope_error}

        # Convert datetime to ISO string for JSON serialization
        created_at = node.created_at.isoformat() if node.created_at else None
        updated_at = node.updated_at.isoformat() if node.updated_at else None

        return {
            "success": True,
            "node": {
                "id": node.id,
                "template_id": node.template_id,
                "label": node.label,
                "properties": node.properties,
                "source_id": node.source_id,
                "created_at": created_at,
                "updated_at": updated_at,
            },
            "search_query": query if query else None,
        }

    @tool_handler("create_node_failed")
    async def create_node(
        self,
        template_id: str,
        label: str,
        properties: dict[str, Any],
        source_ids: list[str] | None = None,
    ) -> dict:
        """Create a new node."""
        node = self.graph.create_node(
            NodeCreate(template_id=template_id, label=label, properties=properties)
        )

        return {
            "success": True,
            "message": f"Created node: {label}",
            "node_id": node.id,
            "node": {
                "id": node.id,
                "label": node.label,
                "template_id": node.template_id,
                "properties": node.properties,
            },
        }

    @tool_handler("update_node_failed")
    async def update_node(
        self,
        node_id: str,
        label: str | None = None,
        properties: dict[str, Any] | None = None,
        source_ids: list[str] | None = None,
    ) -> dict:
        """Update an existing node."""
        # Validate scope before updating
        if source_ids:
            existing = self.graph.get_node(node_id)
            if existing:
                scope_error = self._check_source_scope(existing, source_ids)
                if scope_error:
                    return {"success": False, "error": scope_error}

        update_data: dict[str, Any] = {}
        if label is not None:
            update_data["label"] = label
        if properties is not None:
            update_data["properties"] = properties

        node = self.graph.update_node(node_id, NodeUpdate(**update_data))
        if node is None:
            return {"success": False, "error": f"Failed to update node: {node_id}"}

        return {"success": True, "message": f"Updated node: {node.label}", "node_id": node.id}

    @tool_handler("delete_node_failed")
    async def delete_node(self, node_id: str, source_ids: list[str] | None = None) -> dict:
        """Delete a node."""
        # Validate scope before deleting
        if source_ids:
            existing = self.graph.get_node(node_id)
            if existing:
                scope_error = self._check_source_scope(existing, source_ids)
                if scope_error:
                    return {"success": False, "error": scope_error}

        success = self.graph.delete_node(node_id)
        if success:
            return {"success": True, "message": f"Deleted node: {node_id}"}
        return {"success": False, "error": "Failed to delete node"}

    @tool_handler("get_node_context_failed")
    async def get_node_context(
        self,
        node_id: str,
        include_edges: bool = True,
        include_chunks: bool = False,
        edge_limit: int = 20,
        chunk_limit: int = 5,
        source_ids: list[str] | None = None,
    ) -> dict:
        """Get comprehensive context for a node including edges, related nodes, and chunks.

        Args:
            node_id: The ID of the node to get context for
            include_edges: Whether to include edge information (default True)
            include_chunks: Whether to include document chunks mentioning this node
            edge_limit: Maximum edges to include (default 20)
            chunk_limit: Maximum chunks to include (default 5)
            source_ids: Optional source scope filter

        Returns:
            Dict with node details, edges, related nodes, and optionally chunks

        """
        # Get the main node
        node = self.graph.get_node(node_id)
        if not node:
            return {"success": False, "error": f"Node not found: {node_id}"}

        scope_error = self._check_source_scope(node, source_ids)
        if scope_error:
            return {"success": False, "error": scope_error}

        # Convert datetime to ISO string for JSON serialization
        created_at = node.created_at.isoformat() if node.created_at else None
        updated_at = node.updated_at.isoformat() if node.updated_at else None

        result = {
            "success": True,
            "node": {
                "id": node.id,
                "label": node.label,
                "template_id": node.template_id,
                "properties": node.properties,
                "source_id": node.source_id,
                "created_at": created_at,
                "updated_at": updated_at,
            },
        }

        if include_edges:
            # Get edges connected to this node using proper filters
            # (fetching all edges and filtering locally doesn't scale)
            outgoing_edges = self.graph.list_edges(source_node_id=node_id, limit=edge_limit)
            incoming_edges = self.graph.list_edges(target_node_id=node_id, limit=edge_limit)

            # Collect related node IDs
            related_node_ids: set[str] = set()
            for edge in outgoing_edges:
                related_node_ids.add(edge.target_node_id)
            for edge in incoming_edges:
                related_node_ids.add(edge.source_node_id)

            # Batch fetch related nodes
            related_nodes = self.graph.get_nodes_batch(list(related_node_ids))
            # Filter related nodes by source scope
            if source_ids:
                related_nodes = [
                    n
                    for n in related_nodes
                    if not getattr(n, "source_id", None) or n.source_id in source_ids
                ]
            nodes_dict = {n.id: n for n in related_nodes}

            # Build edge summaries
            result["outgoing_edges"] = [
                {
                    "edge_id": e.id,
                    "label": e.label,
                    "template_id": e.template_id,
                    "target": {
                        "id": e.target_node_id,
                        "label": nodes_dict[e.target_node_id].label
                        if e.target_node_id in nodes_dict
                        else "[unknown]",
                    },
                }
                for e in outgoing_edges
            ]

            result["incoming_edges"] = [
                {
                    "edge_id": e.id,
                    "label": e.label,
                    "template_id": e.template_id,
                    "source": {
                        "id": e.source_node_id,
                        "label": nodes_dict[e.source_node_id].label
                        if e.source_node_id in nodes_dict
                        else "[unknown]",
                    },
                }
                for e in incoming_edges
            ]

            result["edge_summary"] = {
                "outgoing_count": len(outgoing_edges),
                "incoming_count": len(incoming_edges),
                "total_related_nodes": len(related_node_ids),
            }

        # Include document chunks mentioning this node
        if include_chunks and self.indexing:
            chunk_results = await self.search_chunks(
                node.label, limit=chunk_limit, source_ids=source_ids
            )
            if chunk_results.get("success"):
                related_chunks = chunk_results.get("chunks", [])
                result["related_chunks"] = related_chunks
                result["chunks_count"] = len(related_chunks)

        return result

    @tool_handler("resolve_node_failed")
    async def resolve_node(
        self,
        query: str,
        include_alternatives: bool = True,
        max_alternatives: int = 3,
        source_ids: list[str] | None = None,
    ) -> dict:
        """Resolve an alias, nickname, or description to a canonical node.

        Use this when the user refers to an entity by a nickname, title, or
        descriptive phrase (e.g., "The Little Princess", "Natasha's suitor").

        Strategy:
        1. First try keyword search (with label boosting) - best for exact name matches
        2. If keyword search finds good matches, use those
        3. Fall back to hybrid/semantic search for descriptive queries

        Args:
            query: The alias, nickname, or description to resolve
            include_alternatives: Whether to include alternative matches (default True)
            max_alternatives: Maximum alternative matches to return (default 3)
            source_ids: Optional source scope filter

        Returns:
            Dict with the best matching node and confidence score

        """
        search_limit = max_alternatives + 2 if include_alternatives else 2
        search_method = "keyword"

        # Strategy 1: Try keyword search first (has label boosting)
        # This works best for exact or partial name matches like "Pierre" -> "Pierre Bezukhov"
        keyword_results = self.search.keyword_search(query, limit=search_limit)

        # Filter to only node results (not chunks)
        node_results = [
            (rid, score) for rid, score in keyword_results if not rid.startswith("chunk:")
        ]

        # If keyword search didn't find good results, try hybrid/semantic search
        # (semantic search is better for descriptive queries like "Napoleon's enemy")
        if not node_results or (node_results and node_results[0][1] < 1.0):
            hybrid_results = await self.search.hybrid_search(
                query,
                k=search_limit,
                embedding_provider_callback=self._make_embedding_callback(),
            )

            # Filter hybrid results to nodes only
            hybrid_node_results = [
                (rid, score) for rid, score in hybrid_results if not rid.startswith("chunk:")
            ]

            # Use hybrid results if they're better than keyword results
            if hybrid_node_results and (
                not node_results or hybrid_node_results[0][1] > node_results[0][1]
            ):
                node_results = hybrid_node_results
                search_method = "hybrid"

        # Filter by source scope
        if source_ids and node_results:
            filtered = []
            for rid, score in node_results:
                n = self.graph.get_node(rid)
                if n and not self._check_source_scope(n, source_ids):
                    filtered.append((rid, score))
            node_results = filtered

        if not node_results:
            return {
                "success": False,
                "error": f"Could not resolve '{query}' to any node",
                "query": query,
            }

        # Get the best match
        best_id, best_score = node_results[0]
        best_node = self.graph.get_node(best_id)

        if not best_node:
            return {"success": False, "error": f"Node {best_id} not found"}

        # Extract aliases from properties if they exist
        aliases: list[str] = []
        if best_node.properties:
            # Check common alias property names
            for alias_key in ["aliases", "nicknames", "also_known_as", "titles"]:
                alias_val = best_node.properties.get(alias_key)
                if isinstance(alias_val, list):
                    aliases.extend(alias_val)
                elif isinstance(alias_val, str) and alias_val:
                    aliases.append(alias_val)

        result: dict = {
            "success": True,
            "query": query,
            "resolved_node": {
                "id": best_node.id,
                "label": best_node.label,
                "template_id": best_node.template_id,
                "properties": best_node.properties,
            },
            "confidence": best_score,
            "search_method": search_method,
            "aliases": aliases,
        }

        # Include alternatives if requested
        if include_alternatives and len(node_results) > 1:
            alt_ids = [rid for rid, _ in node_results[1 : max_alternatives + 1]]
            alt_nodes = self.graph.get_nodes_batch(alt_ids)
            alt_dict = {n.id: n for n in alt_nodes}

            result["alternatives"] = [
                {
                    "id": rid,
                    "label": alt_dict[rid].label if rid in alt_dict else "[unknown]",
                    "confidence": score,
                }
                for rid, score in node_results[1 : max_alternatives + 1]
                if rid in alt_dict
            ]

        return result
