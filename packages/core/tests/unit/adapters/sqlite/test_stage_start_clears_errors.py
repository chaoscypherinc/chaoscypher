# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: start_indexing/extraction/commit must clear stale error fields."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

import chaoscypher_core.adapters.sqlite.models as _models  # noqa: F401 — registers all tables
from chaoscypher_core.adapters.sqlite.mixins.source_files_indexing import (
    SourceIndexingMixin,
)
from chaoscypher_core.adapters.sqlite.models import SourceRow
from chaoscypher_core.models import SourceStatus


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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

DB_NAME = "test_db"


@pytest.fixture
def adapter(tmp_path: pytest.TempPathFactory) -> _StubAdapter:
    """Create a file-backed SQLite adapter with SourceRow table."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield _StubAdapter(session, database_name=DB_NAME)


def _make_errored_source(adapter: _StubAdapter, source_id: str = "src_e") -> str:
    """Seed an ERROR-status source with stale error fields."""
    source = SourceRow(
        id=source_id,
        database_name=DB_NAME,
        filename="e.txt",
        filepath="/tmp/e.txt",
        status=SourceStatus.ERROR,
        error_message="old error",
        error_stage="indexing",
    )
    adapter.session.add(source)
    adapter.session.commit()
    return source_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_start_indexing_clears_stale_error(adapter: _StubAdapter) -> None:
    """start_indexing must clear error_message and error_stage."""
    sid = _make_errored_source(adapter)
    adapter.start_indexing(sid)

    adapter.session.expire_all()
    row = adapter.session.get(SourceRow, sid)
    assert row is not None
    assert row.status == SourceStatus.INDEXING
    assert row.error_message is None
    assert row.error_stage is None


def test_start_indexing_does_not_clear_recovery_attempts(adapter: _StubAdapter) -> None:
    """start_indexing must NOT touch recovery_attempts (intentional state)."""
    source = SourceRow(
        id="src_r",
        database_name=DB_NAME,
        filename="r.txt",
        filepath="/tmp/r.txt",
        status=SourceStatus.ERROR,
        error_message="boom",
        error_stage="indexing",
        recovery_attempts=3,
    )
    adapter.session.add(source)
    adapter.session.commit()

    adapter.start_indexing("src_r")

    adapter.session.expire_all()
    row = adapter.session.get(SourceRow, "src_r")
    assert row is not None
    assert row.recovery_attempts == 3


def test_start_extraction_clears_stale_error(adapter: _StubAdapter) -> None:
    """start_extraction must clear error_message and error_stage."""
    sid = _make_errored_source(adapter)
    adapter.start_extraction(sid)

    adapter.session.expire_all()
    row = adapter.session.get(SourceRow, sid)
    assert row is not None
    assert row.status == SourceStatus.EXTRACTING
    assert row.error_message is None
    assert row.error_stage is None


def test_start_extraction_does_not_clear_recovery_attempts(adapter: _StubAdapter) -> None:
    """start_extraction must NOT touch recovery_attempts (intentional state)."""
    source = SourceRow(
        id="src_r2",
        database_name=DB_NAME,
        filename="r2.txt",
        filepath="/tmp/r2.txt",
        status=SourceStatus.ERROR,
        error_message="boom",
        error_stage="extraction",
        recovery_attempts=5,
    )
    adapter.session.add(source)
    adapter.session.commit()

    adapter.start_extraction("src_r2")

    adapter.session.expire_all()
    row = adapter.session.get(SourceRow, "src_r2")
    assert row is not None
    assert row.recovery_attempts == 5


def test_start_commit_clears_stale_error(adapter: _StubAdapter) -> None:
    """start_commit must clear error_message and error_stage."""
    sid = _make_errored_source(adapter)
    adapter.start_commit(sid)

    adapter.session.expire_all()
    row = adapter.session.get(SourceRow, sid)
    assert row is not None
    assert row.status == SourceStatus.COMMITTING
    assert row.error_message is None
    assert row.error_stage is None


def test_start_commit_does_not_clear_recovery_attempts(adapter: _StubAdapter) -> None:
    """start_commit must NOT touch recovery_attempts (intentional state)."""
    source = SourceRow(
        id="src_r3",
        database_name=DB_NAME,
        filename="r3.txt",
        filepath="/tmp/r3.txt",
        status=SourceStatus.ERROR,
        error_message="boom",
        error_stage="commit",
        recovery_attempts=7,
    )
    adapter.session.add(source)
    adapter.session.commit()

    adapter.start_commit("src_r3")

    adapter.session.expire_all()
    row = adapter.session.get(SourceRow, "src_r3")
    assert row is not None
    assert row.recovery_attempts == 7
