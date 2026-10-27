# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Embed what has no vector yet: the rows written while vector search was off.

With ``search.enable_vector_search`` off a process stores chunks and nodes
keyword-only. Once vector search is back on, :func:`embed_missing` gives every
chunk with no vector and every node with no vector theirs, and pushes them into
the vector index, without touching a row that already has one
(``chaoscypher source rebuild-search --embed-missing``).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from chaoscypher_core.services.graph.management.release import reembed_nodes
from chaoscypher_core.services.search.engine.index import (
    IndexingService,
    index_source_chunk_vectors,
)


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter
    from chaoscypher_core.ports.embedding import EmbeddingProviderProtocol
    from chaoscypher_core.ports.graph import GraphRepositoryProtocol
    from chaoscypher_core.ports.search import SearchRepositoryProtocol
    from chaoscypher_core.settings import EngineSettings

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class EmbedMissingCounts:
    """How many rows :func:`embed_missing` embedded."""

    chunks: int
    nodes: int


async def embed_missing(
    *,
    storage: SqliteAdapter,
    graph: GraphRepositoryProtocol,
    search: SearchRepositoryProtocol,
    provider: EmbeddingProviderProtocol,
    settings: EngineSettings,
) -> EmbedMissingCounts:
    """Embed every chunk and node that has no vector, and push the vectors into the index.

    Chunks: waves of ``batching.embedding_wave_size`` chunks with no vector,
    each embedded per source (``IndexingService.embed_chunks`` with
    ``chunk_status=None``, so a committed memory stays committed), then each
    touched source's chunk vectors pushed once (:func:`index_source_chunk_vectors`).
    Nodes: waves of the same size re-embedded with :func:`reembed_nodes`. Both
    loops advance a keyset cursor, so a row the provider cannot embed is read
    once and the run ends. A provider error propagates; each wave's writes are
    its own, so a later run picks up what is left.

    Args:
        storage: The storage adapter holding the chunks.
        graph: The graph repository holding the nodes.
        search: The search repository whose vector index takes the vectors.
        provider: The embedding provider (vector search on).
        settings: Engine settings (database, batch sizes, dimensions).

    Returns:
        The number of chunks and nodes embedded.
    """
    wave = settings.batching.embedding_wave_size
    indexing = IndexingService(repository=storage, settings=settings, embedding_service=provider)

    chunks = 0
    touched: dict[str, None] = {}  # the sources whose chunks got vectors, in order
    after: str | None = None
    while batch := storage.list_chunks_without_embedding(
        database_name=settings.current_database, after_id=after, limit=wave
    ):
        after = batch[-1]["id"]
        by_source: dict[str, list[dict]] = defaultdict(list)
        for chunk in batch:
            by_source[chunk["source_id"]].append(chunk)
        for source_id, source_chunks in by_source.items():
            chunks += await indexing.embed_chunks(
                chunks=source_chunks,
                source_id=source_id,
                database_name=settings.current_database,
                chunk_status=None,
            )
            touched[source_id] = None
    # once per source, after every wave: a push sends the source's whole vector set
    for source_id in touched:
        index_source_chunk_vectors(
            storage, search, source_id, page_size=settings.batching.chunk_fetch_limit
        )

    nodes = 0
    after = None
    while node_batch := graph.list_nodes_without_embeddings(after_id=after, limit=wave):
        after = node_batch[-1].id
        nodes += await reembed_nodes(graph, [n.id for n in node_batch], provider, search=search)

    logger.info("embed_missing_complete", chunks=chunks, nodes=nodes)
    return EmbedMissingCounts(chunks=chunks, nodes=nodes)


__all__ = ["EmbedMissingCounts", "embed_missing"]
