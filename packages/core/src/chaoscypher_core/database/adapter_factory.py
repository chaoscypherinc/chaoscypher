# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""SQLite Adapter Factory — Per-Request Pattern with Lifecycle Management.

Creates fresh SqliteAdapter instances with per-request sessions while
reusing the cached SQLAlchemy engine (connection pool) underneath.
Adapters are tracked via a context variable and cleaned up by middleware
after each request, preventing connection pool exhaustion.

Architecture:
- Engine (connection pool) is cached per database path by get_engine() in core
- Adapter and session are created fresh per call (lightweight)
- Prevents session sharing across concurrent requests
- _request_adapters ContextVar tracks adapters for cleanup

Usage (Backend):
    from chaoscypher_core.database.adapter_factory import get_sqlite_adapter

    adapter = get_sqlite_adapter(database_name=settings.current_database)
    # Use adapter for storage operations — session is per-request
    # Cleanup happens automatically via AdapterCleanupMiddleware
"""

from contextvars import ContextVar
from typing import TYPE_CHECKING

import structlog

from chaoscypher_core.adapters.sqlite import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_db_path


if TYPE_CHECKING:
    from chaoscypher_core.app_config import Settings


logger = structlog.get_logger(__name__)

# Tracks adapters created during the current request for cleanup
_request_adapters: ContextVar[list[SqliteAdapter]] = ContextVar(
    "request_adapters",
)


def get_sqlite_adapter(
    database_name: str | None = None,
    *,
    settings: Settings | None = None,
) -> SqliteAdapter:
    """Create a fresh SqliteAdapter instance for a database.

    The underlying SQLAlchemy engine is cached per database path by
    chaoscypher_core.adapters.sqlite.engine.get_engine(), so creating
    a new adapter is lightweight (only creates a new session).

    Adapters are registered in a request-scoped ContextVar for automatic
    cleanup by AdapterCleanupMiddleware.

    The ``database_name`` MUST be honored rather than collapsed onto the
    active ``current_database``: callers operate on databases other than the
    current one — most importantly ``create_database`` initializes and seeds a
    brand-new database *before* it becomes current. Falling back to the
    current database there wrote the new DB's seed rows into the active DB's
    app.db and collided on the global ``graph_templates.id`` primary key.

    Args:
        database_name: Name of the database. Defaults to the active
            ``current_database`` when not given.
        settings: Application settings to resolve the database path and the
            ``current_database`` default. Defaults to the app-config
            singleton when omitted (the only remaining entry-point default;
            request/worker contexts should inject the settings they already
            hold).

    Returns:
        Connected SqliteAdapter instance with a fresh session

    """
    if settings is None:
        from chaoscypher_core.app_config import get_settings

        settings = get_settings()
    name = database_name if database_name is not None else settings.current_database
    db_path = get_db_path(
        name,
        data_dir=settings.paths.data_dir,
        databases_subdir=settings.paths.databases_subdir,
        app_db_filename=settings.paths.app_db_filename,
    )

    adapter = SqliteAdapter(db_path=str(db_path))
    adapter.connect()

    # Register for cleanup (if in request context)
    try:
        adapters = _request_adapters.get()
        adapters.append(adapter)
    except LookupError:
        pass  # Not in request context (e.g. startup, workers)

    return adapter
