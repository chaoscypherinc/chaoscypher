# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Re-index a benchmark graph copy with one embedder: every node, then every chunk.

A cached extraction snapshot carries vectors from whatever embedder the
extraction run was configured with. Retrieval compares a query vector with
node and chunk vectors, so before an embedding or grounded-chat stage reads
the graph, all of them must come from the stage's embedder. The node
full-text index is rebuilt from the graph's nodes in the same pass, so
keyword search and keyword seeds read the graph as it is (a resolved graph's
merged-away nodes and dropped aliases included). The CLI's local stages and
the MCP bridge's reference packs both call :func:`reindex_graph`.
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any

import structlog

from chaoscypher_core.services.search.engine.index import chunk_embedding_text
from chaoscypher_core.services.sources.engine.deduplication.embedding_generator import (
    MAX_EMBEDDING_TEXT_LENGTH,
    node_embedding_text,
)


logger = structlog.get_logger(__name__)

_RECIPE_NODES = (
    SimpleNamespace(
        label="Sample Entity",
        properties={
            "aliases": ["Sample", "the sample"],
            "definition": "A sample entity with a description. " * 12,
            "title": "Sir",
            "occupation": "example",
            "age": 3,
            "source_document_id": "doc",
            "ingested_at": "2026-01-01T00:00:00+00:00",
            "type": "PERSON",
        },
    ),
    SimpleNamespace(label="Placeholder", properties={"definition": "Entity: Placeholder"}),
    SimpleNamespace(label="Packaged", properties={"description": "from a package", "content": "c"}),
    SimpleNamespace(
        label="Templated", properties={"content": "template content", "title": "Templated"}
    ),
)
_RECIPE_CHUNK = {"content": "[S1] A sample chunk.\n[S2] Its second sentence."}


def reindex_recipe() -> str:
    """What :func:`reindex_graph` embeds and keyword-indexes, as a hash of its output on fixed samples.

    Computed, never declared: a change to :func:`node_embedding_text`,
    :func:`chunk_embedding_text`, the text length cap or the node full-text
    row (:func:`~chaoscypher_core.adapters.sqlite.repos.search.node_fulltext_row`)
    changes it, so an index or pins built under the old text are rebuilt
    without anyone remembering to bump a number. A kept reference-pack index records it in READY and the
    pins carry it in their settings fingerprint. Until 2026-10-04 this was a
    hand-bumped integer; before 2026-10-03 it did not exist, and a pack indexed
    on 2026-10-01 kept serving label-only node vectors to MCP harness runs
    after 981f04d8a changed the text.
    """
    from chaoscypher_core.adapters.sqlite.repos.search import node_fulltext_row

    parts = [node_embedding_text(node) for node in _RECIPE_NODES]
    parts.extend(
        repr(sorted(node_fulltext_row(SimpleNamespace(id=f"n{i}", **vars(node))).items()))
        for i, node in enumerate(_RECIPE_NODES)
    )
    parts.append(chunk_embedding_text(_RECIPE_CHUNK))
    parts.append(str(MAX_EMBEDDING_TEXT_LENGTH))
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:12]


def _default_node_limit() -> int:
    """The benchmark settings' default node cap per reindex."""
    from chaoscypher_core.app_config import BenchmarkSettings

    return BenchmarkSettings().reindex_node_batch_limit


class ReindexIncompleteError(RuntimeError):
    """A re-embed did not write a vector for every node or chunk: the index must not be marked READY."""


def _require_written(kind: str, written: int, expected: int, held: int) -> None:
    """Raise unless ``written`` and the index's own count ``held`` both equal ``expected``.

    Raises:
        ReindexIncompleteError: Naming the three numbers.
    """
    if written != expected or held != expected:
        msg = (
            f"reindex wrote {written} {kind} vectors and the index holds {held}, "
            f"but there are {expected} {kind}s: a dimension mismatch or a failed write "
            "dropped some (see embedding_dimension_mismatch / batch_embedding_indexing_failed)"
        )
        raise ReindexIncompleteError(msg)


