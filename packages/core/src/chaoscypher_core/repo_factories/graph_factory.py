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

Usage (Workers):
    from chaoscypher_core.database import get_sqlite_adapter

    adapter = get_sqlite_adapter(database_name=database_name)
    try:
        graph_repo = get_graph_repository(adapter.session, database_name)
        # Use repository within adapter scope
    finally:
        adapter.disconnect()
"""

from typing import TYPE_CHECKING

import structlog

from chaoscypher_core.adapters.sqlite.repos import GraphRepository


if TYPE_CHECKING:
    from sqlmodel import Session

logger = structlog.get_logger(__name__)


def get_graph_repository(session: Session, database_name: str = "default") -> GraphRepository:
    """Create a GraphRepository instance for the given session and database.

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

        # In workers:
        adapter = get_sqlite_adapter(database_name=database_name)
        try:
            graph_repo = get_graph_repository(adapter.session, database_name)
            # ... use repository
        finally:
            adapter.disconnect()

    """
    return GraphRepository(session, database_name)
