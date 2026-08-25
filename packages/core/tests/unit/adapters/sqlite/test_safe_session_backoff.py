# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Lock-retry semantics for ``SafeSession.commit()``.

Regression cover for the retry that silently discarded flushed writes: on
SQLITE_BUSY the old code rolled back, re-added a snapshot of
``new``/``dirty``/``deleted`` and committed again. Inside
``adapter.transaction()`` every write is already flushed by the time the
outer commit runs, so that snapshot was always empty — the rollback threw
the work away and the retry committed nothing while logging
``commit_succeeded_after_retry``.

Every test here drives a real file-backed database through the real
``adapter.transaction()`` boundary, injecting the lock at the exact point
the pysqlite driver raises it (``dialect.do_commit`` for a COMMIT-phase
lock, ``dialect.do_execute`` for a flush-phase one), and reads the result
back through an independent ``sqlite3`` connection so nothing is asserted
against the session's own identity map.

The backoff-multiplier resolution (an explicit value wins; ``None`` falls
back to the ``BackoffSettings`` class default, never the app settings
singleton) is checked through that same real mechanism.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from chaoscypher_core.adapters.sqlite.models import SourceRow
from chaoscypher_core.adapters.sqlite.safe_session import SafeSession
from chaoscypher_core.models import SourceStatus
from chaoscypher_core.settings import BackoffSettings


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


EXISTING_ID = "src_existing"
NEW_ID = "src_new"


def _source(source_id: str, status: SourceStatus) -> SourceRow:
    """Build a minimally-populated ``SourceRow`` for the given id and status."""
    return SourceRow(
        id=source_id,
        database_name="default",
        filename=f"{source_id}.txt",
        filepath=f"/tmp/{source_id}.txt",
        file_type="text",
        status=status,
        created_at=datetime.now(UTC),
    )


def _seed(adapter: SqliteAdapter) -> None:
    """Commit the one pre-existing row the lock scenarios then UPDATE."""
    adapter.session.add(_source(EXISTING_ID, SourceStatus.EXTRACTED))
    adapter.session.commit()


def _statuses_on_disk(adapter: SqliteAdapter) -> dict[str, str]:
    """Read every source row through an INDEPENDENT connection.

    Deliberately not the session under test: only a real durable COMMIT is
    visible to a second connection, so this cannot be satisfied by writes
    that merely sit in the session's identity map or in an open transaction.
    """
    connection = sqlite3.connect(str(adapter.db_path))
    try:
        rows = connection.execute("SELECT id, processing_status FROM sources")
        return {row[0]: row[1] for row in rows}
    finally:
        connection.close()


def _fail_commits(
    adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch, *, times: int
) -> dict[str, int]:
    """Make the first ``times`` COMMITs raise SQLITE_BUSY; count every attempt.

    Patches the dialect hook the pysqlite driver's ``connection.commit()``
    goes through, so the failure lands exactly where a real SQLITE_BUSY on
    COMMIT lands — before the DBAPI commit runs, with the SQLite transaction
    still open and retryable.
    """
    dialect = adapter._engine.dialect  # the driver boundary
    real_do_commit = dialect.do_commit
    calls = {"count": 0}

    def flaky_do_commit(dbapi_connection: Any) -> None:
        """Raise SQLITE_BUSY for the first ``times`` calls, then really commit."""
        calls["count"] += 1
        if calls["count"] <= times:
            raise sqlite3.OperationalError("database is locked")
        real_do_commit(dbapi_connection)

    monkeypatch.setattr(dialect, "do_commit", flaky_do_commit)
    return calls


def _auto_rollback_commits(
    adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch, *, times: int
) -> dict[str, int]:
    """Make the first ``times`` COMMITs roll the transaction back, then raise SQLITE_BUSY.

    SQLite lists SQLITE_BUSY among the errors that MAY roll the *entire*
    transaction back rather than merely failing the statement
    (lang_transaction.html, "Response To Errors Within A Transaction"). The
    driver then reports the busy error on a connection already returned to
    autocommit, where ``connection.commit()`` is a no-op that succeeds — so a
    retry there would report success with the writes gone. Rolling back
    before raising is exactly that documented response, not an invented state.
    """
    dialect = adapter._engine.dialect  # the driver boundary
    real_do_commit = dialect.do_commit
    calls = {"count": 0}

    def rolling_back_do_commit(dbapi_connection: Any) -> None:
        """Discard the transaction the way SQLite may, then report the busy error."""
        calls["count"] += 1
        if calls["count"] <= times:
            dbapi_connection.rollback()
            raise sqlite3.OperationalError("database is locked")
        real_do_commit(dbapi_connection)

    monkeypatch.setattr(dialect, "do_commit", rolling_back_do_commit)
    return calls


