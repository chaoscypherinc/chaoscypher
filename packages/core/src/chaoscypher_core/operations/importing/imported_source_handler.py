# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Handlers: make freshly imported entities searchable.

A CCX import lands the graph (+ chunks for source-bearing packages) but no usable
vectors — the bundle carries node labels, not embeddings, and never chunk
embeddings — so imported entities can't be semantically searched until their
nodes are re-embedded and node + chunk vectors are pushed into the search index.

Two handlers cover the two import shapes, sharing one node-indexing helper:

* ``handle_index_imported_source`` — a SOURCE-bearing import (CCX with sources).
  Indexes the source's nodes, re-embeds its chunks, pushes chunk vectors, and
  finalizes the source's search status. It does NOT re-run extraction or regress
  the source's ``committed`` status — the two reasons ``OP_EMBED_CHUNKS`` can't be
  reused (``complete_indexing`` force-sets ``INDEXED`` and the embed handler
  auto-queues analysis).

* ``handle_index_imported_nodes`` — a KNOWLEDGE-ONLY import (lexicon, CLI). There
  is no source and there are no chunks, so the imported nodes are indexed
  directly off the id list the importer surfaces (``ImportStats.imported_node_ids``).

Order matters for the source path: nodes are indexed FIRST (so node search works
even if chunk re-embedding later fails), then chunks are embedded, then chunk
vectors are pushed. Vector pushes are best-effort (the durable data is the
persisted embeddings; the ANN index can always be rebuilt); only the chunk
embedding step drives degraded-status + retry.
"""

from __future__ import annotations

import base64
from typing import Any

import numpy as np
import structlog

from chaoscypher_core.app_config import get_settings
from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.services.quality.counters import (
    mark_search_indexing_degraded,
    mark_search_indexing_indexed,
)
from chaoscypher_core.services.sources.engine.deduplication.embedding_generator import (
    entity_to_embedding_text,
)


logger = structlog.get_logger(__name__)

# Property keys that are ingestion metadata (ids, timestamps), not semantic
# content — excluded from a node's embedding text so imported node vectors land
# in the same space as freshly extracted ones (which never saw these).
_NODE_EMBED_META_KEYS = frozenset(
    {"source_document_id", "source_document_name", "ingested_at", "source_type", "type"}
)


async def handle_index_imported_source(
    data: dict[str, Any],
    source_repository: Any,
    graph_repository: Any,
    indexing_service: Any,
    search_repository: Any | None = None,
    metadata: dict[str, Any] | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Re-embed a source's chunks and index its node + chunk vectors.

    Args:
        data: Task data — must contain ``source_id``.
        source_repository: SqliteAdapter (chunks, status writes).
        graph_repository: GraphRepository (the source's nodes).
        indexing_service: IndexingService (exposes ``embedding_service``).
        search_repository: SearchRepository (vector index). Best-effort if None.
        metadata: Task metadata; ``database_name`` is read from here.
        task_id: Queue task id (unused; part of the handler contract).

    Returns:
        Result dict with per-stage counts.

    Raises:
        ValidationError: If ``source_id`` is missing/not a string.
    """
    source_id = data.get("source_id")
    if not isinstance(source_id, str):
        msg = "source_id must be a string"
        raise ValidationError(msg, field="source_id")

    database_name = (metadata or {}).get("database_name", "default")
    adapter = source_repository
    logger.info("index_imported_source_processing", source_id=source_id, database=database_name)

    if search_repository is None:
        # Nothing can be vector-indexed without the search repo; degrade so the
        # UI shows "search degraded" rather than a false "indexed".
        logger.warning("index_imported_source_no_search_repo", source_id=source_id)
        mark_search_indexing_degraded(
            adapter=adapter, source_id=source_id, database_name=database_name
        )
        return {"success": False, "source_id": source_id, "reason": "no_search_repository"}

    settings = get_settings()
    provider = getattr(indexing_service, "embedding_service", None)

    # 1. Re-embed + index the source's nodes first, so node search survives a
    #    later chunk-embedding failure.
    nodes = _fetch_source_nodes(graph_repository, source_id)
    nodes_indexed, nodes_embedded = await _index_nodes(
        nodes,
        embedding_provider=provider,
        graph_repository=graph_repository,
        search_repository=search_repository,
        expected_dim=settings.search.vector_dimensions,
    )

    # 2. Re-embed unembedded chunks (the LLM-bound step). On failure, degrade +
    #    re-raise so the queue retries (embedded_at is the resume checkpoint).
    try:
        unembedded = adapter.list_unembedded_chunks(
            source_id=source_id, database_name=database_name
        )
        embedded_count = (
            await indexing_service.embed_chunks(
                chunks=unembedded, source_id=source_id, database_name=database_name
            )
            if unembedded
            else 0
        )
    except Exception:
        logger.exception("index_imported_source_embed_failed", source_id=source_id)
        mark_search_indexing_degraded(
            adapter=adapter, source_id=source_id, database_name=database_name
        )
        raise

    # 3. Push chunk vectors into vec_search_chunks now that embeddings exist.
    #    Best-effort: the embeddings are durably persisted (step 2 stamped
    #    embedded_at), so a vector-push failure must NOT fail the handler — else
    #    the retry re-runs step 2 (a no-op now) and re-hits the same push error,
    #    leaving the source permanently un-finalized. Log and let finalize run;
    #    the ANN index can be rebuilt by the search sweep.
    try:
        chunks_indexed = _index_chunk_vectors(adapter, search_repository, source_id)
    except Exception:
        logger.warning(
            "index_imported_source_chunk_vectors_failed", source_id=source_id, exc_info=True
        )
        chunks_indexed = 0

    # 4. Finalize: indexing complete + search-indexed, keeping the source
    #    ``committed`` (do NOT call complete_indexing, which force-sets INDEXED).
    adapter.update_source_columns(
        source_id=source_id,
        database_name=database_name,
        updates={
            "indexing_complete": True,
            "embedding_model": settings.embedding.model,
            "embedding_dimensions": settings.search.vector_dimensions,
        },
    )
    mark_search_indexing_indexed(adapter=adapter, source_id=source_id, database_name=database_name)

    logger.info(
        "index_imported_source_completed",
        source_id=source_id,
        nodes_indexed=nodes_indexed,
        nodes_embedded=nodes_embedded,
        chunks_embedded=embedded_count,
        chunks_indexed=chunks_indexed,
    )
    return {
        "success": True,
        "source_id": source_id,
        "nodes_indexed": nodes_indexed,
        "nodes_embedded": nodes_embedded,
        "chunks_embedded": embedded_count,
        "chunks_indexed": chunks_indexed,
    }


