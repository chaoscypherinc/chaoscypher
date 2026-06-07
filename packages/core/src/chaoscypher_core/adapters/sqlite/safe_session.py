# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Thread-safe SQLite session with automatic commit retry.

Provides SafeSession class that wraps SQLModel Session with exponential
backoff retry logic for handling SQLITE_BUSY errors in multi-process
environments (Cortex API, Neuron workers).
"""

import time
from typing import Any

import structlog
from sqlalchemy.exc import OperationalError
from sqlmodel import Session


logger = structlog.get_logger(__name__)


class SafeSession(Session):
    """SQLModel Session with automatic retry on database lock.

    Extends Session to override commit() with exponential backoff retry
    logic. When SQLITE_BUSY or "database is locked" errors occur, the
    commit is retried up to max_attempts times.

    This handles the case where multiple processes (Cortex API, Neuron
    workers) attempt concurrent writes to the same SQLite database.

    Example:
        # Instead of: session = Session(engine)
        session = SafeSession(engine)

        # All commits automatically retry on lock
        session.add(entity)
        session.commit()  # Retries automatically if locked

    """

    def __init__(
        self,
        *args: Any,
        max_attempts: int | None = None,
        base_delay: float | None = None,
        backoff_multiplier: float | None = None,
        **kwargs: Any,
    ):
        """Initialize SafeSession with configurable retry parameters.

        Args:
            *args: Positional arguments passed to Session
            max_attempts: Max commit retry attempts (default from DatabaseSettings)
            base_delay: Base delay in seconds for backoff (default from DatabaseSettings)
            backoff_multiplier: Per-attempt exponential growth factor. ``None``
                (default) resolves to ``BackoffSettings().exponential_multiplier``
                (the class default); callers holding engine settings inject it.
            **kwargs: Keyword arguments passed to Session

        """
        super().__init__(*args, **kwargs)

        from chaoscypher_core.settings import DatabaseSettings

        defaults = DatabaseSettings()
        self._max_attempts = (
            max_attempts if max_attempts is not None else defaults.commit_max_retries
        )
        self._base_delay = base_delay if base_delay is not None else defaults.commit_base_delay_secs
        # ``None`` is resolved lazily in ``commit()`` so a default-constructed
        # session always reads the current ``BackoffSettings`` class default.
        self._backoff_multiplier: float | None = backoff_multiplier
        self._transaction_depth: int = 0

    def maybe_commit(self) -> None:
        """Commit or flush depending on active transaction depth.

        When ``_transaction_depth > 0`` (set by an enclosing
        ``adapter.transaction()`` context), writes are flushed to the
        database buffer but not committed — the outer context commits
        on clean exit. When depth == 0 (no enclosing transaction),
        commit immediately, preserving standalone repository semantics.

        This is the single point of coordination between adapter mixins
        (``SqliteAdapter._maybe_commit``), domain repositories
        (``GraphRepository._maybe_commit``), and VSA feature
        repositories — all of them share the same session, so checking
        depth on the session is the correct boundary.
        """
        if self._transaction_depth > 0:
            self.flush()
        else:
            self.commit()

    def commit(self, **kw: Any) -> None:
        """Commit with exponential backoff retry on database lock.

        Overrides Session.commit() to add retry logic for SQLITE_BUSY errors.
        On successful retry, logs info. On failure after all retries, raises.

        When a commit fails with SQLITE_BUSY, SQLAlchemy automatically rolls
        back the transaction. To retry, we must snapshot the pending objects
        before the attempt, then re-add them after the rollback so the next
        commit has data to write.

        Args:
            **kw: Keyword arguments passed to parent commit()

        Raises:
            OperationalError: If all retry attempts fail or for non-lock errors.

        """
        for attempt in range(self._max_attempts):
            # Snapshot pending state before the commit attempt. On
            # SQLITE_BUSY, SQLAlchemy rolls back the transaction and
            # expunges all objects — without this snapshot the retry
            # would commit an empty transaction (silent data loss).
            pending_new = list(self.new)
            pending_dirty = list(self.dirty)
            pending_deleted = list(self.deleted)

            try:
                super().commit(**kw)
                if attempt > 0:
                    logger.info(
                        "commit_succeeded_after_retry",
                        attempt=attempt + 1,
                        total_attempts=self._max_attempts,
                    )
                return
            except OperationalError as e:
                error_msg = str(e).lower()
                is_lock_error = "database is locked" in error_msg or "sqlite_busy" in error_msg

                if is_lock_error and attempt < self._max_attempts - 1:
                    multiplier = self._backoff_multiplier
                    if multiplier is None:
                        from chaoscypher_core.settings import BackoffSettings

                        multiplier = BackoffSettings().exponential_multiplier
                    delay = self._base_delay * (multiplier**attempt)
                    logger.warning(
                        "commit_retry_on_lock",
                        attempt=attempt + 1,
                        max_attempts=self._max_attempts,
                        delay_seconds=delay,
                        pending_new=len(pending_new),
                        pending_dirty=len(pending_dirty),
                    )
                    self._retry_delay(delay)

                    # Rollback clears the session. Re-add pending objects
                    # so the next commit attempt actually writes data.
                    self.rollback()
                    for obj in pending_new:
                        self.add(obj)
                    for obj in pending_dirty:
                        self.add(obj)
                    for obj in pending_deleted:
                        self.delete(obj)
                else:
                    logger.exception(
                        "commit_failed",
                        attempt=attempt + 1,
                        is_lock_error=is_lock_error,
                        error=str(e),
                    )
                    raise

    @staticmethod
    def _retry_delay(delay: float) -> None:
        """Sleep during retry backoff.

        Always uses blocking sleep since SQLAlchemy commit() is synchronous.
        Callers in async contexts should wrap the entire commit call in
        asyncio.to_thread() to avoid blocking the event loop.
        """
        time.sleep(delay)
