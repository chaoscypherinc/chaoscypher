# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""GraphRAG Tool Handlers.

Implements the full GraphRAG retrieval pipeline: embed query, match seed
entities via vector search, run Personalized PageRank, assemble graph
context, retrieve provenance and vector chunks, then merge via RRF.

Extracted as a standalone handler class for SRP compliance and
strategy-pattern delegation from ToolExecutorService.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.app_config import get_settings
from chaoscypher_core.services.graph.engine.algorithms import calculate_pagerank
from chaoscypher_core.services.workflows.tools.engine.chunk_hydration import (
    assign_chunk_aliases,
    clean_chunk_metadata,
    format_chunk_content,
)
from chaoscypher_core.utils.rrf import reciprocal_rank_fusion


if TYPE_CHECKING:
    from chaoscypher_core.ports.graph import GraphRepositoryProtocol
    from chaoscypher_core.ports.index import IndexingProtocol
    from chaoscypher_core.ports.search import SearchRepositoryProtocol
    from chaoscypher_core.settings import EngineSettings, GraphRAGSettings

logger = structlog.get_logger(__name__)


def _default_graphrag_settings() -> GraphRAGSettings:
    """Create default GraphRAG settings when none are provided.

    Returns:
        A ``GraphRAGSettings`` instance with all defaults.

    """
    from chaoscypher_core.settings import GraphRAGSettings

    return GraphRAGSettings()