async def handle_index_imported_nodes(
    data: dict[str, Any],
    source_repository: Any,
    graph_repository: Any,
    indexing_service: Any,
    search_repository: Any | None = None,
    metadata: dict[str, Any] | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Re-embed + index a set of imported nodes by id (knowledge-only imports).

    Lexicon / CLI imports land a knowledge graph with NO source (and no chunks),
    so the source-scoped handler doesn't apply — the imported nodes are indexed
    directly off the id list the importer surfaced
    (``ImportStats.imported_node_ids``). Mirrors the node half of
    ``handle_index_imported_source``.

    Args:
        data: Task data — must contain ``node_ids`` (list[str]).
        source_repository: SqliteAdapter (unused; uniform handler signature).
        graph_repository: GraphRepository (the imported nodes).
        indexing_service: IndexingService (exposes ``embedding_service``).
        search_repository: SearchRepository (vector index). Best-effort if None.
        metadata: Task metadata; ``database_name`` is read from here.
        task_id: Queue task id (unused; part of the handler contract).

    Returns:
        Result dict with node counts.

    Raises:
        ValidationError: If ``node_ids`` is missing/not a list of strings.
    """
    node_ids = data.get("node_ids")
    if not isinstance(node_ids, list) or not all(isinstance(n, str) for n in node_ids):
        msg = "node_ids must be a list of strings"
        raise ValidationError(msg, field="node_ids")

    database_name = (metadata or {}).get("database_name", "default")
    logger.info("index_imported_nodes_processing", count=len(node_ids), database=database_name)

    if search_repository is None:
        logger.warning("index_imported_nodes_no_search_repo", count=len(node_ids))
        return {"success": False, "reason": "no_search_repository"}
    if not node_ids:
        return {"success": True, "nodes_indexed": 0, "nodes_embedded": 0}

    settings = get_settings()
    provider = getattr(indexing_service, "embedding_service", None)
    nodes = _fetch_nodes_by_ids(graph_repository, node_ids)
    nodes_indexed, nodes_embedded = await _index_nodes(
        nodes,
        embedding_provider=provider,
        graph_repository=graph_repository,
        search_repository=search_repository,
        expected_dim=settings.search.vector_dimensions,
    )

    logger.info(
        "index_imported_nodes_completed",
        nodes_indexed=nodes_indexed,
        nodes_embedded=nodes_embedded,
    )
    return {"success": True, "nodes_indexed": nodes_indexed, "nodes_embedded": nodes_embedded}


async def _index_nodes(
    nodes: list[Any],
    *,
    embedding_provider: Any,
    graph_repository: Any,
    search_repository: Any,
    expected_dim: int,
) -> tuple[int, int]:
    """Re-embed (best-effort) + vector-index imported nodes.

    Imported nodes arrive WITHOUT usable vectors (the bundle carries node labels,
    not embeddings), so generate real embeddings before indexing — else
    ``vec_search_nodes`` stays empty and semantic entity search / node-similarity
    GraphRAG seeding don't work. FTS keyword entity search works regardless, so
    node embedding is best-effort. Shared by both import handlers.

    Returns ``(nodes_indexed, nodes_embedded)``.
    """
    if not nodes:
        return 0, 0
    embedded = await _reembed_nodes(nodes, embedding_provider, graph_repository, expected_dim)
    # Best-effort vector push: node embeddings are durably persisted, so a
    # vec_search push failure must not fail the caller (FTS still works; the ANN
    # index can be rebuilt). Otherwise the source handler never reaches finalize.
    try:
        search_repository.index_nodes_batch(nodes)
        indexed = len(nodes)
    except Exception:
        logger.warning("index_imported_nodes_vector_push_failed", exc_info=True)
        indexed = 0
    return indexed, embedded


def _fetch_source_nodes(graph_repository: Any, source_id: str) -> list[Any]:
    """Page through all of a source's nodes (with embeddings) as Node models."""
    page_size = get_settings().batching.chunk_fetch_limit
    nodes: list[Any] = []
    skip = 0
    while True:
        batch = graph_repository.list_nodes(
            source_ids=[source_id],
            skip=skip,
            limit=page_size,
            include_embedding=True,
        )
        nodes.extend(batch)
        if len(batch) < page_size:
            break
        skip += page_size
    return nodes


def _fetch_nodes_by_ids(graph_repository: Any, node_ids: list[str]) -> list[Any]:
    """Fetch nodes (with embeddings) by id, paged to bound the SQL ``IN`` size."""
    page_size = get_settings().batching.chunk_fetch_limit
    nodes: list[Any] = []
    for start in range(0, len(node_ids), page_size):
        nodes.extend(graph_repository.get_nodes_batch(node_ids[start : start + page_size]))
    return nodes


def _node_embed_text(node: Any) -> str:
    """Build a node's embedding text (label + aliases + descriptive properties).

    Mirrors extraction's ``entity_to_embedding_text`` so an imported node's
    vector lands in the same space as a freshly-extracted one — minus the
    ingestion-metadata properties, which extraction never saw.
    """
    props = node.properties or {}
    clean_props = {k: v for k, v in props.items() if k not in _NODE_EMBED_META_KEYS}
    return entity_to_embedding_text(
        {
            "label": node.label,
            "aliases": props.get("aliases") or [],
            "description": props.get("description") or props.get("definition") or "",
            "properties": clean_props,
        }
    )


async def _reembed_nodes(
    nodes: list[Any],
    embedding_provider: Any,
    graph_repository: Any,
    expected_dim: int,
) -> int:
    """Generate real embeddings for imported nodes and persist them.

    Embed each node's text and write the vector onto ``graph_nodes.embedding`` (a
    JSON ``list[float]`` column — NOT base64) so the subsequent
    ``index_nodes_batch`` pushes it. Best-effort: FTS keyword entity search works
    regardless. Idempotent — nodes that already carry a right-dimension vector
    are skipped, so a retry is cheap. Writes are batched into one transaction.

    Returns the number of nodes (re)embedded.
    """
    if embedding_provider is None:
        return 0
    pending = [n for n in nodes if not (n.embedding and len(n.embedding) == expected_dim)]
    if not pending:
        return 0
    texts = [_node_embed_text(n) for n in pending]
    try:
        result = await embedding_provider.batch_embed(texts)
    except Exception:
        logger.warning("index_imported_node_embed_failed", exc_info=True)
        return 0

    if len(result.embeddings) != len(pending):
        # A well-behaved provider returns one vector per text; a short list means
        # the tail nodes silently stay vectorless (FTS-only). Surface it.
        logger.warning(
            "index_imported_node_embed_count_mismatch",
            requested=len(pending),
            returned=len(result.embeddings),
        )
    updates: dict[str, list[float]] = {}
    for node, vector in zip(pending, result.embeddings, strict=False):
        if not vector or len(vector) != expected_dim:
            continue  # a failed/empty vector or dim mismatch would only schedule_reindex
        node.embedding = vector  # in-memory, so the node index_nodes_batch sees is fresh
        updates[node.id] = vector
    if updates:
        graph_repository.update_node_embeddings_batch(updates)  # one txn, not N writes
    return len(updates)


def _index_chunk_vectors(adapter: Any, search_repository: Any, source_id: str) -> int:
    """Decode each embedded chunk and push it into vec_search_chunks.

    Streams ``(id, embedding)`` only — no chunk content (see
    ``iter_chunk_embeddings``) — base64-decodes the float32 vector, prefixes the
    id with ``chunk:``, and bulk-upserts. Best-effort (the embeddings are already
    durably persisted; the ANN index can be rebuilt).
    """
    page_size = get_settings().batching.chunk_fetch_limit
    embeddings_to_index: list[tuple[str, list[float]]] = []
    for chunk_id, embedding in adapter.iter_chunk_embeddings(source_id, page_size=page_size):
        vector = np.frombuffer(base64.b64decode(embedding), dtype=np.float32).tolist()
        embeddings_to_index.append((f"chunk:{chunk_id}", vector))

    if not embeddings_to_index:
        return 0
    search_repository.index_embeddings_batch(embeddings_to_index, item_type="chunk")
    return len(embeddings_to_index)
