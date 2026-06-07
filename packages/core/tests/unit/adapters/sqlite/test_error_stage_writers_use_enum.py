# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: every error_stage writer must persist the SourceErrorStage enum value.

Audit fix — locks the wire format so future writers cannot drift from the
enum definition. Today's literal strings already equal the enum values, so
these tests pass by coincidence; they exist to break loudly if a future
commit changes a literal or renames an enum member without updating all sites.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

import chaoscypher_core.adapters.sqlite.models as _models  # noqa: F401 — registers all tables
from chaoscypher_core.adapters.sqlite.mixins.source_files import SourceLifecycleMixin
from chaoscypher_core.adapters.sqlite.mixins.source_files_indexing import SourceIndexingMixin
from chaoscypher_core.adapters.sqlite.models import SourceRow
from chaoscypher_core.models import SourceErrorStage, SourceStatus


# ---------------------------------------------------------------------------
# Minimal combined stub adapter
# ---------------------------------------------------------------------------


class _StubAdapter(SourceIndexingMixin, SourceLifecycleMixin):
    """Minimal adapter stub that wires both mixins under test."""

    def __init__(self, session: Session, database_name: str = "default") -> None:
        self.session = session
        self._connected = True
        self.database_name = database_name

    def _ensure_connected(self) -> None:
        if not self._connected:
            msg = "Not connected"
            raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

DB_NAME = "default"


@pytest.fixture
def adapter(tmp_path: pytest.TempPathFactory) -> _StubAdapter:
    """Create a file-backed SQLite stub adapter with all tables created."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield _StubAdapter(session, database_name=DB_NAME)


def _seed_source(
    adapter: _StubAdapter,
    source_id: str,
    status: str,
) -> None:
    """Insert a minimal SourceRow with the given status and commit it."""
    row = SourceRow(
        id=source_id,
        database_name=DB_NAME,
        filename="test.txt",
        filepath=f"/data/{source_id}/test.txt",
        status=status,
    )
    adapter.session.add(row)
    adapter.session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_fail_indexing_persists_enum_value(adapter: _StubAdapter) -> None:
    """fail_indexing writes SourceErrorStage.INDEXING.value to error_stage."""
    source_id = "src_indexing"
    _seed_source(adapter, source_id, SourceStatus.INDEXING)

    adapter.fail_indexing(source_id=source_id, error="index error")

    adapter.session.expire_all()
    row = adapter.session.get(SourceRow, source_id)
    assert row is not None
    assert row.error_stage == SourceErrorStage.INDEXING.value


def test_fail_extraction_persists_enum_value(adapter: _StubAdapter) -> None:
    """fail_extraction writes SourceErrorStage.EXTRACTION.value to error_stage."""
    source_id = "src_extraction"
    _seed_source(adapter, source_id, SourceStatus.EXTRACTING)

    adapter.fail_extraction(source_id=source_id, error="extraction error")

    adapter.session.expire_all()
    row = adapter.session.get(SourceRow, source_id)
    assert row is not None
    assert row.error_stage == SourceErrorStage.EXTRACTION.value


def test_fail_commit_persists_enum_value(adapter: _StubAdapter) -> None:
    """fail_commit writes SourceErrorStage.COMMIT.value to error_stage."""
    source_id = "src_commit"
    _seed_source(adapter, source_id, SourceStatus.COMMITTING)

    adapter.fail_commit(source_id=source_id, error="commit error")

    adapter.session.expire_all()
    row = adapter.session.get(SourceRow, source_id)
    assert row is not None
    assert row.error_stage == SourceErrorStage.COMMIT.value


def test_mark_source_exhausted_persists_enum_value(adapter: _StubAdapter) -> None:
    """mark_source_exhausted writes SourceErrorStage.RECOVERY_EXHAUSTED.value."""
    source_id = "src_exhausted"
    _seed_source(adapter, source_id, SourceStatus.EXTRACTING)

    adapter.mark_source_exhausted(
        source_id=source_id,
        database_name=DB_NAME,
        error_message="exceeded max recovery attempts",
    )

    adapter.session.expire_all()
    row = adapter.session.get(SourceRow, source_id)
    assert row is not None
    assert row.error_stage == SourceErrorStage.RECOVERY_EXHAUSTED.value


def test_fail_url_fetch_persists_enum_value(adapter: _StubAdapter) -> None:
    """fail_url_fetch writes SourceErrorStage.URL_FETCH.value to error_stage."""
    source_id = "src_url_fetch"
    # Seed a URL placeholder row directly — create_url_placeholder requires no file I/O
    row = SourceRow(
        id=source_id,
        database_name=DB_NAME,
        filename="https://example.com/doc",
        filepath="",
        status=SourceStatus.PENDING,
        source_type="webpage",
        origin_url="https://example.com/doc",
        step_description="Fetching URL",
        current_step=1,
        total_steps=2,
    )
    adapter.session.add(row)
    adapter.session.commit()

    adapter.fail_url_fetch(source_id=source_id, error="HTTP 404", database_name=DB_NAME)

    adapter.session.expire_all()
    fetched = adapter.session.get(SourceRow, source_id)
    assert fetched is not None
    assert fetched.error_stage == SourceErrorStage.URL_FETCH.value
