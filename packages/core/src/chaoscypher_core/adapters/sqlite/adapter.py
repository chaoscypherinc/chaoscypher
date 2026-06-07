# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""SQLite Storage Adapter for ChaosCypher Knowledge Engine.

Provides complete SQLite database storage implementation using SQLModel.
Implements the per-domain storage Protocols defined under ``chaoscypher_core.ports``
(``storage_workflows``, ``storage_chats``, ``storage_sources``, ...).

This adapter requires SQLModel:
    pip install sqlmodel>=0.0.14

Usage:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter

    adapter = SqliteAdapter(db_path="data/databases/default/app.db")
    adapter.connect()

    # Workflow operations
    workflow = adapter.create_workflow({
        "id": "workflow_001",
        "database_name": "default",
        "name": "Research Pipeline",
        ...
    })

    adapter.disconnect()

    # Or use as context manager
    with SqliteAdapter(db_path="data/app.db") as adapter:
        workflows = adapter.list_workflows("default")
"""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from pathlib import Path

import structlog

from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.mixins import (
    ChatsMixin,
    ExtractionSubmissionsMixin,
    LLMMetricsMixin,
    SearchRetryQueueMixin,
    SourceChunksMixin,
    SourceChunkTasksMixin,
    SourceCitationsMixin,
    SourceDeletionMixin,
    SourceExtractionJobsMixin,
    SourceIndexingMixin,
    SourceLifecycleMixin,
    SourceRecoveryEventsMixin,
    SourcesMixin,
    SourceTagsMixin,
    StageProgressMixin,
    SystemStateMixin,
    ToolsMixin,
    TriggersMixin,
    VisionPagesMixin,
    WorkflowExecutionsMixin,
    WorkflowsMixin,
)
from chaoscypher_core.adapters.sqlite.safe_session import SafeSession


logger = structlog.get_logger(__name__)


_current_session: ContextVar[SafeSession | None] = ContextVar("_current_session", default=None)
"""Per-task session for the queue worker dispatch path.

When ``SqliteAdapter.session_scope()`` is entered, a fresh ``SafeSession``
is installed here and ``SqliteAdapter.session`` (a property) reads it
preferentially. Outside any scope, ``SqliteAdapter.session`` falls back
to ``self._fallback_session`` so non-worker callers (tests, startup
paths, Cortex via ``adapter_factory``) keep their existing single-session
semantics.

