# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: update_file rejects unknown fields and accepts datetime fields."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

import chaoscypher_core.adapters.sqlite.models as _models  # noqa: F401 — registers all tables
from chaoscypher_core.adapters.sqlite.mixins.source_files import SourceLifecycleMixin
from chaoscypher_core.adapters.sqlite.models import SourceRow


# ---------------------------------------------------------------------------
# Minimal adapter stub that inherits the mixin under test
# ---------------------------------------------------------------------------


class _StubAdapter(SourceLifecycleMixin):
    """Minimal adapter providing session and connection state for tests."""

    def __init__(self, session: Session, database_name: str = "default") -> None:
        self.session = session
        self._connected = True
        self.database_name = database_name

    def _ensure_connected(self) -> None:
        """No-op: always connected in tests."""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

DB_NAME = "default"


@pytest.fixture
def adapter(tmp_path: pytest.TempPathFactory) -> _StubAdapter:  # type: ignore[type-arg]
    """Create a file-backed SQLite adapter with SourceRow seeded."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        row = SourceRow(
            id="src_1",
            database_name=DB_NAME,
            filename="doc.pdf",
            filepath="/tmp/doc.pdf",
            file_type="pdf",
            file_size=10,
            title="doc.pdf",
            source_type="pdf",
            status="pending",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(row)
        session.commit()
        yield _StubAdapter(session, database_name=DB_NAME)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_unknown_field_raises(adapter: _StubAdapter) -> None:
    """Unknown field names must raise ValueError immediately (not silently no-op)."""
    with pytest.raises(ValueError, match="unknown field"):
        adapter.update_file("src_1", database_name=DB_NAME, updates={"definitely_not_a_field": 42})


def test_datetime_field_now_writes(adapter: _StubAdapter) -> None:
    """Previously the skip list silently dropped datetime writes. Now they apply."""
    ts = datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)
    adapter.update_file("src_1", database_name=DB_NAME, updates={"indexing_started_at": ts})
    refreshed = adapter.get_file("src_1", DB_NAME)
    assert refreshed is not None
    assert refreshed["indexing_started_at"] is not None


def test_immutable_fields_silently_skipped(adapter: _StubAdapter) -> None:
    """Immutable fields (id, database_name) are silently dropped (not raised)."""
    adapter.update_file(
        "src_1", database_name=DB_NAME, updates={"id": "src_evil", "status": "indexed"}
    )
    refreshed = adapter.get_file("src_1", DB_NAME)
    assert refreshed is not None
    assert refreshed["id"] == "src_1"  # not changed
    assert refreshed["status"] == "indexed"


def test_status_field_writes_normally(adapter: _StubAdapter) -> None:
    """Sanity check: ordinary string field updates still work."""
    adapter.update_file("src_1", database_name=DB_NAME, updates={"status": "indexed"})
    refreshed = adapter.get_file("src_1", DB_NAME)
    assert refreshed is not None
    assert refreshed["status"] == "indexed"


def test_missing_source_raises_not_found(adapter: _StubAdapter) -> None:
    """Missing source raises NotFoundError (changed from silent no-op; audit fix H2)."""
    from chaoscypher_core.exceptions import NotFoundError

    with pytest.raises(NotFoundError):
        adapter.update_file(
            "nonexistent_source", database_name=DB_NAME, updates={"status": "indexed"}
        )
