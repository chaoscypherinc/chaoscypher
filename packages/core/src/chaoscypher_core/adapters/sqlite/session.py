# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Session management for ChaosCypher Knowledge Engine SQLite Adapter.

Provides session creation and context managers for database transactions.
Uses SafeSession for automatic commit retry on database locks.

NOTE: This is the engine version (framework-agnostic).
For FastAPI-specific session management (get_current_session), see backend/shared/database/session.py
"""

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import structlog

from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.safe_session import SafeSession


logger = structlog.get_logger(__name__)


def get_session(db_path: str) -> SafeSession:
    """Create a new database session with automatic commit retry.

    Returns SafeSession which automatically retries commits on SQLITE_BUSY.

    Note: Caller is responsible for closing the session.
    Consider using get_db_session() context manager instead.

    Args:
        db_path: Path to SQLite database file (string or Path)

    Returns:
        SafeSession instance (extends SQLModel Session)

    """
    engine = get_engine(Path(db_path))
    return SafeSession(engine)


@contextmanager
def get_db_session(db_path: str) -> Generator[SafeSession]:
    """Context manager for database sessions with automatic commit/rollback.

    Uses SafeSession for automatic commit retry on database locks.

    Usage:
        with get_db_session("data/databases/default/app.db") as session:
            workflow = session.get(Workflow, workflow_id)
            workflow.name = "Updated Name"
            # Automatically commits on successful exit

    On exception, automatically rolls back the transaction.

    Args:
        db_path: Path to SQLite database file

    Yields:
        SafeSession instance

    """
    session = get_session(db_path)
    try:
        yield session
        session.commit()  # Uses SafeSession.commit() with retry
        logger.debug("session_committed", db_path=db_path)
    except Exception as e:
        session.rollback()
        logger.exception(
            "session_rolled_back",
            db_path=db_path,
            error_type=type(e).__name__,
            error_message=str(e),
        )
        raise
    finally:
        session.close()
        logger.debug("session_closed", db_path=db_path)
