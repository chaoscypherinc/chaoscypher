# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for SourceIndexingMixin — reset_for_retry and related helpers."""

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

import chaoscypher_core.adapters.sqlite.models as _models  # noqa: F401 — registers all tables
from chaoscypher_core.adapters.sqlite.mixins.source_files_indexing import (
    SourceIndexingMixin,
)
from chaoscypher_core.adapters.sqlite.models import SourceRow


# ---------------------------------------------------------------------------
# Minimal adapter stub that inherits the mixin under test
# ---------------------------------------------------------------------------


class _StubAdapter(SourceIndexingMixin):
    """Minimal adapter providing session and connection state for tests."""

    def __init__(self, session: Session, database_name: str = "test_db") -> None:
        self.session = session
        self._connected = True
        self.database_name = database_name

    def _ensure_connected(self) -> None:
        """Raise if not connected."""
        if not self._connected:
            msg = "Not connected"
            raise RuntimeError(msg)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Stub transaction context: commit on clean exit, rollback on exception.

        Real ``SqliteAdapter.transaction`` (adapter.py:215) coordinates depth
        with SafeSession. The stub mirrors the outer commit/rollback semantics
        without depth tracking — sufficient for tests that exercise mixin code
        which calls ``self.transaction()``.
        """
        try:
            yield
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    def cancel_extraction_job_cascade(self, job_id: str) -> int:
        """Stub: no orphan-job state is seeded by these tests, so this is a no-op.

        The real adapter (in source_files_extraction_jobs.py) cancels the job
        and its non-terminal tasks. These TestResetForRetry tests pre-date F53
        and don't exercise the orphan-job branch — current_extraction_job_id
        is always None on the seeded SourceRow, so reset_for_retry's guard
        skips this call. Stub is here to satisfy mixin composition only.
        """
        del job_id
        return 0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

DB_NAME = "test_db"


@pytest.fixture
def adapter(tmp_path) -> _StubAdapter:
    """Create a file-backed SQLite adapter with SourceRow table."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield _StubAdapter(session, database_name=DB_NAME)


def _make_source(
    source_id: str,
    status: str = "error",
    error_stage: str | None = "commit",
    error_message: str | None = "something went wrong",
    recovery_attempts: int = 2,
) -> SourceRow:
    """Return a minimal SourceRow for seeding tests."""
    return SourceRow(
        id=source_id,
        database_name=DB_NAME,
        filename="doc.pdf",
        filepath="/tmp/doc.pdf",
        status=status,
        error_stage=error_stage,
        error_message=error_message,
        recovery_attempts=recovery_attempts,
    )


# ---------------------------------------------------------------------------
# reset_for_retry tests
# ---------------------------------------------------------------------------


class TestResetForRetry:
    """Tests for SourceIndexingMixin.reset_for_retry."""

    def test_resets_errored_source(self, adapter: _StubAdapter) -> None:
        """reset_for_retry transitions an errored source to the target status."""
        source = _make_source("src-ok", status="error", error_stage="commit")
        adapter.session.add(source)
        adapter.session.commit()

        adapter.reset_for_retry(
            source_id="src-ok",
            database_name=DB_NAME,
            new_status="extracted",
        )

        adapter.session.expire_all()
        row = adapter.session.get(SourceRow, "src-ok")
        assert row is not None
        assert row.status == "extracted"
        assert row.error_message is None
        assert row.error_stage is None
        assert row.recovery_attempts == 0

    def test_is_noop_for_non_error_source(self, adapter: _StubAdapter) -> None:
        """reset_for_retry only modifies sources currently in 'error' state.

        Regression test for the double-click idempotency guard: if a source has
        already been reset (e.g. by a concurrent retry request), a second call
        must not clobber the in-progress status.
        """
        source = _make_source("src-committed", status="committed", error_stage=None)
        source.error_message = None
        source.recovery_attempts = 0
        adapter.session.add(source)
        adapter.session.commit()

        adapter.reset_for_retry(
            source_id="src-committed",
            database_name=DB_NAME,
            new_status="pending",
        )

        adapter.session.expire_all()
        row = adapter.session.get(SourceRow, "src-committed")
        assert row is not None
        assert row.status == "committed"  # unchanged

    def test_is_noop_for_already_reset_source(self, adapter: _StubAdapter) -> None:
        """Double-click scenario: first retry sets status='extracted', second is no-op."""
        source = _make_source("src-double", status="error", error_stage="commit")
        adapter.session.add(source)
        adapter.session.commit()

        # First call — should succeed
        adapter.reset_for_retry(
            source_id="src-double",
            database_name=DB_NAME,
            new_status="extracted",
        )
        adapter.session.expire_all()
        row = adapter.session.get(SourceRow, "src-double")
        assert row is not None
        assert row.status == "extracted"

        # Second call — source is now 'extracted', not 'error'; should be no-op
        adapter.reset_for_retry(
            source_id="src-double",
            database_name=DB_NAME,
            new_status="pending",
        )
        adapter.session.expire_all()
        row = adapter.session.get(SourceRow, "src-double")
        assert row is not None
        assert row.status == "extracted"  # not overwritten to 'pending'

    def test_is_noop_for_missing_source(self, adapter: _StubAdapter) -> None:
        """reset_for_retry returns cleanly when the source does not exist."""
        # Should not raise — idempotent success for non-existent source
        adapter.reset_for_retry(
            source_id="nonexistent",
            database_name=DB_NAME,
            new_status="pending",
        )

    def test_is_noop_for_wrong_database(self, adapter: _StubAdapter) -> None:
        """reset_for_retry ignores sources in a different database."""
        source = SourceRow(
            id="src-other-db",
            database_name="other_db",
            filename="doc.pdf",
            filepath="/tmp/doc.pdf",
            status="error",
            error_stage="commit",
            recovery_attempts=1,
        )
        adapter.session.add(source)
        adapter.session.commit()

        adapter.reset_for_retry(
            source_id="src-other-db",
            database_name=DB_NAME,  # different from source's database_name
            new_status="extracted",
        )

        adapter.session.expire_all()
        row = adapter.session.get(SourceRow, "src-other-db")
        assert row is not None
        assert row.status == "error"  # unchanged
