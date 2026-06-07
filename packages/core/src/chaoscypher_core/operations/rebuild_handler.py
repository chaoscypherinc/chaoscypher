# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Search index rebuild handler for operations queue.

Provides ``handle_rebuild_search_indexes`` async function that
rebuilds vector and keyword search indexes, regenerating
embeddings from text when the model has changed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.services.events import event_bus


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository
    from chaoscypher_core.settings import EngineSettings

logger = structlog.get_logger(__name__)


async def handle_rebuild_search_indexes(
    data: dict[str, Any],
    search_repository: Any,
    graph_repository: GraphRepository,
    indexing_service: Any,
    storage_adapter: SqliteAdapter,
    engine_settings: EngineSettings,
    metadata: dict[str, Any] | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Execute search index rebuild.

    Args:
        data: Operation data with 'regenerate' flag (auto-set by API).
        search_repository: SearchRepository for vector/keyword indexing.
        graph_repository: GraphRepository for loading nodes.
        indexing_service: IndexingService for regenerating chunk embeddings.
        storage_adapter: SqliteAdapter for source/chunk data access.
        engine_settings: EngineSettings for search configuration.
        metadata: Optional task metadata.
        task_id: Optional task ID for logging.

    Returns:
        Dict with rebuild results.

    """
    regenerate = data.get("regenerate", False)

    logger.info(
        "rebuild_search_indexes_started",
        regenerate=regenerate,
        task_id=task_id,
    )

    try:
        from chaoscypher_core.services.search.engine.search import SearchService

        search_service = SearchService(
            search_repository=search_repository,
            graph_repository=graph_repository,
            indexing_repository=storage_adapter,
            source_repository=storage_adapter,
            sources_repository=storage_adapter,
            settings=engine_settings,
        )

        if regenerate:
            result = await search_service.rebuild_with_regeneration(
                indexing_service=indexing_service,
            )
        else:
            result = search_service.rebuild_indexes()

        logger.info(
            "rebuild_search_indexes_completed",
            task_id=task_id,
            **{k: v for k, v in result.items() if k != "message"},
        )

        event_bus.emit(
            "task_completed",
            action="Search index rebuild complete",
            source="worker",
            details={"regenerate": regenerate},
        )

        return result

    except Exception as e:
        logger.exception(
            "rebuild_search_indexes_failed",
            task_id=task_id,
            error_type=type(e).__name__,
            error_message=str(e),
        )

        event_bus.emit(
            "task_failed",
            action="Search index rebuild failed",
            source="worker",
            reason=str(e),
        )

        return {"success": False, "error": "Rebuild failed"}
