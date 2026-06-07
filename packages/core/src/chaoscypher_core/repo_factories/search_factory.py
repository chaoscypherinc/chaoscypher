# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Global Search Repository Factory - Singleton Pattern.

Provides a single global SearchRepository instance per database.
Uses the main app.db engine for all search operations (FTS5 + sqlite-vec).
"""

from functools import lru_cache
from pathlib import Path

import structlog

from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.repos import SearchRepository
from chaoscypher_core.app_config import get_settings


logger = structlog.get_logger(__name__)


@lru_cache(maxsize=10)
def _create_search_repository(
    database_name: str, vector_dim: int, embedding_model: str
) -> SearchRepository:
    """Create a SearchRepository instance (internal, cached by args).

    Args:
        database_name: Name of the database
        vector_dim: Vector dimensions for the search index
        embedding_model: Current embedding model name

    Returns:
        New SearchRepository instance

    """
    settings = get_settings()

    db_path = (
        Path(settings.paths.data_dir)
        / settings.paths.databases_subdir
        / database_name
        / settings.paths.app_db_filename
    )
    engine = get_engine(db_path)

    search_repo = SearchRepository(
        engine=engine,
        vector_dim=vector_dim,
        embedding_model=embedding_model,
    )

    logger.info(
        "search_repository_singleton_created",
        database_name=database_name,
        vector_dim=vector_dim,
    )

    return search_repo


def get_search_repository(database_name: str = "default") -> SearchRepository:
    """Get SearchRepository instance for a database.

    Automatically recreates the repository if settings have changed
    (e.g., vector dimensions or embedding model updated).

    Args:
        database_name: Name of the database (default: "default")

    Returns:
        SearchRepository instance with current settings

    """
    settings = get_settings()
    # The cache key includes dims + model, so changes auto-invalidate
    return _create_search_repository(
        database_name=database_name,
        vector_dim=settings.search.vector_dimensions,
        embedding_model=settings.embedding.model,
    )


def invalidate_search_repository() -> None:
    """Clear the cached SearchRepository singletons.

    Call after embedding model/dimensions change or index rebuild
    so the next request creates a fresh instance with current settings.
    """
    _create_search_repository.cache_clear()
    logger.info("search_repository_cache_invalidated")
