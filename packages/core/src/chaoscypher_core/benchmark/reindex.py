# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Re-embed a benchmark graph copy with one embedder: every node, then every chunk.

A cached extraction snapshot carries vectors from whatever embedder the
extraction run was configured with. Retrieval compares a query vector with
node and chunk vectors, so before an embedding or grounded-chat stage reads
the graph, all of them must come from the stage's embedder. The CLI's local
stages and the MCP bridge's reference packs both call :func:`reindex_graph`.
"""

from __future__ import annotations

from typing import Any

import structlog


logger = structlog.get_logger(__name__)


def _default_node_limit() -> int:
    """The benchmark settings' default node cap per reindex."""
    from chaoscypher_core.app_config import BenchmarkSettings

    return BenchmarkSettings().reindex_node_batch_limit


async def reindex_graph(
    target: Any, provider: Any, *, settings: Any, node_limit: int | None = None
) -> None:
    """Re-embed every graph node and every source's chunks with ``provider``.

    Nodes: one ``batch_embed`` over ``"<label>. <description>"`` (the
    production indexing text), then ``index_node_embedding`` per node.
    Chunks: :func:`reembed_chunks`.

    Args:
        target: A connected engine or CLI context: ``graph_repository``,
            ``search_repository`` and ``storage_adapter``.
        provider: The embedding provider to embed with.
        settings: Engine settings (chunk page size, indexing configuration).
        node_limit: Most nodes to load; defaults to the benchmark settings'
            ``reindex_node_batch_limit``.
    """
    limit = node_limit if node_limit is not None else _default_node_limit()
    nodes = target.graph_repository.list_nodes(limit=limit)

    # Collect (node_id, text) pairs then batch-embed in one call,
    # matching the production path in sources/service.py.
    node_ids = [node.id for node in nodes]
    texts = [(node.label or "") + ". " + (getattr(node, "description", "") or "") for node in nodes]
    batch_result = await provider.batch_embed(texts)
    for node_id, embedding in zip(node_ids, batch_result.embeddings, strict=True):
        target.search_repository.index_node_embedding(node_id, embedding)

    await reembed_chunks(target, provider, settings=settings)


async def reembed_chunks(target: Any, provider: Any, *, settings: Any) -> None:
    """Re-embed every source's chunks with ``provider`` and re-index their vectors.

    Follows the product's regeneration path (``SearchService.
    rebuild_with_regeneration``): ``IndexingService.create_index`` per source
    rewrites ``document_chunks.embedding`` in ``embedding_batch_size``
    batches, then the persisted vectors are pushed into ``vec_search_chunks``
    with ``index_embeddings_batch(item_type="chunk")`` (upsert by id, so the
    previous embedder's vectors are replaced). A source with no chunks is
    skipped; any other failure propagates so a stage never silently scores
    retrieval against mixed-model chunk vectors.

    Args:
        target: A connected engine or CLI context (``storage_adapter``,
            ``search_repository``).
        provider: The embedding provider to embed with.
        settings: Engine settings (``batching.chunk_fetch_limit`` and the
            indexing service's configuration).
    """
    import base64

    import numpy as np

    from chaoscypher_core.exceptions import ValidationError
    from chaoscypher_core.services.search.engine.index import IndexingService

    adapter = target.storage_adapter
    indexing = IndexingService(repository=adapter, settings=settings, embedding_service=provider)
    page_size = settings.batching.chunk_fetch_limit
    sources, _ = adapter.list_sources(page=1, page_size=page_size)
    for source in sources:
        source_id = source["id"]
        try:
            await indexing.create_index(source_id)
        except ValidationError as exc:
            if exc.field != "source_id":
                raise
            logger.info("benchmark_reindex_source_without_chunks", source_id=source_id)
            continue
        vectors = [
            (
                f"chunk:{chunk_id}",
                np.frombuffer(base64.b64decode(embedding), dtype=np.float32).tolist(),
            )
            for chunk_id, embedding in adapter.iter_chunk_embeddings(source_id, page_size=page_size)
        ]
        if vectors:
            target.search_repository.index_embeddings_batch(vectors, item_type="chunk")


__all__ = ["reembed_chunks", "reindex_graph"]
