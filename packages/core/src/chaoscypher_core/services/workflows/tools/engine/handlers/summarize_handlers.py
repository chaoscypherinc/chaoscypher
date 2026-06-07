# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Summarize tool handlers for compressed document retrieval.

Provides the summarize chat tool which retrieves document chunks,
auto-scales clustering to the context window, and compresses content
via a queued LLM call.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog

from chaoscypher_core.utils.tokens import estimate_tokens


if TYPE_CHECKING:
    from chaoscypher_core.settings import EngineSettings

logger = structlog.get_logger(__name__)


@dataclass
class SummarizeBudget:
    """Token budget for summarization computed from settings."""

    context_window: int
    usable_tokens: int
    max_chunks: int


class SummarizeToolHandlers:
    """Handlers for the summarize chat tool.

    Retrieves chunks by source or query, clusters to fit context window,
    and compresses via LLM queue.
    """

    def __init__(
        self,
        indexing_repository: Any,
        search_repository: Any,
        *,
        settings: EngineSettings,
        llm_chat_callback: Callable | None = None,
        embedding_callback: Callable | None = None,
        scope: dict[str, Any] | None = None,
    ) -> None:
        """Initialize summarize tool handlers.

        Args:
            indexing_repository: For fetching chunks by source.
            search_repository: For semantic/hybrid search.
            llm_chat_callback: Queue-based LLM chat call.
            embedding_callback: Queue-based embedding call.
            settings: Engine settings with LLM, chunking, and chat config.
            scope: Optional source scope for filtered retrieval.

        """
        self.indexing = indexing_repository
        self.search = search_repository
        self.llm_chat_callback = llm_chat_callback
        self.embedding_callback = embedding_callback
        self.settings = settings
        self.scope = scope

    def _compute_budget(self, prompt_text: str) -> SummarizeBudget:
        """Compute how many chunks fit in the context window.

        Args:
            prompt_text: The summarization prompt template text.

        Returns:
            SummarizeBudget with computed max_chunks.

        """
        context_window = self.settings.llm.ai_context_window
        prompt_tokens = estimate_tokens(prompt_text)
        # Reserve space for the summary output.  ai_max_tokens is the model's
        # maximum capability (e.g. 65K) which can exceed the context window.
        # Cap the reserve at 25% of the context window so we always leave
        # room for input chunks.
        max_output = self.settings.llm.ai_max_tokens
        output_reserve = min(max_output, context_window // 4)
        tools_overhead = self.settings.chat.tools_token_estimate
        usable = context_window - prompt_tokens - output_reserve - tools_overhead
        usable = max(usable, 0)

        chunk_tokens = self.settings.chunking.small_chunk_size // 4
        max_chunks = usable // chunk_tokens if chunk_tokens > 0 else 0

        logger.debug(
            "summarize_budget_computed",
            context_window=context_window,
            usable_tokens=usable,
            max_chunks=max_chunks,
        )

        return SummarizeBudget(
            context_window=context_window,
            usable_tokens=usable,
            max_chunks=max_chunks,
        )

    def _select_strategy(self, num_chunks: int, budget: SummarizeBudget) -> tuple[str, int]:
        """Select summarization strategy based on chunk count vs budget.

        Args:
            num_chunks: Total number of retrieved chunks.
            budget: Computed token budget.

        Returns:
            Tuple of (strategy_name, k) where k is number of chunks to use.

        """
        # Ensure at least 1 chunk so clustering doesn't fail on empty input
        effective_max = max(budget.max_chunks, 1)
        if num_chunks <= effective_max:
            return "stuff", num_chunks
        return "cluster", effective_max

    @staticmethod
    def _decode_embedding(raw: str | bytes | None) -> np.ndarray | None:
        """Decode a base64-encoded float32 embedding.

        Args:
            raw: Base64-encoded embedding string or bytes, or None.

        Returns:
            Numpy float32 array, or None if decoding fails.

        """
        if raw is None:
            return None
        try:
            if isinstance(raw, str):
                raw = raw.encode("utf-8")
            return np.frombuffer(base64.b64decode(raw), dtype=np.float32).copy()
        except Exception:
            return None

    @staticmethod
    def _kmeans(embeddings: np.ndarray, k: int, max_iter: int = 20) -> np.ndarray:
        """Simple K-Means clustering using numpy only.

        Args:
            embeddings: Array of shape (n, dim).
            k: Number of clusters.
            max_iter: Maximum iterations.

        Returns:
            Array of cluster labels of shape (n,).

        """
        n = embeddings.shape[0]
        # Initialize centroids with k evenly-spaced indices
        indices = np.linspace(0, n - 1, k, dtype=int)
        centroids = embeddings[indices].copy()

        labels = np.zeros(n, dtype=int)
        for _ in range(max_iter):
            # Assign each point to nearest centroid
            dists = np.linalg.norm(embeddings[:, None] - centroids[None, :], axis=2)
            new_labels = np.argmin(dists, axis=1)
            if np.array_equal(new_labels, labels):
                break
            labels = new_labels
            # Update centroids
            for j in range(k):
                mask = labels == j
                if mask.any():
                    centroids[j] = embeddings[mask].mean(axis=0)
        return labels

    @staticmethod
    def _select_representatives(chunks: list[dict[str, Any]], k: int) -> list[dict[str, Any]]:
        """Select k representative chunks via K-Means clustering.

        Args:
            chunks: List of chunk dicts with 'embedding' and 'chunk_index' keys.
            k: Number of representatives to select.

        Returns:
            List of k chunk dicts sorted by chunk_index.

        """
        # Filter chunks with valid embeddings
        valid = []
        embeddings_list = []
        for chunk in chunks:
            emb = SummarizeToolHandlers._decode_embedding(chunk.get("embedding"))
            if emb is not None:
                valid.append(chunk)
                embeddings_list.append(emb)

        if not valid:
            return []
        if len(valid) <= k:
            return sorted(valid, key=lambda c: c.get("chunk_index", 0))

        embeddings = np.vstack(embeddings_list)
        labels = SummarizeToolHandlers._kmeans(embeddings, k)

        # Pick chunk closest to centroid in each cluster
        representatives = []
        for j in range(k):
            cluster_indices = np.where(labels == j)[0]
            if len(cluster_indices) == 0:
                continue
            cluster_embs = embeddings[cluster_indices]
            centroid = cluster_embs.mean(axis=0)
            dists = np.linalg.norm(cluster_embs - centroid, axis=1)
            closest_idx = cluster_indices[np.argmin(dists)]
            representatives.append(valid[closest_idx])

        return sorted(representatives, key=lambda c: c.get("chunk_index", 0))

    @staticmethod
    def _select_representatives_per_source(
        chunks: list[dict[str, Any]], k_per_source: int
    ) -> list[dict[str, Any]]:
        """Cluster per source document for fair multi-doc representation.

        Args:
            chunks: List of chunk dicts from multiple sources.
            k_per_source: Number of representatives per source.

        Returns:
            List of representative chunks sorted by source then chunk_index.

        """
        by_source: dict[str, list[dict[str, Any]]] = {}
        for chunk in chunks:
            sid = chunk.get("source_id", "unknown")
            by_source.setdefault(sid, []).append(chunk)

        result = []
        for source_id in sorted(by_source.keys()):
            source_chunks = by_source[source_id]
            reps = SummarizeToolHandlers._select_representatives(source_chunks, k_per_source)
            result.extend(reps)
        return result

    # ── Retrieval Methods ──────────────────────────────────────────

    async def _retrieve_by_source(self, source_ids: list[str]) -> list[dict[str, Any]]:
        """Fetch all chunks for given source IDs.

        Args:
            source_ids: List of source document IDs.

        Returns:
            List of chunk dicts with embeddings.

        """
        # Reset the session's implicit transaction so we see chunks
        # committed by other processes (e.g. neuron worker).  The singleton
        # adapter keeps a long-lived session that may hold a stale read
        # snapshot from when the transaction began.
        session = getattr(self.indexing, "session", None)
        if session is not None:
            session.expire_all()
            # End any implicit transaction so the next query starts fresh
            if session.in_transaction():
                session.rollback()

        page_size = self.settings.batching.summarize_chunk_page_size
        all_chunks: list[dict[str, Any]] = []
        for source_id in source_ids:
            page = 1
            while True:
                chunks, total = self.indexing.get_chunks_by_source(
                    source_id=source_id,
                    page=page,
                    page_size=page_size,
                    include_embeddings=True,
                )
                logger.debug(
                    "summarize_retrieve_by_source_page",
                    source_id=source_id,
                    page=page,
                    chunks_returned=len(chunks),
                    total=total,
                    adapter_db=getattr(self.indexing, "database_name", "unknown"),
                )
                all_chunks.extend(chunks)
                if len(all_chunks) >= total or not chunks:
                    break
                page += 1
        return all_chunks

    async def _retrieve_by_query(self, query: str, limit: int = 100) -> list[dict[str, Any]]:
        """Fetch chunks via semantic vector search.

        Uses chunk-only vector search to avoid node/template results
        dominating the KNN results and pushing chunks below the
        similarity threshold.

        Args:
            query: Search query text.
            limit: Maximum chunks to retrieve.

        Returns:
            List of chunk dicts with embeddings.

        """
        if not self.embedding_callback:
            logger.warning("summarize_no_embedding_callback")
            return []

        try:
            result = await self.embedding_callback(query)
            if isinstance(result, dict):
                query_embedding = result.get("embedding", [])
            elif hasattr(result, "embedding"):
                query_embedding = result.embedding
            else:
                query_embedding = result

            if not query_embedding:
                logger.warning("summarize_empty_query_embedding")
                return []

            results = self.search.vector_search(query_embedding, k=limit, item_type="chunk")
        except Exception as e:
            logger.warning(
                "summarize_chunk_search_failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return []

        chunks = []
        for result_id, _score in results:
            chunk_id = result_id.removeprefix("chunk:")
            chunk = self.indexing.get_chunk_by_id(chunk_id)
            if chunk:
                chunks.append(chunk)
        return chunks

    def _build_prompt(
        self,
        query: str,
        selected_chunks: list[dict[str, Any]],
        multi_source: bool = False,
    ) -> list[dict[str, Any]]:
        """Build LLM messages for summarization.

        Args:
            query: User's summarization query.
            selected_chunks: Representative chunks to summarize.
            multi_source: Whether chunks come from multiple sources.

        Returns:
            List of message dicts for LLM chat call.

        """
        if multi_source:
            by_source: dict[str, list[dict[str, Any]]] = {}
            for chunk in selected_chunks:
                sid = chunk.get("source_id", "unknown")
                by_source.setdefault(sid, []).append(chunk)

            sections = []
            for sid, source_chunks in sorted(by_source.items()):
                content = "\n\n".join(c.get("content", "") for c in source_chunks)
                sections.append(f"--- Source: {sid} ---\n{content}")
            all_content = "\n\n".join(sections)
        else:
            all_content = "\n\n".join(c.get("content", "") for c in selected_chunks)

        return [
            {
                "role": "system",
                "content": (
                    "You are a precise summarization assistant. Summarize the "
                    "provided passages accurately. Do not add information not "
                    "present in the text. If comparing multiple sources, "
                    "structure your response by source."
                ),
            },
            {
                "role": "user",
                "content": (f"<query>{query}</query>\n\n<passages>\n{all_content}\n</passages>"),
            },
        ]

    # ── Main Handler ───────────────────────────────────────────────

    async def summarize(
        self,
        query: str,
        source_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Retrieve, cluster, and compress document content.

        Args:
            query: What to summarize (topic, question, or 'full document').
            source_ids: Optional source document IDs to limit retrieval.

        Returns:
            Dict with success, summary, strategy, and metadata.

        """
        try:
            # 1. Retrieve chunks
            if source_ids:
                chunks = await self._retrieve_by_source(source_ids)
            else:
                chunks = await self._retrieve_by_query(query)

            if not chunks:
                return {
                    "success": False,
                    "error": "No content found to summarize.",
                    "chunks_analyzed": 0,
                }

            # 2. Compute budget
            multi_source = len({c.get("source_id") for c in chunks}) > 1
            prompt_template = self._build_prompt(query, [], multi_source)
            prompt_text = " ".join(m.get("content", "") for m in prompt_template)
            budget = self._compute_budget(prompt_text)

            # 3. Select strategy and pick chunks
            strategy, k = self._select_strategy(len(chunks), budget)

            if strategy == "stuff":
                selected = sorted(chunks, key=lambda c: c.get("chunk_index", 0))
            elif multi_source and source_ids and len(source_ids) > 1:
                k_per_source = max(1, k // len(source_ids))
                selected = self._select_representatives_per_source(chunks, k_per_source)
            else:
                selected = self._select_representatives(chunks, k)

            # 4. Compress via LLM queue
            if not self.llm_chat_callback:
                return {
                    "success": False,
                    "error": "LLM chat callback not available for summarization.",
                    "chunks_analyzed": len(chunks),
                }

            messages = self._build_prompt(query, selected, multi_source)
            llm_result = await self.llm_chat_callback(messages)

            summary = llm_result.get("content", "")

            sources_used = sorted({c.get("source_id", "") for c in selected})

            logger.info(
                "summarize_completed",
                strategy=strategy,
                chunks_analyzed=len(chunks),
                chunks_selected=len(selected),
                sources_used=sources_used,
            )

            return {
                "success": True,
                "summary": summary,
                "strategy": strategy,
                "chunks_analyzed": len(chunks),
                "chunks_selected": len(selected),
                "sources_used": sources_used,
            }

        except Exception as e:
            logger.exception("summarize_failed", error=str(e))
            return {
                "success": False,
                "error": "Summarization failed",
                "chunks_analyzed": 0,
            }

    async def get_summary_context(
        self,
        query: str,
        source_ids: list[str] | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Retrieve and cluster chunks for summarization without LLM.

        Reuses the summarize pipeline's chunk retrieval and K-Means
        clustering but returns the curated chunks directly, allowing
        the MCP host LLM to perform synthesis.

        Args:
            query: Search query for chunk retrieval.
            source_ids: Optional source scope filter.
            limit: Max representative chunks to select.

        Returns:
            Dict with strategy, chunk counts, sources, and selected chunks.

        """
        try:
            # 1. Retrieve chunks (same as summarize)
            if source_ids:
                chunks = await self._retrieve_by_source(source_ids)
            else:
                chunks = await self._retrieve_by_query(query, limit=limit * 3)

            if not chunks:
                return {
                    "success": False,
                    "error": "No content found for query.",
                    "chunks_analyzed": 0,
                }

            # 2. Select strategy and pick representatives
            if len(chunks) <= limit:
                strategy = "stuff"
                selected = sorted(chunks, key=lambda c: c.get("chunk_index", 0))
            else:
                strategy = "cluster"
                selected = self._select_representatives(chunks, limit)

            # 3. Build response chunks (strip embeddings, add metadata)
            sources_used = sorted({c.get("source_id", "") for c in selected})
            response_chunks = []
            for i, chunk in enumerate(selected):
                response_chunks.append(
                    {
                        "chunk_id": chunk.get("id", ""),
                        "content": chunk.get("content", ""),
                        "filename": chunk.get("filename", ""),
                        "chunk_index": chunk.get("chunk_index", 0),
                        "page_number": chunk.get("page_number"),
                        "cluster_id": i if strategy == "cluster" else 0,
                        "score": chunk.get("score", 0.0),
                    }
                )

            logger.info(
                "get_summary_context_completed",
                strategy=strategy,
                chunks_analyzed=len(chunks),
                chunks_selected=len(selected),
            )

            return {
                "success": True,
                "strategy": strategy,
                "chunks_analyzed": len(chunks),
                "chunks_selected": len(selected),
                "sources_used": sources_used,
                "chunks": response_chunks,
            }

        except Exception as e:
            logger.exception("get_summary_context_failed", error=str(e))
            return {
                "success": False,
                "error": "Summary context retrieval failed",
                "chunks_analyzed": 0,
            }
