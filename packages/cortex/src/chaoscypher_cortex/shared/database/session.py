# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Backend Session Management - FastAPI Integration Layer.

Provides the single FastAPI-specific session dependency used by Cortex
feature endpoints. All other callers should prefer the
``SqliteAdapter``-based API (``get_sqlite_adapter`` +
``adapter.transaction()``).
"""

from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import structlog
from fastapi import Depends

from chaoscypher_core.adapters.sqlite.session import (
    get_session as engine_get_session,
)
from chaoscypher_core.app_config import Settings, get_settings


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.safe_session import SafeSession

logger = structlog.get_logger(__name__)


def get_current_session(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Generator[SafeSession]:
    """FastAPI dependency that provides a session for the current database.

    Automatically gets database_name from settings.current_database,
    so endpoints don't need to require it as a query parameter.

    Usage in API factory functions:
        def get_node_service(
            session: Annotated[Session, Depends(get_current_session)],
            settings: Annotated[Settings, Depends(get_settings)]
        ) -> NodeService:
            ...

    Yields:
        SQLModel Session instance for current database

    """
    # Get current database from settings, then construct the path using
    # settings (kept inline so this module has no legacy get_session /
    # get_db_session wrappers to export).
    database_name = settings.current_database
    db_path = (
        Path(settings.paths.data_dir)
        / settings.paths.databases_subdir
        / database_name
        / settings.paths.app_db_filename
    )

    session = engine_get_session(str(db_path))
    try:
        yield session
    finally:
        session.close()
