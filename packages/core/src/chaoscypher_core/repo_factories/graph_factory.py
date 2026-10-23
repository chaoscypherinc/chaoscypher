# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Graph Repository Factory - Session-Based Pattern.

Provides GraphRepository instances that use SQLite for storage.
No caching needed - SQLite handles all persistence and concurrency.

Architecture:
- Fresh repository per request/operation
- All data stored in SQLite with WAL mode for concurrent access
- No in-memory cache = no multi-process synchronization issues

Usage (Backend API):
    from chaoscypher_core.repo_factories import get_graph_repository

    # In FastAPI factory functions:
    def get_node_service(
        session: Annotated[Session, Depends(get_current_session)],
        settings: Annotated[Settings, Depends(get_settings)]
    ) -> NodeService:
        graph_repo = get_graph_repository(session, settings.current_database)
        return NodeService(graph_repo)

Usage (Workers, on an adapter's own session):
    from chaoscypher_core.database import get_sqlite_adapter
    from chaoscypher_core.repo_factories import get_transaction_graph_repository

    adapter = get_sqlite_adapter(database_name=database_name)
    try:
        graph_repo = get_transaction_graph_repository(adapter, database_name)
        # Use repository within adapter scope
    finally:
        adapter.disconnect()
"""

from typing import TYPE_CHECKING

import structlog

from chaoscypher_core.adapters.sqlite.repos import GraphRepository


if TYPE_CHECKING:
    from sqlmodel import Session

    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter

logger = structlog.get_logger(__name__)


def get_graph_repository(session: Session, database_name: str = "default") -> GraphRepository:
    """Create a GraphRepository instance for the given session and database.

    The one place a ``GraphRepository`` is constructed (a guard in
    ``test_ci_config_guards.py`` holds every other module to it). On an
    adapter's own session, call :func:`get_transaction_graph_repository`,
    which checks the adapter is connected and delegates here; passing
    ``adapter.session`` directly is refused by the same guard.

    Each call returns a fresh repository with no in-memory caching — every
    operation goes directly to SQLite, so concurrent processes always
    observe the same state.

    Args:
        session: SQLModel session for database operations
        database_name: Name of the database (default: "default")

    Returns:
        GraphRepository instance bound to the session

    Example:
        # In FastAPI factory:
        def get_my_service(
            session: Annotated[Session, Depends(get_current_session)],
            settings: Annotated[Settings, Depends(get_settings)]
        ) -> MyService:
            graph_repo = get_graph_repository(session, settings.current_database)
            return MyService(graph_repo)

        # In workers: get_transaction_graph_repository(adapter, database_name)

    """
    return GraphRepository(session, database_name)


def get_transaction_graph_repository(adapter: SqliteAdapter, database_name: str) -> GraphRepository:
    """A GraphRepository on ``adapter``'s own session, so its writes join ``adapter.transaction()``.

    The one way to build a repository on an adapter's session (a guard in
    ``test_ci_config_guards.py`` holds every other module to it). Every source
    delete path (single delete, re-commit, force re-extract, reset) releases the
    source's graph rows and deletes or resets the source row in one transaction.
    A repository on any other session would commit the release on its own, or
    hold SQLite's writer lock against the adapter.

    Raises:
        RuntimeError: ``adapter`` is not connected (it has no session).
    """
    session = adapter.session
    if session is None:
        msg = "a graph repository needs a connected adapter: call adapter.connect() first"
        # nosemgrep: cc-045-bare-stdlib-raise-in-core - programmer-error guard; a connected adapter always has a session
        raise RuntimeError(msg)
    return get_graph_repository(session, database_name)