class GraphRAGToolHandlers:
    """Handles the graphrag_search tool: graph-enhanced retrieval pipeline.

    Fuses Personalized PageRank over the knowledge graph with hybrid
    vector/keyword search and RRF merging to produce context that is both
    structurally and semantically relevant to the user query.
    """

    def __init__(
        self,
        graph_repository: GraphRepositoryProtocol,
        search_repository: SearchRepositoryProtocol,
        indexing_repository: IndexingProtocol | None = None,
        source_storage: Any | None = None,
        embedding_callback: Callable[..., Any] | None = None,
        settings: EngineSettings | None = None,
        database_name: str = "default",
    ) -> None:
        """Initialize the instance.

        Args:
            graph_repository: Repository for graph node/edge operations.
            search_repository: Repository for keyword and vector search.
            indexing_repository: Optional repository for chunk hydration.
            source_storage: Optional source storage for citation lookups.
            embedding_callback: Async callback that embeds text and returns
                an object with an ``embedding`` attribute (or dict key).
            settings: Full engine settings (``settings.graphrag`` is used).
            database_name: Active database name for citation queries.

        """
        self.graph = graph_repository
        self.search = search_repository
        self.indexing = indexing_repository
        self.source_storage = source_storage
        self.embedding_callback = embedding_callback
        self._graphrag: GraphRAGSettings = (
            settings.graphrag if settings else _default_graphrag_settings()
        )
        self.database_name = database_name

    # ------------------------------------------------------------------
    # Pipeline step 1: Embed query
    # ------------------------------------------------------------------

    async def _embed_query(self, query: str) -> list[float] | None:
        """Generate an embedding vector for the user query.

        Args:
            query: Raw query text.

        Returns:
            Embedding vector, or ``None`` if the callback is missing or
            fails (triggers keyword-only fallback in the main pipeline).

        """
        if not self.embedding_callback:
            logger.debug("graphrag_no_embedding_callback")
            return None
        try:
            result = await self.embedding_callback(query)
            # Support both attribute access and dict access
            if hasattr(result, "embedding"):
                return result.embedding  # type: ignore[no-any-return]
            if isinstance(result, dict):
                return result.get("embedding")
            return None
        except Exception:
            logger.warning("graphrag_embed_query_failed", query=query, exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Pipeline step 2: Match seed entities
    # ------------------------------------------------------------------

    def _match_seed_entities(
        self,
        query_embedding: list[float],
        seed_limit: int,
    ) -> list[tuple[str, float]]:
        """Find seed entities via vector similarity.

        Queries the vector search index, filters out chunk results, and applies
        the similarity threshold from settings.

        Args:
            query_embedding: Embedding vector for the query.
            seed_limit: Maximum number of seed entities to return.

        Returns:
            List of ``(node_id, similarity_score)`` tuples.

        """
        try:
            overfetch = seed_limit * self._graphrag.vector_overfetch_multiplier
            raw_results = self.search.vector_search(query_embedding, k=overfetch)

            # Keep only node IDs (filter out "chunk:" prefix IDs)
            threshold = self._graphrag.seed_similarity_threshold
            seeds = [
                (rid, score)
                for rid, score in raw_results
                if not rid.startswith("chunk:") and score >= threshold
            ]
            return seeds[:seed_limit]
        except Exception:
            logger.warning("graphrag_seed_match_failed", exc_info=True)
            return []

    # ------------------------------------------------------------------
    # Pipeline step 3: Personalized PageRank
    # ------------------------------------------------------------------

    def _run_ppr(
        self,
        seeds: list[tuple[str, float]],
        source_ids: list[str] | None,
    ) -> dict[str, float]:
        """Run Personalized PageRank seeded on matched entities.

        Args:
            seeds: Seed entities as ``(node_id, score)`` pairs.
            source_ids: Optional source scope filter for graph loading.

        Returns:
            Mapping of node ID to PPR score. Empty dict when the graph
            has no nodes.

        """
        try:
            max_nodes = self._graphrag.max_graph_nodes
            # Load graph nodes (use minimal variant when available)
            if hasattr(self.graph, "list_nodes_minimal"):
                nodes = list(self.graph.list_nodes_minimal(limit=max_nodes))
            else:
                nodes = list(self.graph.list_nodes(limit=max_nodes))

            if not nodes:
                return {}

            # Scope filter
            if source_ids:
                nodes = [
                    n
                    for n in nodes
                    if not getattr(n, "source_id", None) or n.source_id in source_ids
                ]

            # Load edges
            edge_limit = max_nodes * 4
            if hasattr(self.graph, "list_edges_minimal"):
                edges = list(self.graph.list_edges_minimal(limit=edge_limit))
            else:
                edges = list(self.graph.list_edges(limit=edge_limit))

            if source_ids:
                node_id_set = {n.id for n in nodes}
                edges = [
                    e
                    for e in edges
                    if e.source_node_id in node_id_set and e.target_node_id in node_id_set
                ]

            if not nodes:
                return {}

            personalization = dict(seeds)

            pr_result = calculate_pagerank(
                nodes,
                edges,
                damping=self._graphrag.ppr_damping,
                personalization=personalization,
            )
            scores: dict[str, float] = pr_result.get("pagerank_scores", {})
            return scores
        except Exception:
            logger.warning("graphrag_ppr_failed", exc_info=True)
            return {}

    # ------------------------------------------------------------------
    # Pipeline step 4: Assemble graph context
    # ------------------------------------------------------------------

    def _assemble_graph_context(
        self,
        ppr_scores: dict[str, float],
        seeds: list[tuple[str, float]],
    ) -> dict[str, Any]:
        """Build a structured graph context from PPR results.

        Fetches node metadata and 1-hop edges for the top-scoring
        entities and formats them as triples.

        Args:
            ppr_scores: Mapping of node ID to PPR score.
            seeds: Original seed ``(node_id, score)`` pairs.

        Returns:
            Dict with ``seed_entities``, ``related_entities``,
            ``relationships`` (triples), and ``summary`` keys.

        """
        try:
            seed_ids = {sid for sid, _ in seeds}
            ppr_top_k = self._graphrag.ppr_top_k
            max_triples = self._graphrag.max_triples

            # Sort by PPR score descending, take top K
            sorted_ids = sorted(ppr_scores, key=ppr_scores.get, reverse=True)[:ppr_top_k]  # type: ignore[arg-type]

            if not sorted_ids:
                return {}

            # Fetch 1-hop edges for top entities
            triples: list[dict[str, str]] = []
            # Collect all neighbor node IDs to batch-fetch labels later
            neighbor_ids: set[str] = set()

            edges_by_node: dict[str, list[Any]] = {}
            edge_query_limit = get_settings().batching.graphrag_edge_query_limit
            for nid in sorted_ids:
                outgoing = list(self.graph.list_edges(source_node_id=nid, limit=edge_query_limit))
                incoming = list(self.graph.list_edges(target_node_id=nid, limit=edge_query_limit))
                edges_by_node[nid] = outgoing + incoming
                for e in outgoing:
                    neighbor_ids.add(e.target_node_id)
                for e in incoming:
                    neighbor_ids.add(e.source_node_id)

            # Single batch-fetch for all nodes (sorted + neighbors)
            all_needed = neighbor_ids | set(sorted_ids)
            all_fetched = self.graph.get_nodes_batch(list(all_needed))
            nodes_map: dict[str, Any] = {n.id: n for n in all_fetched}
            label_map: dict[str, str] = {n.id: n.label for n in all_fetched}
            template_map = self._resolve_template_names(all_fetched)

            # Classify into seed vs related
            seed_entities: list[dict[str, Any]] = []
            related_entities: list[dict[str, Any]] = []
            for nid in sorted_ids:
                node = nodes_map.get(nid)
                if not node:
                    continue
                entry = {
                    "id": nid,
                    "label": node.label,
                    "template_id": node.template_id,
                    "ppr_score": ppr_scores.get(nid, 0.0),
                }
                if nid in seed_ids:
                    seed_entities.append(entry)
                else:
                    related_entities.append(entry)

            seen_triples: set[tuple[str, str, str]] = set()
            for nid in sorted_ids:
                for edge in edges_by_node.get(nid, []):
                    src_label = label_map.get(edge.source_node_id, edge.source_node_id)
                    tgt_label = label_map.get(edge.target_node_id, edge.target_node_id)
                    triple_key = (edge.source_node_id, edge.label, edge.target_node_id)
                    if triple_key not in seen_triples:
                        seen_triples.add(triple_key)
                        triples.append(
                            {
                                "source": src_label,
                                "source_id": edge.source_node_id,
                                "label": edge.label,
                                "target": tgt_label,
                                "target_id": edge.target_node_id,
                            }
                        )
                    if len(triples) >= max_triples:
                        break
                if len(triples) >= max_triples:
                    break

            summary = self._build_summary(triples, template_map)
            entity_count = len(seed_entities) + len(related_entities)
            triple_count = len(triples)

            return {
                "seed_entities": seed_entities,
                "related_entities": related_entities,
                "relationships": triples,
                "entity_count": entity_count,
                "triple_count": triple_count,
                "summary": summary,
            }
        except Exception:
            logger.warning("graphrag_assemble_context_failed", exc_info=True)
            return {}

    def _resolve_template_names(self, nodes: list[Any]) -> dict[str, str]:
        """Build a mapping from node ID to human-readable template name.

        Resolves template IDs to their display names (e.g. "Character",
        "Location") instead of raw UUIDs, making the graph summary
        useful for the LLM.

        Args:
            nodes: List of node objects with ``id`` and ``template_id``.

        Returns:
            Dict mapping node ID to template name (empty string if none).

        """
        template_ids = {n.template_id for n in nodes if n.template_id}
        name_cache: dict[str, str] = {}
        for tid in template_ids:
            tmpl = self.graph.get_template(tid)
            name_cache[tid] = tmpl.name if tmpl else tid
        return {n.id: name_cache.get(n.template_id, "") if n.template_id else "" for n in nodes}

    @staticmethod
    def _build_summary(
        triples: list[dict[str, str]],
        template_map: dict[str, str],
    ) -> str:
        """Build a truncated human-readable summary from triples.

        Limits output to ~4K characters (~1K tokens) to avoid flooding
        the LLM context window while still providing useful relationship
        context.

        Args:
            triples: List of triple dicts with source, target, label keys.
            template_map: Mapping of node ID to template ID for type labels.

        Returns:
            Newline-separated summary string, truncated if necessary.

        """
        lines: list[str] = []
        for t in triples:
            src_type = template_map.get(t["source_id"], "")
            tgt_type = template_map.get(t["target_id"], "")
            src_suffix = f" ({src_type})" if src_type else ""
            tgt_suffix = f" ({tgt_type})" if tgt_type else ""
            lines.append(f"{t['source']}{src_suffix} {t['label']} {t['target']}{tgt_suffix}.")
        summary = "\n".join(lines)

        max_chars = 4000
        if len(summary) > max_chars:
            truncated = summary[:max_chars].rsplit("\n", 1)[0]
            omitted = len(lines) - truncated.count("\n") - 1
            summary = f"{truncated}\n... ({omitted} more triples omitted)"

        return summary

    # ------------------------------------------------------------------
    # Pipeline step 5: Retrieve provenance chunks (citation-based)
    # ------------------------------------------------------------------

    async def _retrieve_provenance_chunks(
        self,
        ppr_top_node_ids: list[str],
        source_ids: list[str] | None,
    ) -> list[dict[str, Any]]:
        """Retrieve document chunks linked to top PPR entities via citations.

        Args:
            ppr_top_node_ids: Node IDs from the graph context.
            source_ids: Optional source scope filter.

        Returns:
            List of hydrated chunk dicts with provenance metadata.

        """
        if not self.source_storage or not self.indexing:
            return []
        try:
            citations = self.source_storage.get_citations_batch(
                self.database_name,
                entity_uris=ppr_top_node_ids,
                source_ids=source_ids,
            )

            # Extract unique chunk IDs
            chunk_ids: list[str] = []
            seen: set[str] = set()
            for cit in citations:
                cid = cit.get("chunk_id")
                if cid and cid not in seen:
                    seen.add(cid)
                    chunk_ids.append(cid)

            # Hydrate chunks
            chunks: list[dict[str, Any]] = []
            source_filenames: dict[str, str] = {}
            for cid in chunk_ids:
                chunk_data = self.indexing.get_chunk_by_id(cid)
                if not chunk_data:
                    continue
                chunk_source_id = chunk_data.get("source_id", "")

                # Source scope filter
                if source_ids and chunk_source_id not in source_ids:
                    continue

                # Resolve filename
                if chunk_source_id and chunk_source_id not in source_filenames:
                    get_source = getattr(self.indexing, "get_source", None)
                    if get_source:
                        db = chunk_data.get("database_name", "")
                        src = get_source(chunk_source_id, db)
                        source_filenames[chunk_source_id] = src.get("filename", "") if src else ""

                original_content = chunk_data.get("content", "")
                filename = source_filenames.get(chunk_source_id, "")
                alias = f"C{len(chunks)}"

                numbered_content, sentence_count = format_chunk_content(
                    original_content, filename, alias
                )
                chunk_meta = clean_chunk_metadata(chunk_data.get("chunk_metadata"))

                chunks.append(
                    {
                        "chunk_id": chunk_data["id"],
                        "chunk_alias": alias,
                        "content": numbered_content,
                        "original_content": original_content,
                        "source_id": chunk_source_id,
                        "filename": filename,
                        "chunk_index": chunk_data.get("chunk_index"),
                        "page_number": chunk_data.get("page_number"),
                        "section": chunk_data.get("section"),
                        "sentence_count": sentence_count,
                        "chunk_metadata": chunk_meta,
                        "score": 1.0,  # provenance chunks get max score
                        "retrieval_origin": "provenance",
                    }
                )
            return chunks
        except Exception:
            logger.warning("graphrag_provenance_retrieval_failed", exc_info=True)
            return []

    # ------------------------------------------------------------------
    # Pipeline step 6: Retrieve vector/hybrid chunks
    # ------------------------------------------------------------------

    async def _retrieve_vector_chunks(
        self,
        query: str,
        limit: int,
        source_ids: list[str] | None,
    ) -> list[dict[str, Any]]:
        """Retrieve document chunks via hybrid (semantic + keyword) search.

        Args:
            query: User query text.
            limit: Target number of chunks.
            source_ids: Optional source scope filter.

        Returns:
            List of hydrated chunk dicts.

        """
        if not self.indexing:
            return []
        try:
            search_results = await self.search.hybrid_search(
                query,
                k=limit * 2,
                embedding_provider_callback=self.embedding_callback,
                min_similarity=0.3,
            )

            chunks: list[dict[str, Any]] = []
            source_filenames: dict[str, str] = {}
            for result_id, score in search_results:
                if not result_id.startswith("chunk:"):
                    continue

                chunk_uuid = result_id[6:]
                chunk_data = self.indexing.get_chunk_by_id(chunk_uuid)
                if not chunk_data:
                    continue

                chunk_source_id = chunk_data.get("source_id", "")
                if source_ids and chunk_source_id not in source_ids:
                    continue

                # Resolve filename
                if chunk_source_id and chunk_source_id not in source_filenames:
                    get_source = getattr(self.indexing, "get_source", None)
                    if get_source:
                        db = chunk_data.get("database_name", "")
                        src = get_source(chunk_source_id, db)
                        source_filenames[chunk_source_id] = src.get("filename", "") if src else ""

                original_content = chunk_data.get("content", "")
                filename = source_filenames.get(chunk_source_id, "")
                alias = f"C{len(chunks)}"

                numbered_content, sentence_count = format_chunk_content(
                    original_content, filename, alias
                )
                chunk_meta = clean_chunk_metadata(chunk_data.get("chunk_metadata"))

                chunks.append(
                    {
                        "chunk_id": chunk_data["id"],
                        "chunk_alias": alias,
                        "content": numbered_content,
                        "original_content": original_content,
                        "source_id": chunk_source_id,
                        "filename": filename,
                        "chunk_index": chunk_data.get("chunk_index"),
                        "page_number": chunk_data.get("page_number"),
                        "section": chunk_data.get("section"),
                        "sentence_count": sentence_count,
                        "chunk_metadata": chunk_meta,
                        "score": score,
                        "retrieval_origin": "vector",
                    }
                )
            return chunks
        except Exception:
            logger.warning("graphrag_vector_retrieval_failed", query=query, exc_info=True)
            return []

    # ------------------------------------------------------------------
    # Pipeline step 7: Merge and rank via RRF
    # ------------------------------------------------------------------

    def _merge_and_rank(
        self,
        provenance_chunks: list[dict[str, Any]],
        vector_chunks: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        """Merge provenance and vector chunks via Reciprocal Rank Fusion.

        Deduplicates by ``chunk_id``, tags retrieval origin, and assigns
        sequential chunk aliases.

        Args:
            provenance_chunks: Chunks from citation-based retrieval.
            vector_chunks: Chunks from hybrid search.
            limit: Maximum chunks to return.

        Returns:
            Merged and ranked chunk list.

        """
        try:
            provenance_list: list[tuple[str, float]] = [
                (c["chunk_id"], c.get("score", 1.0)) for c in provenance_chunks
            ]
            vector_list: list[tuple[str, float]] = [
                (c["chunk_id"], c.get("score", 0.0)) for c in vector_chunks
            ]

            rrf_ranked = reciprocal_rank_fusion(provenance_list, vector_list)

            # Build lookup by chunk_id (provenance wins on duplicates)
            chunk_lookup: dict[str, dict[str, Any]] = {}
            provenance_ids = {c["chunk_id"] for c in provenance_chunks}
            vector_ids = {c["chunk_id"] for c in vector_chunks}

            for chunk in provenance_chunks:
                chunk_lookup.setdefault(chunk["chunk_id"], chunk)
            for chunk in vector_chunks:
                chunk_lookup.setdefault(chunk["chunk_id"], chunk)

            merged: list[dict[str, Any]] = []
            for chunk_id, rrf_score in rrf_ranked:
                found = chunk_lookup.get(chunk_id)
                if not found:
                    continue

                # Determine retrieval origin
                in_prov = chunk_id in provenance_ids
                in_vec = chunk_id in vector_ids
                if in_prov and in_vec:
                    origin = "both"
                elif in_prov:
                    origin = "provenance"
                else:
                    origin = "vector"

                merged_chunk = dict(found)
                merged_chunk["retrieval_origin"] = origin
                merged_chunk["rrf_score"] = rrf_score
                merged.append(merged_chunk)

                if len(merged) >= limit:
                    break

            assign_chunk_aliases(merged)
            return merged
        except Exception:
            logger.warning("graphrag_merge_failed", exc_info=True)
            # Fallback: concatenate and deduplicate manually
            seen: set[str] = set()
            fallback: list[dict[str, Any]] = []
            for chunk in [*provenance_chunks, *vector_chunks]:
                if chunk["chunk_id"] not in seen:
                    seen.add(chunk["chunk_id"])
                    fallback.append(chunk)
                if len(fallback) >= limit:
                    break
            assign_chunk_aliases(fallback)
            return fallback

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def graphrag_search(
        self,
        query: str,
        limit: int = 10,
        seed_limit: int = 10,
        source_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Execute the full GraphRAG retrieval pipeline.

        The pipeline degrades gracefully:

        - **full_graphrag**: Embedding + seed entities + PPR + provenance + vector.
        - **vector_only**: Embedding succeeded but no graph seeds found.
        - **keyword_only**: Embedding failed; falls back to keyword search.

        Args:
            query: User search query.
            limit: Maximum number of chunks to return.
            seed_limit: Maximum seed entities for PPR.
            source_ids: Optional source scope filter.

        Returns:
            Dict with ``success``, ``graph_context``, ``chunks``,
            ``retrieval_stats``, and ``query`` keys.

        """
        stats: dict[str, Any] = {
            "mode": "keyword_only",
            "seed_entities_found": 0,
            "ppr_entities_explored": 0,
            "provenance_chunks": 0,
            "vector_chunks": 0,
            "total_chunks_returned": 0,
            "deduplicated": 0,
        }

        # Step 1: Embed query
        embedding = await self._embed_query(query)

        # Step 2: Match seed entities
        seeds: list[tuple[str, float]] = []
        if embedding:
            seeds = self._match_seed_entities(embedding, seed_limit)
            stats["seed_entities_found"] = len(seeds)

        # Steps 3-4: PPR + graph context (only if seeds found)
        graph_context: dict[str, Any] = {}
        ppr_top_ids: list[str] = []
        if seeds:
            ppr_scores = self._run_ppr(seeds, source_ids)
            if ppr_scores:
                graph_context = self._assemble_graph_context(ppr_scores, seeds)
                ppr_top_ids = [e["id"] for e in graph_context.get("related_entities", [])]
                # Also include seed entities in provenance lookup
                ppr_top_ids.extend(e["id"] for e in graph_context.get("seed_entities", []))
                stats["mode"] = "full_graphrag"
                stats["ppr_entities_explored"] = len(ppr_scores)

        # Step 5: Provenance chunks (if we have graph entities)
        provenance_chunks: list[dict[str, Any]] = []
        if ppr_top_ids and self.source_storage and self.indexing:
            provenance_chunks = await self._retrieve_provenance_chunks(ppr_top_ids, source_ids)
            stats["provenance_chunks"] = len(provenance_chunks)

        # Step 6: Vector/hybrid chunks
        vector_chunks = await self._retrieve_vector_chunks(query, limit, source_ids)
        stats["vector_chunks"] = len(vector_chunks)

        # Step 7: Merge
        if provenance_chunks or vector_chunks:
            chunks = self._merge_and_rank(provenance_chunks, vector_chunks, limit)
        else:
            chunks = []

        # Determine final mode
        if not seeds:
            stats["mode"] = "vector_only" if embedding else "keyword_only"
        elif not ppr_top_ids:
            stats["mode"] = "vector_only"

        stats["total_chunks_returned"] = len(chunks)
        stats["deduplicated"] = (len(provenance_chunks) + len(vector_chunks)) - len(chunks)

        logger.info(
            "graphrag_search_completed",
            query=query,
            mode=stats["mode"],
            seeds=stats["seed_entities_found"],
            chunks=stats["total_chunks_returned"],
        )

        return {
            "success": True,
            "graph_context": graph_context,
            "chunks": chunks,
            "retrieval_stats": stats,
            "query": query,
        }
