# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""ServiceFactory — Simplified service access for workers and scripts.

Eliminates boilerplate when using Cortex services outside of FastAPI
dependency injection (e.g., in Neuron workers, management scripts,
data migrations).

Usage:
    from chaoscypher_cortex.shared.service_factory import ServiceFactory

    with ServiceFactory("default") as factory:
        search = factory.search_service()
        sources = factory.source_service()
        # All share the same adapter session; cleaned up on exit.

Compare to the old pattern (5+ lines per service):
    from chaoscypher_core.database.adapter_factory import get_sqlite_adapter
    from chaoscypher_core.repo_factories import get_graph_repository

    adapter = get_sqlite_adapter(database_name=database_name)
    try:
        graph_repo = get_graph_repository(adapter.session, database_name)
        search_repo = get_search_repository(database_name=database_name)
        service = NodeService(graph_repo, ...)
    finally:
        adapter.disconnect()
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_core.database.adapter_factory import get_sqlite_adapter
from chaoscypher_core.repo_factories import (
    get_graph_repository,
    get_search_repository,
)


if TYPE_CHECKING:
    from types import TracebackType

    from sqlmodel import Session

    from chaoscypher_core.adapters.sqlite import SqliteAdapter
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository, SearchRepository
    from chaoscypher_cortex.features.search.service import SearchService as SearchFeatureService
    from chaoscypher_cortex.features.sources.service import SourceService


logger = structlog.get_logger(__name__)


class ServiceFactory:
    """Context manager that wires adapter + repositories once, then creates services on demand.

    Designed for use in workers and scripts — NOT for FastAPI endpoints
    (use ``Depends(get_current_session)`` and per-feature factory
    functions there).

    All services created from the same factory share the same underlying
    SqliteAdapter (and its session) and repository instances. The adapter
    is disconnected automatically on context exit.
    """

    def __init__(self, database_name: str = "default") -> None:
        """Initialise the factory.

        Args:
            database_name: Database to connect to.

        """
        self._database_name = database_name
        self._settings: Settings | None = None
        self._graph_repo: GraphRepository | None = None
        self._search_repo: SearchRepository | None = None
        self._adapter: SqliteAdapter | None = None

    # -- Context manager -------------------------------------------------------

    def __enter__(self) -> ServiceFactory:
        """Open adapter and lazily prepare shared repositories."""
        self._settings = get_settings()
        self._adapter = get_sqlite_adapter(database_name=self._database_name)
        logger.debug("service_factory_opened", database=self._database_name)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Disconnect the adapter."""
        if self._adapter is not None:
            self._adapter.disconnect()
            self._adapter = None
        logger.debug("service_factory_closed", database=self._database_name)

    # -- Shared dependencies (lazy) -------------------------------------------

    @property
    def session(self) -> Session:
        """Current database session (from the adapter)."""
        if self._adapter is None or self._adapter.session is None:
            raise RuntimeError("ServiceFactory must be used as a context manager.")
        return self._adapter.session

    @property
    def settings(self) -> Settings:
        """Backend settings singleton."""
        if self._settings is None:
            self._settings = get_settings()
        return self._settings

    @property
    def graph_repository(self) -> GraphRepository:
        """Graph repository (lazy, cached per factory instance)."""
        if self._graph_repo is None:
            self._graph_repo = get_graph_repository(self.session, self._database_name)
        return self._graph_repo

    @property
    def search_repository(self) -> SearchRepository:
        """Search repository (lazy, cached per factory instance)."""
        if self._search_repo is None:
            self._search_repo = get_search_repository(database_name=self._database_name)
        return self._search_repo

    @property
    def adapter(self) -> SqliteAdapter:
        """SQLite adapter (single instance per factory context)."""
        if self._adapter is None:
            raise RuntimeError("ServiceFactory must be used as a context manager.")
        return self._adapter

    # -- Service factories ----------------------------------------------------

    def search_service(self) -> SearchFeatureService:
        """Create a SearchService with wired dependencies."""
        from chaoscypher_cortex.features.search.service import (
            SearchService as _SearchService,
        )

        return _SearchService(
            search_repository=self.search_repository,
            graph_repository=self.graph_repository,
            indexing_repository=self.adapter,
            source_repository=self.adapter,
            sources_repository=self.adapter,
            settings=self.settings,
        )

    def source_service(self) -> SourceService:
        """Create a SourceService with wired dependencies."""
        from chaoscypher_core.app_config.engine_factory import (
            build_engine_settings,
        )
        from chaoscypher_core.services.graph.management import (
            SourceService as EngineSourceService,
        )
        from chaoscypher_cortex.features.sources.service import (
            SourceService as _SourceService,
        )

        engine_service = EngineSourceService(
            repository=self.adapter,
            database_name=self._database_name,
            settings=build_engine_settings(self.settings),
        )

        return _SourceService(
            engine_service,
            database_name=self._database_name,
            settings=self.settings,
            storage_adapter=self.adapter,
            graph_repository=self.graph_repository,
            search_repository=self.search_repository,
        )


__all__ = ["ServiceFactory"]