The 2026-05-20 incident — parallel imports losing extraction jobs +
chunk tasks because the singleton ``SafeSession`` was shared across
concurrent queue handlers — is resolved by per-task scoping: each
``_execute_handler`` invocation enters a fresh scope, so two interleaved
handlers can never observe each other's pending state.
"""


class SqliteAdapter(
    WorkflowsMixin,
    WorkflowExecutionsMixin,
    ToolsMixin,
    SourceLifecycleMixin,
    SourceIndexingMixin,
    SourceExtractionJobsMixin,
    SourceChunkTasksMixin,
    SourceDeletionMixin,
    SourcesMixin,
    StageProgressMixin,
    SourceTagsMixin,
    SourceChunksMixin,
    SourceCitationsMixin,
    SourceRecoveryEventsMixin,
    VisionPagesMixin,
    ChatsMixin,
    TriggersMixin,
    LLMMetricsMixin,
    ExtractionSubmissionsMixin,
    SearchRetryQueueMixin,
    SystemStateMixin,
):
    """SQLite storage adapter implementing all storage protocols.

    Provides complete CRUD operations for all ChaosCypher entities using SQLite + SQLModel.

    Architecture:
        - Entity conversion via SqliteMixinBase (delegates to utils module)
        - Implements all storage protocols via mixins (ISP-compliant)
        - Uses SQLModel for ORM
        - Manages SQLite sessions and transactions

    Source file operations are split across 4 focused mixins:
        - SourceLifecycleMixin: File CRUD (upload, get, list, delete, update)
        - SourceIndexingMixin: Status lifecycle, embeddings, extraction gating
        - SourceExtractionJobsMixin: Extraction job CRUD and status
        - SourceChunkTasksMixin: Chunk task CRUD, analytics, recovery

    Source operations are split across 5 focused mixins:
        - SourcesMixin: Core source CRUD (get, create, update, list)
        - SourceTagsMixin: Tag CRUD and tag-to-source assignments
        - SourceChunksMixin: Document chunk CRUD, batch ops, hierarchical grouping
        - SourceCitationsMixin: Citations, stats, orphan detection, bulk clear
        - SourceDeletionMixin: Cross-mixin delete-source cascade orchestrator

    Example:
        adapter = SqliteAdapter(db_path="data/app.db")
        adapter.connect()
        workflow = adapter.create_workflow({...})
        adapter.disconnect()

    """

    def __init__(self, db_path: str, database_name: str | None = None):
        """Initialize SQLite storage adapter.

        Args:
            db_path: Path to SQLite database file or directory. When a
                directory path is given (no ``.db`` suffix), ``app.db``
                is appended automatically.
            database_name: Optional database name (derived from path if not provided)

        """
        resolved = Path(db_path)
        if resolved.suffix != ".db":
            resolved = resolved / "app.db"

        if not database_name:
            database_name = resolved.parent.name

        self.database_name = database_name
        self.db_path = resolved
        self._fallback_session: SafeSession | None = None
        self._engine: object | None = None
        self._connected = False

        logger.debug(
            "sqlite_adapter_initialized",
            db_path=str(self.db_path),
            database_name=self.database_name,
        )

    @property
    def session(self) -> SafeSession | None:
        """Active session: per-task scope if entered, else fallback.

        Inside ``session_scope()`` returns the ContextVar-bound session
        unique to the current async task. Outside any scope (tests,
        startup, Cortex per-request adapters) returns the connection-time
        ``_fallback_session``.
        """
        scoped = _current_session.get()
        if scoped is not None:
            return scoped
        return self._fallback_session

    @session.setter
    def session(self, value: SafeSession | None) -> None:
        """Backward-compatible setter; assigns to the fallback session.

        External callers that explicitly assign ``adapter.session`` (e.g.
        ``connect()`` / ``disconnect()`` here, and any test helper) update
        the fallback, never the ContextVar — scopes own their own session.
        """
        self._fallback_session = value

    def connect(self) -> None:
        """Initialize connection to SQLite database.

        NOTE: Tables should already be created via initialize_database() at startup.
        This method just creates the session for database operations.
        """
        if self._connected:
            return

        try:
            engine = get_engine(self.db_path)
            self._engine = engine
            self._fallback_session = SafeSession(engine)
            self._connected = True

            logger.debug("sqlite_adapter_connected", db_path=str(self.db_path))
        except Exception as e:
            logger.exception(
                "sqlite_adapter_connection_failed",
                db_path=str(self.db_path),
                error_type=type(e).__name__,
            )
            raise

    def disconnect(self) -> None:
        """Close connection to SQLite database."""
        if not self._connected:
            return

        try:
            if self._fallback_session is not None:
                self._fallback_session.close()
                self._fallback_session = None

            self._connected = False

            logger.debug("sqlite_adapter_disconnected", db_path=str(self.db_path))
        except Exception as e:
            logger.exception(
                "sqlite_adapter_disconnect_failed",
                db_path=str(self.db_path),
                error_type=type(e).__name__,
            )
            raise

    @asynccontextmanager
    async def session_scope(self) -> AsyncIterator[SafeSession]:
        """Run the wrapped block with a fresh per-task ``SafeSession``.

        Creates a brand-new ``SafeSession`` bound to the cached engine,
        installs it in the module-level ContextVar so ``self.session``
        (and ``GraphRepository.session``) resolve to it for the duration
        of the block, and closes the session on exit.

        This is what eliminates the 2026-05-20 silent-data-loss race:
        every queue handler dispatch enters a fresh scope, so two
        interleaved handlers can never share session state.

        Example::

            async with adapter.session_scope():
                adapter.create_extraction_job(...)
                # adapter.session here is a fresh SafeSession unique
                # to this task; sibling tasks see a different session.

        Raises:
            RuntimeError: if the adapter is not connected.
        """
        self._ensure_connected()
        if self._engine is None:  # pragma: no cover — connect() invariant
            msg = "SqliteAdapter._engine is unset after connect()"
            raise RuntimeError(msg)
        session = SafeSession(self._engine)
        token = _current_session.set(session)
        try:
            yield session
        finally:
            _current_session.reset(token)
            session.close()

    def __enter__(self) -> SqliteAdapter:
        """Enter context manager — connect and return self."""
        self.connect()
        return self

    def __exit__(self, *args: object) -> None:
        """Exit context manager — disconnect."""
        self.disconnect()

    def _ensure_connected(self) -> None:
        """Ensure adapter is connected before performing operations.

        Raises:
            RuntimeError: If adapter is not connected

        """
        if not self._connected:
            msg = (
                f"{self.__class__.__name__} is not connected. "
                "Call connect() before performing storage operations."
            )
            raise RuntimeError(msg)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Group multiple repository writes into a single database transaction.

        Inside this context, repository methods call ``_maybe_commit()``
        which flushes to the database buffer but does not commit. The
        outer transaction commits at context exit, or rolls back on any
        exception. Supports nesting — only the outermost context
        actually commits / rolls back.

        Example:
            with adapter.transaction():
                adapter.source_repository.start_commit(source_id)
                adapter.graph_repository.delete_graph_data_by_source(source_id)
                # ... more writes ...
            # All committed atomically here
        """
        self._ensure_connected()
        if self.session is None:
            yield
            return
        self.session._transaction_depth += 1  # noqa: SLF001 — coordinated transaction depth with SafeSession
        is_outermost = self.session._transaction_depth == 1  # noqa: SLF001 — coordinated transaction depth with SafeSession
        try:
            yield
            if is_outermost:
                self.session.commit()  # noqa: CC011 — outer transaction boundary owns final commit
        except Exception:
            if is_outermost:
                self.session.rollback()
            raise
        finally:
            self.session._transaction_depth -= 1  # noqa: SLF001 — coordinated transaction depth with SafeSession

    def _maybe_commit(self) -> None:
        """Commit the session, or flush if inside a ``transaction()`` context.

        Delegates to ``SafeSession.maybe_commit()`` which is the single
        source of truth for transaction depth across all writers.
        """
        self._ensure_connected()
        if self.session is not None:
            self.session.maybe_commit()
