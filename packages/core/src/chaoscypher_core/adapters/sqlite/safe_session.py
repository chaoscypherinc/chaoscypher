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
from sqlalchemy.engine import RootTransaction
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm.session import SessionTransactionState
from sqlmodel import Session


logger = structlog.get_logger(__name__)


class SafeSession(Session):
    """SQLModel Session with automatic retry on database lock.

    Extends Session to override commit() with exponential backoff retry
    logic. When SQLITE_BUSY or "database is locked" errors occur on the
    COMMIT itself, the commit is retried up to max_attempts times against
    the transaction SQLite left open — the flushed writes stay staged and
    are never rolled back. A retry budget that runs out raises; the commit
    never reports success after dropping writes.

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

        The retry never rolls back. By the time an enclosing
        ``adapter.transaction()`` reaches its commit, every write has already
        been flushed (``maybe_commit()`` flushes at depth > 0), so a rollback
        would discard the work and the retry would commit an empty
        transaction while reporting success. Instead the still-open SQLite
        transaction is re-armed and the COMMIT re-issued, leaving the flushed
        writes in place. See ``_rearm_busy_commit()``.

        Args:
            **kw: Keyword arguments passed to parent commit()

        Raises:
            OperationalError: If all retry attempts fail, if the lock error
                came from a phase that cannot be retried, or for non-lock
                errors. Never returns normally once writes have been lost.

        """
        for attempt in range(self._max_attempts):
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

                retryable = is_lock_error and attempt < self._max_attempts - 1
                if retryable:
                    retryable = self._rearm_busy_commit()

                if not retryable:
                    logger.exception(
                        "commit_failed",
                        attempt=attempt + 1,
                        is_lock_error=is_lock_error,
                        error=str(e),
                    )
                    raise

                delay = self._base_delay * (self._resolve_backoff_multiplier() ** attempt)
                logger.warning(
                    "commit_retry_on_lock",
                    attempt=attempt + 1,
                    max_attempts=self._max_attempts,
                    delay_seconds=delay,
                )
                self._retry_delay(delay)

    def _resolve_backoff_multiplier(self) -> float:
        """Return the per-attempt exponential growth factor for the retry delay.

        An explicit ``backoff_multiplier`` wins. ``None`` (the constructor
        default) resolves lazily to ``BackoffSettings().exponential_multiplier``
        so a default-constructed session always reads the current class
        default rather than a value frozen at construction time — and never
        the app settings singleton.
        """
        if self._backoff_multiplier is not None:
            return self._backoff_multiplier

        from chaoscypher_core.settings import BackoffSettings

        return BackoffSettings().exponential_multiplier

    def _rearm_busy_commit(self) -> bool:
        """Re-arm a COMMIT that SQLite rejected with SQLITE_BUSY so it can be retried.

        SQLITE_BUSY on COMMIT *usually* leaves the transaction open and
        retryable, but SQLite documents it among the errors that may instead
        roll the whole transaction back ("Response To Errors Within A
        Transaction"). That the transaction survived is therefore a checked
        precondition here, never an assumption: re-arming a transaction
        SQLite already discarded would retry a ``connection.commit()`` that
        is a silent no-op on an autocommit connection, reporting success on
        writes that are gone — the exact failure this method exists to
        prevent.

        When the transaction did survive, SQLAlchemy still stands in the way:
        ``RootTransaction._do_commit`` deactivates its transaction in a
        ``finally`` whatever the outcome, so a second ``Session.commit()``
        raises ``PendingRollbackError`` instead of re-issuing the COMMIT.
        Setting ``is_active`` back to True re-arms exactly the transaction
        SQLite is still holding open, so the retry commits the already-flushed
        writes rather than nothing.

        Three conditions must all hold, and each rules out a different way the
        writes could already be lost:

        - The ``SessionTransaction`` is ``PREPARED`` — the COMMIT-phase
          failure, where the flush has run and its writes are staged in the
          open SQLite transaction. A failure during the flush leaves
          ``DEACTIVE`` with the pending objects already expunged to
          transient, so nothing is left to re-commit.
        - SQLAlchemy has not cleared ``connection._transaction``. It clears
          that only for a COMMIT that completed (``engine/base.py``: "only
          remove as the connection's current transaction if commit
          succeeded"), so an already-committed transaction is never re-armed
          — which would trip ``_do_commit``'s own assert on a multi-bind
          session.
        - The DBAPI connection still reports ``in_transaction``
          (``sqlite3_get_autocommit``), proving SQLite did not take the
          rollback branch.

        Only root transactions are re-armed. A savepoint is released by a
        different statement, and re-arming one would not re-issue the COMMIT
        that SQLite rejected.

        Returns:
            True when a still-open transaction was re-armed and the COMMIT
            can be retried; False for any other state, which tells
            ``commit()`` to raise.

        """
        transaction = self._transaction
        if transaction is None:
            return False
        if transaction._state is not SessionTransactionState.PREPARED:  # noqa: SLF001 — commit-phase check has no public equivalent
            return False

        rearmed = False
        for _conn, root_transaction, should_commit, _autoclose in set(
            transaction._connections.values()  # noqa: SLF001 — the connections SQLAlchemy still holds open
        ):
            if (
                should_commit
                and isinstance(root_transaction, RootTransaction)
                and not root_transaction.is_active
                and self._transaction_still_open(root_transaction)
            ):
                root_transaction.is_active = True
                rearmed = True
        return rearmed

    @staticmethod
    def _transaction_still_open(root_transaction: RootTransaction) -> bool:
        """Report whether SQLite is still holding this transaction open.

        Asks two independent sources rather than assuming, because a
        SQLITE_BUSY that rolled the transaction back is indistinguishable
        from one that did not by the raised error alone:

        - ``connection._transaction``: SQLAlchemy clears it only once a
          COMMIT has completed ("only remove as the connection's current
          transaction if commit succeeded", ``engine/base.py``). While it
          still points here, this transaction's COMMIT is the one that
          failed, and the transaction has not been committed out from under
          the retry.
        - ``dbapi_connection.in_transaction``: pysqlite's view of
          ``sqlite3_get_autocommit``. False means SQLite took the rollback
          branch and discarded the writes, so re-issuing ``commit()`` would
          be a no-op that succeeds on nothing.

        Args:
            root_transaction: The transaction whose COMMIT raised SQLITE_BUSY.

        Returns:
            True only when both sources agree the transaction is still open.

        """
        connection = root_transaction.connection
        if connection._transaction is not root_transaction:  # noqa: SLF001 — SQLAlchemy's own completed-commit marker
            return False

        dbapi_connection = connection.connection.dbapi_connection
        if dbapi_connection is None:  # pragma: no cover — a live COMMIT always has one
            return False
        return bool(dbapi_connection.in_transaction)

    @staticmethod
    def _retry_delay(delay: float) -> None:
        """Sleep during retry backoff.

        Always uses blocking sleep since SQLAlchemy commit() is synchronous.
        Callers in async contexts should wrap the entire commit call in
        asyncio.to_thread() to avoid blocking the event loop.
        """
        time.sleep(delay)
