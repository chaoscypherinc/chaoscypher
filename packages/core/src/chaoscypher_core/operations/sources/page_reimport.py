# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Re-import a site page whose text changed, in place (spec 2026-10-22, decision 7).

The page keeps its source id. Its graph rows go through the existing release
path, ``force_re_extract`` (the source's memberships released and the nodes
and edges only it attested deleted, survivors rebuilt from their other
sources, the extraction reset, in one transaction), followed by the
post-release refresh. Then the new content is staged over the page's file,
the row goes back to ``pending``, its old chunk vectors leave the search
index, and indexing is queued exactly as an upload queues it, so the page
runs the whole pipeline again under its stored upload options (in a process
with no queue the page is left ``pending`` for that process's pipeline).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.exceptions import ExternalServiceError, NotFoundError
from chaoscypher_core.services.graph.management.release import (
    LazyEmbedder,
    refresh_after_release,
)
from chaoscypher_core.services.graph.management.source import (
    remove_chunk_vectors,
    source_chunk_ids,
)
from chaoscypher_core.services.sources.management.re_extraction import force_re_extract
from chaoscypher_core.services.sources.web import FetchedPage, page_names


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
    from chaoscypher_core.ports.embedding import EmbeddingProviderProtocol
    from chaoscypher_core.ports.search import SearchRepositoryProtocol
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


def _release_embedder(engine_settings: EngineSettings | None) -> LazyEmbedder:
    """The provider the post-release refresh re-embeds with, built only if a node went stale."""

    def build() -> EmbeddingProviderProtocol:
        from chaoscypher_core.adapters.embedding import create_embedding_provider

        if engine_settings is None:
            raise ExternalServiceError("embedding", "no engine settings to build a provider from")
        return create_embedding_provider(engine_settings)

    return LazyEmbedder(build=build, owned=True)


async def reimport_page_in_place(
    source_id: str,
    page: FetchedPage,
    *,
    storage: SqliteAdapter,
    operations_manager: Any,
    search_repository: SearchRepositoryProtocol | None,
    engine_settings: EngineSettings | None,
    settings: Any,
    database_name: str,
) -> None:
    """Replace a page's content with ``page`` and run it through the pipeline again.

    Args:
        source_id: The page's source row (it keeps its id).
        page: The page as fetched now (its hash differs from the row's).
        storage: The SQLite adapter (``force_re_extract`` needs its
            ``transaction()``).
        operations_manager: What queues indexing (``queue_import_indexing``),
            the upload path's own; ``None`` in a process with no queue (the
            CLI, the MCP server), which leaves the page ``pending`` for its
            own pipeline, as ``upload_file`` does there.
        search_repository: The search index (the refresh and the old chunk
            vectors); ``None`` skips both.
        engine_settings: The engine settings the release reads the graph's
            naming conventions under.
        settings: App settings (``paths`` for the title, ``priorities``).
        database_name: The database the page lives in.
    """
    chunk_ids = source_chunk_ids(storage, source_id, engine_settings) if search_repository else []
    released = force_re_extract(
        source_id=source_id,
        database_name=database_name,
        storage_adapter=storage,
        settings=engine_settings,
    )
    # Now, not at the next commit: the re-extraction reset the commit
    # counters, so that commit skips its own cleanup and never sees this release.
    await refresh_after_release(
        storage,
        database_name,
        released,
        embedder=_release_embedder(engine_settings),
        search=search_repository,
    )
    storage.restage_site_page(
        source_id,
        database_name,
        payload=page.payload,
        content_hash=page.content_hash,
        title=page_names(page.title, settings).title,
    )
    if search_repository is not None:
        remove_chunk_vectors(search_repository, source_id, chunk_ids)
    row = storage.get_source(source_id, database_name)
    if row is None:
        raise NotFoundError("Source", source_id)
    if operations_manager is not None:
        await operations_manager.queue_import_indexing(
            file_id=source_id,
            file_info={**row, "generate_embeddings": True},
            database_name=database_name,
            priority=settings.priorities.background,
        )
    logger.info(
        "site_page_reimported",
        source_id=source_id,
        nodes_deleted=len(released.deleted_node_ids),
        nodes_kept=len(released.touched_node_ids),
    )


__all__ = ["reimport_page_in_place"]
