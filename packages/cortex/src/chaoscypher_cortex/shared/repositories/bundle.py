# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""RepositoryBundle — Shared repository set for FastAPI factory functions.

Consolidates the graph + search + adapter triple that most feature
factories need, eliminating repeated import/instantiation boilerplate.

Usage in feature api.py:
    from chaoscypher_cortex.shared.repositories.bundle import (
        RepositoryBundle,
        get_repositories,
    )

    def get_node_service(
        repos: Annotated[RepositoryBundle, Depends(get_repositories)],
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> NodeService:
        return NodeService(repos=repos, settings=settings)
"""

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends
from sqlmodel import Session  # noqa: TC002 - FastAPI runtime DI dep

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_core.database.adapter_factory import get_sqlite_adapter
from chaoscypher_core.repo_factories.graph_factory import get_graph_repository
from chaoscypher_core.repo_factories.search_factory import get_search_repository
from chaoscypher_cortex.shared.database.session import get_current_session


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository, SearchRepository


class RepositoryBundle:
    """Pre-built set of shared repositories for a single request.

    Created once per request via the ``get_repositories`` FastAPI
    dependency.  Feature factory functions receive it instead of
    manually calling 3+ individual factory functions.

    Attributes:
        graph: GraphRepository for node/edge/template operations.
        search: SearchRepository for FTS5 + vector search.
        adapter: SqliteAdapter singleton (implements multiple storage protocols).
        session: Raw SQLModel session for features that need it directly.
        database_name: Active database name.

    """

    __slots__ = ("adapter", "database_name", "graph", "search", "session")

    def __init__(
        self,
        session: Session,
        settings: Settings,
    ) -> None:
        """Build the repository triple from session + settings.

        Args:
            session: Current request's database session.
            settings: Backend settings (provides current_database).

        """
        db = settings.current_database
        self.database_name: str = db
        self.session: Session = session
        self.graph: GraphRepository = get_graph_repository(session, db)
        self.search: SearchRepository = get_search_repository(database_name=db)
        self.adapter: SqliteAdapter = get_sqlite_adapter(database_name=db)


def get_repositories(
    session: Annotated[Session, Depends(get_current_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RepositoryBundle:
    """FastAPI dependency that provides a RepositoryBundle for the current request.

    Replaces the repeated pattern of calling ``get_graph_repository``,
    ``get_search_repository``, and ``get_sqlite_adapter`` individually
    in every feature factory function.
    """
    return RepositoryBundle(session, settings)


__all__ = ["RepositoryBundle", "get_repositories"]