async def reindex_graph(
    target: Any, provider: Any, *, settings: Any, node_limit: int | None = None
) -> None:
    """Re-embed every graph node and every source's chunks with ``provider``; rebuild the node FTS.

    Nodes: one ``batch_embed`` over :func:`node_embedding_text` (the
    production entity text, the one every re-embed path uses), then the
    node vector table is cleared and refilled with
    ``index_embeddings_batch(item_type="node")``; :func:`reindex_recipe`
    names it. The node full-text index is rebuilt from the same nodes
    (``rebuild_node_fulltext``: labels and current aliases, rows of nodes no
    longer in the graph deleted); until 2026-10-06 it kept whatever the
    extraction wrote, so after the identity resolver it still held the
    merged-away nodes and their old aliases. Chunks: :func:`reembed_chunks`,
    then :func:`recompute_sentence_offsets` (the stored offsets follow the
    live splitter, audit wave 3). Once complete, the search repository's
    ``needs_full_reindex`` flag is cleared: every vector now comes from
    ``provider``.

    Complete or nothing (audit R8): a graph larger than ``node_limit``, or a
    vector the index did not take (a dimension mismatch is skipped with a log
    line), raises :class:`ReindexIncompleteError`, so a caller never writes
    READY over an index missing vectors. The tables are cleared before the
    write, so a vector left from the previous embedder cannot pass the count.
    Templates are not re-embedded: no retrieval reads them (vector search
    without an item type covers nodes and chunks only).

    Args:
        target: A connected engine or CLI context: ``graph_repository``,
            ``search_repository`` and ``storage_adapter``.
        provider: The embedding provider to embed with.
        settings: Engine settings (chunk page size, indexing configuration).
        node_limit: Most nodes to load; defaults to the benchmark settings'
            ``reindex_node_batch_limit``.

    Raises:
        ReindexIncompleteError: When any node or chunk has no vector afterwards.
    """
    limit = node_limit if node_limit is not None else _default_node_limit()
    nodes = target.graph_repository.list_nodes(limit=limit)
    if len(nodes) >= limit:
        msg = (
            f"the graph holds at least {limit} nodes, the reindex limit "
            "(benchmark reindex_node_batch_limit): raise it so every node is re-embedded"
        )
        raise ReindexIncompleteError(msg)

    # Collect (node_id, text) pairs then batch-embed in one call,
    # matching the production path in sources/service.py.
    node_ids = [node.id for node in nodes]
    texts = [node_embedding_text(node) for node in nodes]
    batch_result = await provider.batch_embed(texts)
    search = target.search_repository
    search.clear_vectors("node")
    written = search.index_embeddings_batch(
        list(zip(node_ids, batch_result.embeddings, strict=True)), item_type="node"
    )
    _require_written("node", written, len(nodes), search.count_vectors("node"))
    search.rebuild_node_fulltext(nodes)

    await reembed_chunks(target, provider, settings=settings)
    await recompute_sentence_offsets(target)
    search.clear_reindex_flag()


async def recompute_sentence_offsets(target: Any) -> int:
    """Bring every chunk's stored ``sentence_offsets`` to the live splitter's; return how many moved.

    The citation validator and the evidence resolver slice chunks by the
    stored offsets while hydration numbers sentences live, so a splitter
    change (C2, 2026-10-06: a break after a closing quote) left 88 of v3's
    179 chunks citing the wrong sentence until migration 0008 rewrote them.
    A reindex is the one pass every pack goes through on a recipe change, so
    it runs the same backfill (``recompute_chunk_sentence_offsets``) and
    commits it.

    Args:
        target: A connected engine or CLI context (``storage_adapter`` with
            ``session_scope``).

    Returns:
        The number of chunks rewritten (0 when every row already matched).
    """
    from chaoscypher_core.database.migrations.backfills import (
        recompute_chunk_sentence_offsets,
    )

    async with target.storage_adapter.session_scope() as session:
        rewritten = int(recompute_chunk_sentence_offsets(session))
        if rewritten:
            session.commit()
    if rewritten:
        logger.info("benchmark_reindex_sentence_offsets_recomputed", chunks=rewritten)
    return rewritten


async def reembed_chunks(target: Any, provider: Any, *, settings: Any) -> None:
    """Re-embed every source's chunks with ``provider`` and re-index their vectors.

    Follows the product's regeneration path (``SearchService.
    rebuild_with_regeneration``): ``IndexingService.create_index`` per source
    rewrites ``document_chunks.embedding`` in ``embedding_batch_size``
    batches, then the chunk vector table is cleared and the persisted vectors
    are pushed into it with ``index_embeddings_batch(item_type="chunk")``. A
    source with no chunks is skipped; any other failure propagates so a stage
    never silently scores retrieval against mixed-model chunk vectors, and a
    chunk left without a vector raises :class:`ReindexIncompleteError`.

    Args:
        target: A connected engine or CLI context (``storage_adapter``,
            ``search_repository``).
        provider: The embedding provider to embed with.
        settings: Engine settings (``batching.chunk_fetch_limit`` and the
            indexing service's configuration).

    Raises:
        ReindexIncompleteError: When the chunk vectors written, or held, are
            fewer than the chunks stored.
    """
    import base64

    import numpy as np

    from chaoscypher_core.exceptions import ValidationError
    from chaoscypher_core.services.search.engine.index import IndexingService

    adapter = target.storage_adapter
    search = target.search_repository
    indexing = IndexingService(repository=adapter, settings=settings, embedding_service=provider)
    page_size = settings.batching.chunk_fetch_limit
    sources, total_sources = adapter.list_sources(page=1, page_size=page_size)
    if total_sources > len(sources):
        msg = (
            f"{total_sources} sources but one page holds {len(sources)} "
            "(batching.chunk_fetch_limit): the rest would keep no chunk vectors"
        )
        raise ReindexIncompleteError(msg)
    vectors: list[tuple[str, list[float]]] = []
    expected = 0
    for source in sources:
        source_id = source["id"]
        expected += adapter.get_chunks_by_source(source_id, page=1, page_size=1)[1]
        try:
            await indexing.create_index(source_id)
        except ValidationError as exc:
            if exc.field != "source_id":
                raise
            logger.info("benchmark_reindex_source_without_chunks", source_id=source_id)
            continue
        vectors.extend(
            (
                f"chunk:{chunk_id}",
                np.frombuffer(base64.b64decode(embedding), dtype=np.float32).tolist(),
            )
            for chunk_id, embedding in adapter.iter_chunk_embeddings(source_id, page_size=page_size)
        )
    search.clear_vectors("chunk")
    written = search.index_embeddings_batch(vectors, item_type="chunk") if vectors else 0
    _require_written("chunk", written, expected, search.count_vectors("chunk"))


__all__ = [
    "ReindexIncompleteError",
    "recompute_sentence_offsets",
    "reembed_chunks",
    "reindex_graph",
    "reindex_recipe",
]