def _record_delays(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replace the retry sleep with a recorder and return the delay log."""
    delays: list[float] = []
    monkeypatch.setattr(SafeSession, "_retry_delay", staticmethod(delays.append))
    return delays


def _write_update_and_insert(adapter: SqliteAdapter) -> None:
    """Apply the repro's UPDATE + INSERT and flush them at transaction depth 1."""
    existing = adapter.session.get(SourceRow, EXISTING_ID)
    assert existing is not None
    existing.status = SourceStatus.COMMITTED
    adapter.session.add(_source(NEW_ID, SourceStatus.EXTRACTED))
    adapter._maybe_commit()  # the flush every repository write performs
    assert adapter.session._transaction_depth == 1  # repro precondition


def test_flushed_writes_survive_a_locked_commit(
    sqlite_adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A busy COMMIT is retried on the open transaction; both writes land."""
    _seed(sqlite_adapter)
    commits = _fail_commits(sqlite_adapter, monkeypatch, times=1)
    _record_delays(monkeypatch)

    with sqlite_adapter.transaction():
        _write_update_and_insert(sqlite_adapter)

    assert _statuses_on_disk(sqlite_adapter) == {
        EXISTING_ID: SourceStatus.COMMITTED,
        NEW_ID: SourceStatus.EXTRACTED,
    }, (
        "the flushed UPDATE and INSERT must survive a SQLITE_BUSY on the first "
        "COMMIT — rolling back to retry discards them and commits nothing"
    )
    assert commits["count"] == 2, (
        "the retry must re-issue a real COMMIT on the still-open transaction, "
        f"not commit an empty one; driver-level COMMIT attempts: {commits['count']}"
    )


def test_auto_rollback_during_busy_commit_raises_instead_of_reporting_success(
    sqlite_adapter: SqliteAdapter,
    monkeypatch: pytest.MonkeyPatch,
    structlog_for_caplog: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A busy COMMIT that took the transaction down with it is not retryable.

    SQLITE_BUSY may roll the whole transaction back rather than just fail the
    COMMIT. The connection is then in autocommit, where ``connection.commit()``
    is a no-op that succeeds — re-arming there would report success on writes
    SQLite has already thrown away, which is the very bug this module fixes.
    ``_rearm_busy_commit()`` must detect the closed transaction and decline.
    """
    _seed(sqlite_adapter)
    commits = _auto_rollback_commits(sqlite_adapter, monkeypatch, times=1)
    delays = _record_delays(monkeypatch)

    with pytest.raises(OperationalError, match="database is locked"), sqlite_adapter.transaction():
        _write_update_and_insert(sqlite_adapter)

    assert commits["count"] == 1, (
        "the COMMIT must not be re-issued once SQLite has discarded the "
        f"transaction; driver-level COMMIT attempts: {commits['count']}"
    )
    assert delays == [], "there is nothing left to retry, so no backoff sleep should happen"
    assert _statuses_on_disk(sqlite_adapter) == {EXISTING_ID: SourceStatus.EXTRACTED}, (
        "the rolled-back UPDATE and INSERT must not appear on disk"
    )
    assert "commit_succeeded_after_retry" not in caplog.text, (
        "a no-op commit on an autocommit connection must never be reported as a "
        "successful retry — the writes are gone"
    )


def test_exhausted_retry_budget_raises_instead_of_reporting_success(
    sqlite_adapter: SqliteAdapter,
    monkeypatch: pytest.MonkeyPatch,
    structlog_for_caplog: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A lock that never clears surfaces as OperationalError, never as success."""
    _seed(sqlite_adapter)
    commits = _fail_commits(sqlite_adapter, monkeypatch, times=99)
    delays = _record_delays(monkeypatch)
    max_attempts = sqlite_adapter.session._max_attempts  # the budget under test

    with pytest.raises(OperationalError, match="database is locked"), sqlite_adapter.transaction():
        _write_update_and_insert(sqlite_adapter)

    assert commits["count"] == max_attempts, (
        f"every one of the {max_attempts} attempts must reach the driver; "
        f"observed {commits['count']}"
    )
    assert len(delays) == max_attempts - 1, (
        f"expected {max_attempts - 1} backoff sleeps between attempts, got {delays!r}"
    )
    assert _statuses_on_disk(sqlite_adapter) == {EXISTING_ID: SourceStatus.EXTRACTED}, (
        "a commit that never succeeded must leave the database untouched"
    )
    assert "commit_succeeded_after_retry" not in caplog.text, (
        "success must never be logged for a commit whose writes were lost"
    )


def test_flush_phase_lock_raises_instead_of_committing_nothing(
    sqlite_adapter: SqliteAdapter,
    monkeypatch: pytest.MonkeyPatch,
    structlog_for_caplog: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A lock during the flush is not retryable — SQLAlchemy has already dropped the work.

    The failing INSERT deactivates the session transaction and expunges the
    pending objects, so there is nothing left to re-commit. Retrying there is
    exactly the silent-success bug in a second disguise, so ``commit()`` must
    raise instead.
    """
    _seed(sqlite_adapter)
    dialect = sqlite_adapter._engine.dialect  # the driver boundary
    real_do_execute = dialect.do_execute
    inserts = {"count": 0}

    def flaky_do_execute(cursor: Any, statement: str, parameters: Any, context: Any) -> None:
        """Raise SQLITE_BUSY on the first INSERT into ``sources``, then behave."""
        if statement.strip().upper().startswith("INSERT INTO SOURCES"):
            inserts["count"] += 1
            if inserts["count"] == 1:
                raise sqlite3.OperationalError("database is locked")
        real_do_execute(cursor, statement, parameters, context)

    monkeypatch.setattr(dialect, "do_execute", flaky_do_execute)
    delays = _record_delays(monkeypatch)

    sqlite_adapter.session.add(_source(NEW_ID, SourceStatus.EXTRACTED))
    with pytest.raises(OperationalError, match="database is locked"):
        sqlite_adapter._maybe_commit()  # depth 0, so this commits

    assert delays == [], "a flush-phase lock has nothing to retry, so it must not back off"
    assert _statuses_on_disk(sqlite_adapter) == {EXISTING_ID: SourceStatus.EXTRACTED}, (
        "the failed INSERT must not appear on disk"
    )
    assert "commit_succeeded_after_retry" not in caplog.text


def test_explicit_multiplier_drives_the_retry_delays(
    sqlite_adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit ``backoff_multiplier`` sets the exponential growth of the delays."""
    _seed(sqlite_adapter)
    _fail_commits(sqlite_adapter, monkeypatch, times=2)
    delays = _record_delays(monkeypatch)

    session = SafeSession(
        sqlite_adapter._engine,  # same engine, independent retry knobs
        max_attempts=3,
        base_delay=2.0,
        backoff_multiplier=3.0,
    )
    try:
        session.add(_source(NEW_ID, SourceStatus.EXTRACTED))
        session.flush()  # the state adapter.transaction() leaves before its commit
        session.commit()
    finally:
        session.close()

    assert delays == [2.0, 6.0], f"base_delay 2.0 * 3**attempt expected, got {delays!r}"
    assert NEW_ID in _statuses_on_disk(sqlite_adapter)


def test_none_multiplier_uses_class_default_not_singleton(
    sqlite_adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``backoff_multiplier=None`` resolves to the class default, not the app singleton."""
    _seed(sqlite_adapter)
    _fail_commits(sqlite_adapter, monkeypatch, times=2)
    delays = _record_delays(monkeypatch)

    poisoned = MagicMock()
    poisoned.backoff.exponential_multiplier = 9.0

    session = SafeSession(
        sqlite_adapter._engine,  # same engine, independent retry knobs
        max_attempts=3,
        base_delay=2.0,
    )
    try:
        session.add(_source(NEW_ID, SourceStatus.EXTRACTED))
        session.flush()
        with patch("chaoscypher_core.app_config.get_settings", return_value=poisoned):
            session.commit()
    finally:
        session.close()

    default_multiplier = BackoffSettings().exponential_multiplier
    assert default_multiplier != 9.0, "test is vacuous if the class default matches the singleton"
    assert delays == [2.0, 2.0 * default_multiplier], (
        f"expected the {default_multiplier} class default to drive the growth, got {delays!r}"
    )
